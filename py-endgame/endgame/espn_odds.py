import json
from datetime import date, timedelta
from logging import getLogger
from typing import AsyncIterator, Dict, List, Optional, Tuple, TypedDict

import aiohttp

from .date import chunk_date_range, format_dates_param
from .web import RequestParameters, get

logger = getLogger(__name__)


# How many events ESPN will hand back in one scoreboard response.
#
# It silently truncates rather than paging or erroring: a 7-day NCAABB range
# asked for with limit=300 comes back with exactly 300 events, the last day
# cut from 47 games to 4, and nothing in the response says so. So the limit
# is asked for as high as it goes and `_get_odds_span` treats a full
# response as truncated.
#
# Above roughly 1000 ESPN stops honouring the parameter altogether and falls
# back to its own default of 25, which would be a much quieter way to lose
# most of a season. Measured on a 14-day NCAABB range: 900 and 1000 both
# return the real 670, 1200 and up return 25. Hence 1000 and not more.
ODDS_PAGE_LIMIT = 1000

# How many days a single odds request covers before it's split up.
#
# Sized for the densest league we ask about: NCAABB runs ~50 D1 games a day,
# so 14 days is ~670 events, comfortably under the cap. Leagues that play
# less can raise it and spend fewer requests -- see the `odds_chunk_days`
# on `DailyLeague` and the per-league constants in `nfl` and `ncaafb`.
DEFAULT_ODDS_CHUNK_DAYS = 14


class _RangeRefused(Exception):
    """
    ESPN answered a `dates=start-end` request with 400 Bad Request.

    It started doing so on 2026-09-16, for every league and any span down
    to two days, after accepting ranges for as long as this module has
    sent them; a single day still works. Whether that is a policy or an
    outage nobody can say, so the range is still the first thing asked
    for, and this is how `get_odds_range` learns to stop asking.
    """


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
    try:
        content = await get(url, parameters)
    except aiohttp.ClientResponseError as error:
        if error.status == 400 and "-" in str((parameters or {}).get("dates", "")):
            raise _RangeRefused() from error
        raise
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


async def _get_odds_span(
    url: str,
    base_parameters: Dict,
    start: date,
    end: date,
) -> AsyncIterator[Odds]:
    """
    One span's odds, split in half and re-asked if ESPN truncated it.

    `chunk_days` already keeps a span well under the cap for the league it
    was sized for, so this is the backstop for the days that aren't
    ordinary: a conference tournament, the first Saturday of March. Halving
    rather than failing means an unexpectedly busy stretch costs a couple of
    extra requests instead of silently dropping games.
    """
    parameters = dict(base_parameters)
    parameters["dates"] = format_dates_param(start, end)
    parameters["limit"] = ODDS_PAGE_LIMIT

    n_events, odds = await _get_odds_page(url, parameters)
    if n_events < ODDS_PAGE_LIMIT:
        for odd in odds:
            yield odd
        return

    if start == end:
        # Nothing left to split. One day with 1000+ events is beyond
        # anything either sport has ever played, so this is much more
        # likely to be ESPN changing its cap than a real schedule.
        logger.warning(
            "%s returned a full %d events for the single day %s -- "
            "odds for that day are probably incomplete",
            url,
            ODDS_PAGE_LIMIT,
            start,
        )
        for odd in odds:
            yield odd
        return

    # The halves have to be disjoint: a midpoint that ends one and starts
    # the other would report that day's games twice, and these are appended
    # to a snapshot rather than keyed by game.
    midpoint = start + timedelta(days=(end - start).days // 2)
    logger.info(
        "%s truncated %s..%s at %d events, splitting at %s",
        url,
        start,
        end,
        ODDS_PAGE_LIMIT,
        midpoint,
    )
    async for odd in _get_odds_span(url, base_parameters, start, midpoint):
        yield odd
    async for odd in _get_odds_span(
        url, base_parameters, midpoint + timedelta(days=1), end
    ):
        yield odd


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

    ESPN's scoreboard takes a range of days as well as a single one, so a
    fortnight of fixtures is one request rather than fourteen. That's the
    whole reason odds can be pulled ahead at all: looking a week further out
    costs nothing extra to ask for, where walking it a day at a time cost a
    request per day and made a wide horizon too expensive to run hourly.

    Long stretches are still split -- `chunk_days` at a time, and further if
    a chunk comes back at the cap -- because a range that overflows one
    response is truncated without saying so.

    When ESPN refuses a range outright (`_RangeRefused`) the rest of the
    pull is asked for a day at a time. That is the request count this was
    built to avoid, but a fortnight's `near` horizon is fourteen requests
    once a day and a season's is a few hundred once a week, which is
    affordable; what is not affordable is the alternative, which was every
    horizon wider than a day failing for two days straight. One refusal is
    enough to switch: a 400 on a range is deterministic, and `web.get`
    already spends thirty seconds of retries finding that out.
    """
    base = dict(base_parameters or {})
    ranges = True
    for chunk_start, chunk_end in chunk_date_range(start, end, chunk_days):
        if ranges and chunk_start != chunk_end:
            try:
                async for odd in _get_odds_span(url, base, chunk_start, chunk_end):
                    yield odd
                continue
            except _RangeRefused:
                logger.warning(
                    "%s refused the date range %s..%s; asking a day at a time "
                    "for the rest of this pull",
                    url,
                    chunk_start,
                    chunk_end,
                )
                ranges = False
        for day, _ in chunk_date_range(chunk_start, chunk_end, 1):
            async for odd in _get_odds_span(url, base, day, day):
                yield odd
