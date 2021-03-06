from datetime import datetime
from itertools import groupby
from logging import getLogger
from typing import List, Iterator
import aiohttp

from .async_tools import apply_in_parallel
from .date import get_end_year
from .types import Game, Week, Season, WeekParams, NcaaFbGroup, SeasonType
from .espn_games import get_games, save_seasons
from .season_cache import SeasonCache
from .web import RequestParameters


logger = getLogger(__name__)


BASE_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
)
N_REGULAR_WEEKS = 16
SEASON_END = (2, 1)


async def update(location="ncaaf.csv"):
    """
    Update the NCAAFB data
    """
    end_year = get_end_year(SEASON_END)
    args = [[y] for y in range(1999, end_year + 1)]
    seasons = [s async for s in apply_in_parallel(get_season, args)]
    save_seasons(seasons, location)


async def get_season(year: int) -> Season:
    """
    Get the games from a season of NCAAFB
    """
    logger.info("Getting NCAA season %s", year)
    cache = SeasonCache("ncaafb")
    season = cache.check_cache(year)
    # TODO: add an option to ignore the cache if it has any skipped weeks?
    if season:
        return season

    week_params: List[WeekParams] = []
    for group in NcaaFbGroup:
        for week_num in range(1, N_REGULAR_WEEKS + 1):
            week_params.append(WeekParams(year, week_num, SeasonType.regular, group))
        week_params.append(WeekParams(year, 1, SeasonType.post, group))

    weeks = []
    trouble_weeks: List[WeekParams] = []
    for week_param in week_params:
        try:
            week = await _get_week(*week_param)
            weeks.append(week)
        # Should I raise custom exception instead?
        except aiohttp.client_exceptions.ClientResponseError:
            year, week_num, season_type, group = week_param
            msg = (
                f"Marking week as trouble: "
                f"{year=} {week_num=} type={season_type.name} group={group.name}"
            )
            logger.warning(msg)
            trouble_weeks.append(week_param)

    weeks = list(_remove_cross_division_duplicates(weeks))
    season = Season(weeks, year, trouble_weeks)

    # Cache if the season is over
    season_end_date = datetime(year + 1, *SEASON_END)
    if datetime.utcnow() > season_end_date:
        cache.save_to_cache(season)

    return season


def _remove_cross_division_duplicates(weeks: List[Week]) -> Iterator[Week]:
    # Removes duplicates that come from when teams play across divisions
    # Assumption: those still show up under the same week number
    # ...I'm not totally sure that's the case
    key = lambda w: w.number
    for number, matched_weeks in groupby(sorted(weeks, key=key), key=key):
        games: List[Game] = []
        for week in matched_weeks:
            games += week.games
        yield Week(list(set(games)), number)


async def _get_week(
    year: int, week: int, season_type: SeasonType, group: NcaaFbGroup
) -> Week:
    msg = f"Getting NCAAFB {year} {season_type.name} week {week} for {group.name}"
    logger.info(msg)
    parameters: RequestParameters = dict(
        lang="en",
        region="us",
        calendartype="blacklist",
        limit=300,
        seasontype=season_type.value,
        dates=year,
        week=week,
        groups=group.value,
    )

    games = await get_games(BASE_URL, parameters)
    games = list(map(_rename_teams, games))
    if season_type == SeasonType.post:
        week += N_REGULAR_WEEKS
    return Week(games, week)


def _rename_teams(game: Game) -> Game:
    game_dict = game.to_dict()
    game_dict["away"] = _rename_team(game_dict["away"])
    game_dict["home"] = _rename_team(game_dict["home"])
    return Game(**game_dict)


def _rename_team(name: str) -> str:
    # Adjust any teams that have gone by multiple names
    if name == "Army Knights":
        return "Army Black Knights"
    if name == "Hawaii Warriors":
        return "Hawai'i Rainbow Warriors"
    if name == "Connecticut Huskies":
        return "UConn Huskies"
    if name == "Southern Methodist Mustangs":
        return "SMU Mustangs"
    if name == "Southern University Jaguars":
        return "Southern Jaguars"
    return name
