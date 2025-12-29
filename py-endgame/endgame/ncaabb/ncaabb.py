from datetime import date, timedelta, datetime
from enum import Enum
from itertools import groupby
from logging import getLogger
from typing import List, NamedTuple, AsyncIterator
import aiohttp

from ..async_tools import apply_in_parallel
from ..constants import ESPN_SPORTS_API_BASE
from ..date import get_end_year
from ..types import Game, Season, Week
from ..espn_games import get_games, save_seasons
from ..espn_odds import get_odds, Odds
from ..season_cache import SeasonCache
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
    args = [[y, gender] for y in range(2001, end_year + 1)]
    return [s async for s in apply_in_parallel(get_ncaabb_season, args)]


async def get_ncaabb_season(year: int, gender: NcaabbGender) -> Season:
    logger.info("Getting NCAABB %s season %d", gender.name, year)
    cache = SeasonCache(f"ncaa{gender.name[0]}bb")
    season = cache.check_cache(year)
    if season:
        return season

    day_params: List[DayParams] = []
    start = date(year, *REGULAR_SEASON_START)
    end = date(year + 1, *REGULAR_SEASON_END)
    # Don't try to get dates in the future
    end = min(end, date.today())
    for day in _date_range(start, end):
        day_params.append(DayParams(day, gender, NcaabbGroup.d1))
    start = date(year + 1, *POST_SEASON_START)
    end = date(year + 1, *SEASON_END)
    # Don't try to get dates in the future
    end = min(end, date.today())
    for group in POSTSEASON_GROUPS:
        for day in _date_range(start, end):
            day_params.append(DayParams(day, gender, group))

    games: List[Game] = []
    trouble_days = []
    for day_param in day_params:
        try:
            games += await _get_ncaabb_games(*day_param)
        except aiohttp.client_exceptions.ClientResponseError:
            day, gender, group = day_param
            logger.warning(
                "Marking %s for %s %s as trouble", day, gender.name, group.name
            )
            trouble_days.append(day_param)

    # Group into weeks.
    week_groups = groupby(
        sorted(games, key=lambda g: g.date), key=lambda g: _get_week(g.date)
    )
    weeks = [
        Week(list(week_games), week_num + 1)
        for week_num, (_, week_games) in enumerate(week_groups)
    ]
    season = Season(weeks, year, trouble_days)

    if datetime.utcnow() > datetime(year + 1, *SEASON_END):
        cache.save_to_cache(season)

    return season


def _get_week(gametime: datetime) -> date:
    # The AP poll is released based on Monday-Sunday games,
    # so I'll default to that grouping.
    # 7 is the .weekday for Sunday
    days_until_sunday = 7 - gametime.weekday()
    return gametime.date() + timedelta(days=days_until_sunday)


def _date_range(start: date, end: date) -> List[date]:
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(days)]


async def _get_ncaabb_games(
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
    # Filtering thanks to Montana State Bobcats at Northern Arizona Lumberjacks on 2003-02-28
    # and a bunch of NCAAWBB games
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


async def get_ncaabb_spreads(day: date) -> AsyncIterator[Odds]:
    regular_start = date(day.year, *REGULAR_SEASON_START)
    regular_end = date(day.year + 1, *REGULAR_SEASON_END)
    postseason_start = date(day.year + 1, *POST_SEASON_START)
    postseason_end = date(day.year + 1, *SEASON_END)
    day_params = []
    for gender in NcaabbGender:
        if regular_start <= day <= regular_end:
            day_params.append(DayParams(day, gender, NcaabbGroup.d1))
        if postseason_start <= day <= postseason_end:
            for group in POSTSEASON_GROUPS:
                day_params.append(DayParams(day, gender, group))

    for params in day_params:
        async for odd in _get_ncaabb_odds(*params):
            yield odd
