from datetime import datetime
from enum import Enum
from logging import getLogger

from .types import Week, Season
from .espn_games import get_games
from .web import RequestParameters


logger = getLogger(__name__)


BASE_URL = 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard'
N_REGULAR_WEEKS = 16

class NcaaFbGroup(Enum):
    fbs = 80
    fcs = 81
    d23 = 35  # two AND three


class SeasonType(Enum):
    regular = 2
    post = 3


SEASON_END = (2, 1)


async def update():
    # TODO: make a SeasonCache class?
    # TODO: share this "end year" logic?
    now = datetime.utcnow()
    end_year = now.year - 1 if (now.month, now.day) < SEASON_END else now.year
    for year in range(1999, end_year):
        await get_season(year)


async def get_season(year):
    weeks = []
    for group in NcaaFbGroup:
        for week_num in range(1, N_REGULAR_WEEKS + 1):
            week = await _get_week(year, week_num, SeasonType.regular, group)
            weeks.append(week)
        weeks.append(await _get_week(year, 1, SeasonType.post, group))
    return Season(year, weeks)


async def _get_week(
        year: int, week: int,
        season_type: SeasonType, group: NcaaFbGroup
) -> Week:
    logger.info(f"Getting NCAAFB {year} week {week} for {group.name}")
    parameters: RequestParameters = dict(
        lang='en',
        region='us',
        calendartype='blacklist',
        limit=300,
        seasontype=season_type.value,
        dates=year,
        week=week,
        groups=group.value,
    )

    games = await get_games(BASE_URL, parameters)
    if season_type == SeasonType.post:
        week += N_REGULAR_WEEKS
    return Week(games, week)
