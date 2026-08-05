from datetime import date, datetime, timedelta
from enum import Enum
from itertools import groupby
from logging import getLogger
from typing import AsyncIterator, Iterable, List, NamedTuple

import aiohttp

from ..async_tools import apply_in_parallel
from ..constants import ESPN_SPORTS_API_BASE
from ..date import get_end_year
from ..espn_games import get_games, save_seasons
from ..espn_odds import Odds, get_odds
from ..season_cache import SeasonCache
from ..types import Game, Season, Week
from ..web import RequestParameters
from .gender import NcaabbGender

logger = getLogger(__name__)


NCAABB_SCOREBOARD = (
    f"{ESPN_SPORTS_API_BASE}/basketball/{{}}-college-basketball/scoreboard"
)
REGULAR_SEASON_START = (11, 1)
REGULAR_SEASON_END = (4, 1)
POST_SEASON_START = (3, 1)
SEASON_END = (4, 30)


class NcaabbGroup(Enum):
    """
    Group in NCAABB is division AND tournament if postseason
    """

    d1 = 50
    # Basketball counts postseason as a different "group"
    ncaa = 100
    nit = 98
    cbi = 55
    cit = 56


POSTSEASON_GROUPS = frozenset(
    [
        NcaabbGroup.ncaa,
        NcaabbGroup.nit,
        NcaabbGroup.cbi,
        NcaabbGroup.cit,
    ]
)


class DayParams(NamedTuple):
    """
    Query parameters for grabbing a day of
    NCAABB games from the ESPN API
    """

    date: date
    gender: NcaabbGender
    group: NcaabbGroup


async def update(gender: NcaabbGender, location=None):
    """
    Update a NCAABB .csv
    """
    if location is None:
        location = f"ncaa{gender.name[0]}bb.csv"

    seasons = await get_seasons(gender)
    save_seasons(seasons, location)


async def get_seasons(gender: NcaabbGender) -> List[Season]:
    """
    Get all seasons for a NCAABB
    """
    end_year = get_end_year(SEASON_END)
    args = [(y, gender) for y in range(2001, end_year + 1)]
    return [s async for s in apply_in_parallel(get_ncaabb_season, args)]



def _last_day_so_far(season_so_far: Season | None) -> date | None:
    if season_so_far is None:
        return None
    # Might get the most recent day's games again unnecessarily.
    # That's fine because we don't know if all the games
    # for that day were done last time this was run.
    last_day_done = max((g.date for w in season_so_far.weeks for g in w.games), None)
    if last_day_done is None:
        return None
    return last_day_done.date()


async def get_ncaabb_season(
    year: int, gender: NcaabbGender, season_so_far: Season | None = None
) -> Season:
    logger.info("Getting NCAABB %s season %d", gender.name, year)
    cache = SeasonCache(f"ncaa{gender.name[0]}bb")
    season = cache.check_cache(year)
    if season:
        return season

    day_params: List[DayParams] = []
    start = _last_day_so_far(season_so_far) or date(year, *REGULAR_SEASON_START)
    end = date(year + 1, *REGULAR_SEASON_END)
    # Don't try to get dates in the future
    end = min(end, date.today())
    for day in _date_range(start, end):
        day_params.append(DayParams(day, gender, NcaabbGroup.d1))
    start = _last_day_so_far(season_so_far) or date(year + 1, *POST_SEASON_START)
    end = date(year + 1, *SEASON_END)
    # Don't try to get dates in the future
    end = min(end, date.today())
    for group in POSTSEASON_GROUPS:
        for day in _date_range(start, end):
            day_params.append(DayParams(day, gender, group))
    # TODO: consider having season_so_far.trouble_params re-checked
    games: List[Game] = []
    trouble_days = []
    for day_param in day_params:
        try:
            games += await get_ncaabb_games(*day_param)
        except aiohttp.client_exceptions.ClientResponseError:
            day, gender, group = day_param
            logger.warning(
                "Marking %s for %s %s as trouble", day, gender.name, group.name
            )
            trouble_days.append(day_param)

    season = Season(group_games_into_weeks(games, year), year, trouble_days)
    if season_so_far:
        season = merge_seasons([season_so_far, season])

    if datetime.utcnow() > datetime(year + 1, *SEASON_END):
        cache.save_to_cache(season)

    return season


