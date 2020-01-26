import aiohttp
from datetime import date, timedelta, datetime
from enum import Enum
from itertools import groupby
from logging import getLogger
from typing import List, NamedTuple

from .async_tools import apply_in_parallel
from .date import get_end_year
from .types import Game, Season, Week
from .espn_games import get_games, save_seasons
from .season_cache import SeasonCache
from .web import RequestParameters


logger = getLogger(__name__)


NCAABB_SCOREBOARD = 'https://site.api.espn.com/apis/site/v2/sports/basketball/{}-college-basketball/scoreboard'
REGULAR_SEASON_START = (11, 1)
REGULAR_SEASON_END = (4, 1)
POST_SEASON_START = (3, 1)
SEASON_END = (4, 30)

class NcaabbGender(Enum):
    womens = 'womens'
    mens = 'mens'


class NcaabbGroup(Enum):
    d1 = 50
    # Basketball counts postseason as a different "group"
    ncaa = 100
    nit = 98
    cbi = 55
    cit = 56

POSTSEASON_GROUPS = frozenset([
    NcaabbGroup.ncaa,
    NcaabbGroup.nit,
    NcaabbGroup.cbi,
    NcaabbGroup.cit,
])


class DayParams(NamedTuple):
    date: date
    gender: NcaabbGender
    group: NcaabbGroup


async def update(gender: NcaabbGender, location = None):
    if location is None:
        location = f'ncaa{gender.name[0]}bb.csv'
    end_year = get_end_year(SEASON_END)
    args = [[y, gender] for y in range(2001, end_year + 1)]
    seasons = [s async for s in apply_in_parallel(get_ncaabb_season, args)]
    save_seasons(seasons, location)


async def get_ncaabb_season(year: int, gender: NcaabbGender) -> Season:
    logger.info(f"Getting NCAABB {gender.name} season {year}")
    cache = SeasonCache(f'ncaa{gender.name[0]}bb')
    s = cache.check_cache(year)
    if s:
        return s

    day_params: List[DayParams] = []
    start = date(year, *REGULAR_SEASON_START)
    end = date(year + 1, *REGULAR_SEASON_END)
    for day in _date_range(start, end):
        day_params.append(DayParams(day, gender, NcaabbGroup.d1))
    start = date(year + 1, *POST_SEASON_START)
    end = date(year + 1, *SEASON_END)
    for group in POSTSEASON_GROUPS:
        for day in _date_range(start, end):
            day_params.append(DayParams(day, gender, group))

    games: List[Game] = []
    trouble_days = []
    for day_param in day_params:
        try:
            games += await get_ncaabb_games(*day_param)
        except aiohttp.client_exceptions.ClientResponseError:
            day, gender, group = day_param
            logger.warning(f"Marking {day} for {gender.name} {group.name} as trouble")
            trouble_days.append(day_param)
    
    # Group into weeks.
    week_groups = groupby(sorted(games, key=lambda g: g.date), key=lambda g: _get_week(g.date))
    weeks = [
        Week(list(week_games), week_num + 1)
        for week_num, (_, week_games)
        in enumerate(week_groups)
    ]
    season = Season(weeks, year, trouble_days)

    if datetime.utcnow() > datetime(year + 1, *SEASON_END):
        cache.save_to_cache(year, season)

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


async def get_ncaabb_games(game_date: date, gender: NcaabbGender, group: NcaabbGroup) -> List[Game]:
    logger.info(f"Getting NCAABB {gender.value} {game_date} {group.name}")
    parameters: RequestParameters = dict(
        lang='en',
        region='us',
        calendartype='blacklist',
        limit=300,
        dates=game_date.strftime('%Y%m%d'),
        groups=group.value,
    )
    return await get_games(NCAABB_SCOREBOARD.format(gender.name), parameters)
