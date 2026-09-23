import json
from datetime import date, timedelta
from logging import getLogger
from typing import AsyncIterator, Dict, List, NamedTuple, Optional, TypedDict

from .async_tools import apply_in_parallel
from .date import date_range
from .web import RequestParameters, get

logger = getLogger(__name__)


# How many events ESPN will hand back in one scoreboard response.
#
# It silently truncates rather than paging or erroring: a 7-day NCAABB range
# asked for with limit=300 came back with exactly 300 events, the last day
# cut from 47 games to 4, and nothing in the response said so. A request is
# one day now, which no schedule comes close to filling, but the limit is
# still asked for high and a full response still raised on -- that is the
# only signal there is that a response was cut.
#
# Ask for too much and ESPN stops honouring the parameter at all, falling
# back to its own default of 25. This used to be 1000, on a measurement
# taken over a 14-day NCAABB range, where 900 and 1000 both returned the
# real 670. That threshold does not hold for a single day: asking
# college-football, groups=80, dates=20260912 -- a Saturday with 80 real
# FBS events -- returns all 80 at limit<=500 and exactly 25 at limit>=600.
#
# Since the day-at-a-time change every request is that shape, so the old
# 1000 silently cut every football Saturday from 80 games to 25. 300 is
# inside the honoured band on both measurements and still four times any
# day either sport has played.
ODDS_PAGE_LIMIT = 300

# What ESPN serves when it decides to ignore `limit` entirely. A response of
# exactly this size is therefore ambiguous -- it is either a real 25-event
# day or the parameter being dropped on the floor -- and `_get_odds_day`
# spends one extra request to tell the two apart rather than guessing.
ESPN_DEFAULT_LIMIT = 25


# How many events a range has to carry before "none of them were priced"
# counts as evidence of anything. A stray exhibition or an all-star day can
# come back listed and unpriced, and failing a scheduled pull over two
# events would be noise; a whole range of them is a different claim.
#
# Only events that haven't kicked off count towards it. ESPN drops a game's
# price at kickoff, so a game day's scoreboard is full of listed, unpriced
# events by the evening, and a pull that runs after the last kickoff sees
# every event unpriced for the most ordinary reason there is.
MIN_EVENTS_TO_EXPECT_A_PRICE = 10


class OddsProblem(Exception):
    """A pull came back wrong in a way the response itself doesn't admit to.

    A base so a caller can catch the class -- these are all "the data is not
    what it claims to be", and a scheduled job wants to stop on any of them
    rather than enumerate the ones known so far.
    """


class NoPricesFound(OddsProblem):
    """A range listed games and not one of them carried a price.

    The failure a schema change looks like. `_get_odds_page` reads prices out
    of `competition["odds"]` and skips an event that has none, so if ESPN
    renames that key or moves it, every event is silently "unpriced" and the
    pull writes an empty snapshot -- which is indistinguishable from an
    off-season day, and is exactly how a rename would go unnoticed for a
    season. A range that saw real events and priced none of them is the
    cheapest place to catch it: no second request, no second source, just
    the two numbers the parse already has.

    Games that have kicked off are left out of the count. ESPN takes the
    price down at kickoff, so by 9pm on an NFL Sunday the scoreboard lists
    fourteen events and prices none of them, and that is the day going as
    planned, not the schema moving. An unstarted game with no price is the
    claim worth raising over.

    So are preseason games. Books don't price them, and ESPN lists a full
    slate of them unpriced for the fortnight before every NHL and NFL
    season -- ten on an NHL night, enough to trip the guard every hour
    until puck drop.
    """


class OddsTruncated(OddsProblem):
    """ESPN gave back less than a day, and said nothing about it.

    Raised rather than logged because the damage is invisible downstream: a
    truncated day is indistinguishable from a quiet one once it is a list of
    prices, so a run that carries on writes a snapshot that looks complete
    and is not. Every caller here is a scheduled pull whose next run is an
    hour away -- failing it is cheap, and a missing snapshot is a far louder
    signal than a short one.
    """


