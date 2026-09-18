import json
from datetime import date, timedelta
from logging import getLogger
from typing import AsyncIterator, Dict, List, Optional, Tuple, TypedDict

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


class OddsTruncated(Exception):
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


async def _get_odds_page(
    url: str, parameters: RequestParameters
) -> Tuple[int, List[Odds]]:
    """
    One scoreboard request, as (how many events came back, the priced ones).

    The count is every event in the response, not just the ones carrying
    odds, because it's what says whether ESPN truncated: a response is only
    trustworthy if it came back under `ODDS_PAGE_LIMIT`.
    """
    content = await get(url, parameters)
    tree = json.loads(content.data)
    events = tree.get("events") or []
    odds = []
    for event in events:
        assert len(event["competitions"]) == 1
        competition = event["competitions"][0]
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
    return len(events), odds


async def get_odds(url: str, parameters: RequestParameters) -> AsyncIterator[Odds]:
    """
    The odds on whatever one scoreboard request comes back with.
    """
    _, odds = await _get_odds_page(url, parameters)
    for odd in odds:
        yield odd


async def _get_odds_day(
    url: str,
    base_parameters: Dict,
    day: date,
) -> List[Odds]:
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

    n_events, odds = await _get_odds_page(url, parameters)
    if n_events >= ODDS_PAGE_LIMIT:
        raise OddsTruncated(
            f"{url} returned a full {n_events} events for {day} at "
            f"limit={ODDS_PAGE_LIMIT}; the rest of that day was dropped"
        )
    if n_events == ESPN_DEFAULT_LIMIT:
        # One extra request, only on the days that are ambiguous. A real
        # 25-event day answers the same both times and costs nothing but
        # the round trip.
        probe = dict(parameters)
        probe["limit"] = ESPN_DEFAULT_LIMIT * 2
        probe_events, probe_odds = await _get_odds_page(url, probe)
        if probe_events > n_events:
            raise OddsTruncated(
                f"{url} returned {n_events} events for {day} at "
                f"limit={ODDS_PAGE_LIMIT} but {probe_events} at "
                f"limit={probe['limit']}: ESPN is ignoring the limit and "
                f"serving its default of {ESPN_DEFAULT_LIMIT}"
            )
        # The smaller ask is the honoured one, so prefer what it returned.
        return probe_odds
    return odds


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
    async for day_odds in apply_in_parallel(
        _get_odds_day,
        [(url, base, day) for day in days],
        max_parallel=ODDS_MAX_PARALLEL_DAYS,
    ):
        for odd in day_odds:
            yield odd
