import aiohttp
from datetime import datetime
from logging import getLogger
from typing import List

from .types import Week, Season, WeekParams, NcaaFbGroup, SeasonType
from .espn_games import get_games
from .season_cache import SeasonCache
from .web import RequestParameters


logger = getLogger(__name__)


BASE_URL = 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard'
N_REGULAR_WEEKS = 16
SEASON_END = (2, 1)


async def update():
    # TODO: share this "end year" logic?
    now = datetime.utcnow()
    end_year = now.year - 1 if (now.month, now.day) < SEASON_END else now.year
    # TODO: use https://github.com/talkpython/async-techniques-python-course/blob/6c417a48a86a32413f3614a7a6bfe5cc96cb7687/src/04-asyncio/web_scraping/async_scrape/program.py#L49-L59
    # to actually do these in ||
    for year in range(1999, end_year + 1):
        await get_season(year)


async def get_season(year: int):
    logger.info(f"Getting NCAA season {year}")
    cache = SeasonCache('ncaafb')
    s = cache.check_cache(year)
    # TODO: add an option to ignore the cache if it has any skipped weeks?
    if s:
        return s

    week_params: List[WeekParams] = []
    for group in NcaaFbGroup:
        for week_num in range(1, N_REGULAR_WEEKS + 1):
            week_params.append((year, week_num, SeasonType.regular, group))
        week_params.append((year, 1, SeasonType.post, group))

    weeks = []
    trouble_weeks: List[WeekParams] = []
    for week_param in week_params:
        try:
            week = await _get_week(*week_param)
            weeks.append(week)
        # Should I raise custom exception instead?
        except aiohttp.client_exceptions.ClientResponseError:
            year, week, season_type, group = week_param
            logger.warning(f"Marking week as trouble: {year=} {week=} type={season_type.name} group={group.name}")
            trouble_weeks.append(week_param)
    season = Season(year, weeks, trouble_weeks)

    # Cache if the season is over
    season_end_date = datetime(year + 1, *SEASON_END)
    if datetime.utcnow() > season_end_date:
        cache.save_to_cache(year, season)

    return season


async def _get_week(
        year: int, week: int,
        season_type: SeasonType, group: NcaaFbGroup
) -> Week:
    logger.info(f"Getting NCAAFB {year} {season_type.name} week {week} for {group.name}")
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