# How many of a range's days are in flight at once.
#
# Days are independent requests now, so this is a politeness limit on ESPN
# rather than the size of anything. `apply_in_parallel`'s own default.
ODDS_MAX_PARALLEL_DAYS = 10

# Retained only so the callers that still pass `chunk_days` keep type-
# checking; nothing reads it. See `get_odds_range` for why.
DEFAULT_ODDS_CHUNK_DAYS = 14


class Odds(TypedDict):
    competition_id: str
    # When the game is played, as ESPN's own ISO-8601 UTC string.
    #
    # A snapshot used to be one day's games, so the day was the S3 key and
    # didn't need repeating in the record. Now that a snapshot can span a
    # whole season, the key says when the odds were *read*, and this is the
    # only thing saying which game they're about.
    date: str
    odds: dict


class _OddsPage(NamedTuple):
    """One scoreboard response's parse: what ESPN listed, and the priced ones.

    The counts ride along because the callers need them and the page is the
    only place they exist -- an unpriced event leaves nothing behind in
    `odds` to be counted later.
    """

    # Every event in the response, priced or not. What says whether ESPN
    # truncated: a response is only trustworthy if it came back under
    # `ODDS_PAGE_LIMIT`.
    events: int
    # The events that hadn't kicked off yet, by ESPN's own status, leaving
    # out the preseason. The only ones a missing price says anything about;
    # see `NoPricesFound`.
    unstarted: int
    odds: List[Odds]


def _has_kicked_off(competition: dict) -> bool:
    """
    Whether ESPN says the game is under way or over.

    `status.type.state` is `pre`, `in` or `post`. A competition that doesn't
    say counts as unstarted, so if ESPN moves *that* key the price guard
    gets stricter rather than quieter -- it is the guard against keys moving,
    and shouldn't be disarmed by one.
    """
    state = (competition.get("status") or {}).get("type", {}).get("state")
    return state in ("in", "post")


# ESPN's `season.type` for the preseason; 2 is the regular season, 3 the
# postseason.
PRESEASON = 1


def _is_preseason(event: dict) -> bool:
    """
    Whether ESPN files the event under the preseason.

    Only an explicit preseason excuses a game, so an event that doesn't say
    counts, for the same reason as in `_has_kicked_off`.
    """
    return (event.get("season") or {}).get("type") == PRESEASON


async def _get_odds_page(url: str, parameters: RequestParameters) -> _OddsPage:
    """
    One scoreboard request, parsed.
    """
    content = await get(url, parameters)
    tree = json.loads(content.data)
    events = tree.get("events") or []
    unstarted = 0
    odds = []
    for event in events:
        assert len(event["competitions"]) == 1
        competition = event["competitions"][0]
        if not _has_kicked_off(competition) and not _is_preseason(event):
            unstarted += 1
        event_odds = competition.get("odds")
        if not event_odds:
            continue
        odds.append(
            Odds(
                competition_id=competition["id"],
                date=event["date"],
                odds=event_odds,
            )
        )
    return _OddsPage(len(events), unstarted, odds)


async def get_odds(url: str, parameters: RequestParameters) -> AsyncIterator[Odds]:
    """
    The odds on whatever one scoreboard request comes back with.
    """
    page = await _get_odds_page(url, parameters)
    for odd in page.odds:
        yield odd