def merge_seasons(seasons: List[Season]) -> Season:
    assert all(s.year == seasons[0].year for s in seasons)

    # Pool every game, then rebuild the weeks from their dates. Regrouping
    # rather than merging by week.number means a season saved under the old
    # positional numbering gets repaired the next time it's merged, instead
    # of the bad grouping being carried forward.
    games: dict[str, Game] = {}
    for season in seasons:
        for week in season.weeks:
            for game in week.games:
                # If a game showed up multiple times, keep the latest version
                games[game.game_id] = game

    # Merge trouble params
    trouble_params = set(sum((s.trouble_params or [] for s in seasons), []))

    return Season(
        group_games_into_weeks(games.values(), seasons[0].year),
        seasons[0].year,
        list(trouble_params),
    )


def _week_end(day: date) -> date:
    # The AP poll is released based on Monday-Sunday games, so I'll default
    # to that grouping, keyed by the Monday that follows it.
    return day + timedelta(days=7 - day.weekday())


def _get_week(gametime: datetime) -> date:
    return _week_end(gametime.date())


def _week_number(week_end: date, year: int) -> int:
    """
    Number a week by how far it is from the start of the season.

    Derived from the date rather than from the week's position among the
    games we happen to have, so grouping part of a season gives the same
    numbers as grouping all of it. Numbering positionally is what let an
    incremental pull restart at 1 and merge March games into November's
    week 1.
    """
    return (week_end - _week_end(date(year, *REGULAR_SEASON_START))).days // 7 + 1


def group_games_into_weeks(games: Iterable[Game], year: int) -> List[Week]:
    """
    Group games into Monday-Sunday weeks, numbered from the season's start.
    """
    by_week = groupby(
        sorted(games, key=lambda g: g.date), key=lambda g: _get_week(g.date)
    )
    return [
        Week(list(week_games), _week_number(week_end, year))
        for week_end, week_games in by_week
    ]


def _date_range(start: date, end: date) -> List[date]:
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(days)]


async def get_ncaabb_games(
    game_date: date, gender: NcaabbGender, group: NcaabbGroup
) -> List[Game]:
    logger.info("Getting NCAABB %s %s %s", gender.value, game_date, group.name)
    parameters: RequestParameters = dict(
        lang="en",
        region="us",
        calendartype="blacklist",
        limit=300,
        dates=game_date.strftime("%Y%m%d"),
        groups=group.value,
    )
    games = await get_games(NCAABB_SCOREBOARD.format(gender.name), parameters)
    # Filtering thanks to Montana State Bobcats at Northern Arizona
    # Lumberjacks on 2003-02-28 and a bunch of NCAAWBB games
    return [g for g in games if g.home_score > 0 or g.away_score > 0]


async def _get_ncaabb_odds(
    game_date: date, gender: NcaabbGender, group: NcaabbGroup
) -> AsyncIterator[Odds]:
    logger.info("Getting NCAABB %s %s %s", gender.value, game_date, group.name)
    parameters: RequestParameters = dict(
        lang="en",
        region="us",
        calendartype="blacklist",
        limit=300,
        dates=game_date.strftime("%Y%m%d"),
        groups=group.value,
    )
    odds = get_odds(NCAABB_SCOREBOARD.format(gender.name), parameters)
    async for odd in odds:
        yield odd


def is_between_dates(
    day: date, month_day_start: tuple[int, int], month_day_end: tuple[int, int]
) -> bool:
    """
    Checks if a given date is between a start and end date (inclusive).
    Handles ranges that wrap around the year end (e.g. start > end).
    """
    day_tuple = (day.month, day.day)

    if month_day_start <= month_day_end:
        # Standard range within the same year
        return month_day_start <= day_tuple <= month_day_end
    # Range wraps around the new year (e.g. Nov to Mar)
    # It's in the range if it's after the start date (late in the year)
    # OR before the end date (early in the year)
    return day_tuple >= month_day_start or day_tuple <= month_day_end


async def get_ncaabb_spreads(day: date) -> AsyncIterator[Odds]:
    day_params = []
    for gender in NcaabbGender:
        if is_between_dates(day, REGULAR_SEASON_START, REGULAR_SEASON_END):
            day_params.append(DayParams(day, gender, NcaabbGroup.d1))
        if is_between_dates(day, POST_SEASON_START, SEASON_END):
            for group in POSTSEASON_GROUPS:
                day_params.append(DayParams(day, gender, group))

    for params in day_params:
        async for odd in _get_ncaabb_odds(*params):
            yield odd