async def _get_odds_day(
    url: str,
    base_parameters: Dict,
    day: date,
) -> _OddsPage:
    """
    One day's priced games.

    A full response used to mean "ask for a narrower span", and this used to
    halve the span and recurse. A day is the narrowest request ESPN still
    accepts, so there is nowhere left to split: a full day is a truncation
    nothing here can work around, and it raises.

    Two ways a day comes back short, and neither announces itself:

    * **At the cap.** `n_events == ODDS_PAGE_LIMIT` means ESPN stopped
      counting, and the rest of the day is gone.
    * **At ESPN's own default.** `n_events == ESPN_DEFAULT_LIMIT` means
      either a real 25-event day or `limit` being ignored, and those look
      identical in the response. The tie is broken by asking again for a
      number ESPN is known to honour: if a *smaller* limit returns *more*
      events, the parameter is not being respected at the size we ask for.

    That second check is the one that would have caught the 1000 -> 25
    fallback on the day it started, instead of two seasons of football
    Saturdays quietly arriving at 25 games.
    """
    parameters = dict(base_parameters)
    parameters["dates"] = day.strftime("%Y%m%d")
    parameters["limit"] = ODDS_PAGE_LIMIT

    page = await _get_odds_page(url, parameters)
    if page.events >= ODDS_PAGE_LIMIT:
        raise OddsTruncated(
            f"{url} returned a full {page.events} events for {day} at "
            f"limit={ODDS_PAGE_LIMIT}; the rest of that day was dropped"
        )
    if page.events == ESPN_DEFAULT_LIMIT:
        # One extra request, only on the days that are ambiguous. A real
        # 25-event day answers the same both times and costs nothing but
        # the round trip.
        probe = dict(parameters)
        probe["limit"] = ESPN_DEFAULT_LIMIT * 2
        probe_page = await _get_odds_page(url, probe)
        if probe_page.events > page.events:
            raise OddsTruncated(
                f"{url} returned {page.events} events for {day} at "
                f"limit={ODDS_PAGE_LIMIT} but {probe_page.events} at "
                f"limit={probe['limit']}: ESPN is ignoring the limit and "
                f"serving its default of {ESPN_DEFAULT_LIMIT}"
            )
        # The smaller ask is the honoured one, so prefer what it returned.
        return probe_page
    return page


async def get_odds_range(
    url: str,
    base_parameters: Optional[Dict] = None,
    *,
    start: date,
    end: date,
    chunk_days: int = DEFAULT_ODDS_CHUNK_DAYS,
) -> AsyncIterator[Odds]:
    """
    Every priced game between `start` and `end`, both inclusive.

    ESPN's scoreboard took a range of days until 2026-09-16, when it started
    answering `dates=YYYYMMDD-YYYYMMDD` with a 400 -- any range, forward or
    back, every league. A single `dates=YYYYMMDD` still works, so a range is
    back to a request per day.

    The days go out through `apply_in_parallel` rather than one after the
    other, because a fortnight horizon is fourteen requests now and a whole
    season is a few hundred. Ten at a time turns the season pull back into
    seconds; the horizon ones were never slow enough to notice either way.

    `chunk_days` is ignored. Its callers thread a tuned value through
    (`DailyLeague.odds_chunk_days`, 60 for NHL and WNBA) and there is
    nothing left for it to size, but dropping the parameter means touching
    five call sites in the same change that unbreaks the jobs. It goes in
    the follow-up, with `chunk_date_range` and `format_dates_param`.
    """
    base = dict(base_parameters or {})
    # `date_range` stops before its end; this range includes it.
    days = date_range(start, end + timedelta(days=1))
    seen_events = 0
    seen_unstarted = 0
    seen_priced = 0
    async for day_odds in apply_in_parallel(
        _get_odds_day,
        [(url, base, day) for day in days],
        max_parallel=ODDS_MAX_PARALLEL_DAYS,
    ):
        seen_events += day_odds.events
        seen_unstarted += day_odds.unstarted
        seen_priced += len(day_odds.odds)
        for odd in day_odds.odds:
            yield odd

    # Games listed, none priced: see `NoPricesFound`. Checked over the range
    # rather than per day because a single unpriced day is ordinary -- a
    # season horizon reaches months past where any book has posted -- and a
    # range where nothing at all is priced is not. Only the games that
    # haven't kicked off count as listed: the ones that have are unpriced by
    # design, and a pull after the last kickoff sees nothing else. Nor does
    # the preseason, which no book prices.
    if seen_unstarted >= MIN_EVENTS_TO_EXPECT_A_PRICE and seen_priced == 0:
        raise NoPricesFound(
            f"{url} listed {seen_events} events between {start} and {end}, "
            f"{seen_unstarted} of them not yet kicked off outside the preseason, "
            f"and priced none of "
            f"them; the odds are missing or have moved"
        )
