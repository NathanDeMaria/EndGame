from datetime import date, datetime, timezone
from logging import getLogger
from typing import AsyncIterator

from endgame.async_tools import apply_in_parallel
from endgame.date import get_end_year
from endgame.espn_games import get_games, save_seasons
from endgame.espn_odds import Odds, get_odds_range
from endgame.season_cache import SeasonCache
from endgame.types import Game, Season, SeasonType, Week
from endgame.web import RequestParameters

from .teams import NflTeam

logger = getLogger(__name__)


# Say each season ends on March 1st
SEASON_END = (3, 1)
BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
REAL_TEAMS = frozenset(
    [
        "Arizona Cardinals",
        "Atlanta Falcons",
        "Baltimore Ravens",
        "Buffalo Bills",
        "Carolina Panthers",
        "Chicago Bears",
        "Cincinnati Bengals",
        "Cleveland Browns",
        "Dallas Cowboys",
        "Denver Broncos",
        "Detroit Lions",
        "Green Bay Packers",
        "Houston Texans",
        "Indianapolis Colts",
        "Jacksonville Jaguars",
        "Kansas City Chiefs",
        "Los Angeles Chargers",
        "Los Angeles Rams",
        "Miami Dolphins",
        "Minnesota Vikings",
        "New England Patriots",
        "New Orleans Saints",
        "New York Giants",
        "New York Jets",
        "Oakland Raiders",
        "Philadelphia Eagles",
        "Pittsburgh Steelers",
        "San Francisco 49ers",
        "Seattle Seahawks",
        "Tampa Bay Buccaneers",
        "Tennessee Titans",
        "Washington",
    ]
)
N_REGULAR_WEEKS = 17


async def update(location: str = "nfl.csv"):
    """
    Update the nfl.csv
    """
    end_year = get_end_year(SEASON_END)
    args = [(y,) for y in range(1999, end_year + 1)]
    seasons = [s async for s in apply_in_parallel(get_season, args)]
    save_seasons(seasons, location)


async def get_season(
    year: int,
    # Keyword-only so the positional signature stays `(year,)`, which is what
    # `apply_in_parallel` unpacks its arg tuples into.
    *,
    include_unplayed: bool = False,
) -> Season:
    """
    Get an NFL season

    `include_unplayed` keeps the games ESPN hasn't finished, so the season
    carries the fixtures ahead of it as well as the results behind it. A
    season fetched with it holds games with no result yet -- read
    `game.completed` before reading a score.

    It costs nothing here: the season is already every week, and a week's
    request comes back with its fixtures whether or not they've been played.

    It needs nothing from the season cache, which is only written once a
    season is over and every game in it is complete: there's no unplayed
    game for a cached season to be missing, so a hit is as good either way
    and the cache doesn't have to know which way it was fetched.
    """
    logger.info("Getting NFL season %d", year)
    cache = SeasonCache("nfl")
    season = cache.check_cache(year)
    if season:
        return season

    # This "season" is 2019 for the season whose Super Bowl is in 2020
    weeks = []
    for week in range(1, N_REGULAR_WEEKS + 1):
        weeks.append(
            await _get_week(
                year, week, SeasonType.regular, include_unplayed=include_unplayed
            )
        )
    for week in range(1, 6):
        weeks.append(
            await _get_week(
                year, week, SeasonType.post, include_unplayed=include_unplayed
            )
        )
    season = Season(weeks, year)

    # Cache if the season is over
    season_end_date = datetime(year + 1, *SEASON_END, tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > season_end_date:
        cache.save_to_cache(season)

    return season


async def _get_week(
    season: int,
    week: int,
    season_type: SeasonType,
    *,
    include_unplayed: bool = False,
) -> Week:
    logger.info("Getting NFL %d %s week %d", season, season_type.name, week)
    parameters: RequestParameters = dict(
        lang="en",
        region="us",
        calendartype="blacklist",
        limit=32,
        seasontype=season_type.value,
        dates=season,
        week=week,
    )

    games = await get_games(BASE_URL, parameters, include_unplayed=include_unplayed)
    # Filtering on the home team's name, which a fixture has as much as a
    # result does -- so this drops the Pro Bowl either way round.
    games = [move_teams(g) for g in games if g.home in REAL_TEAMS]

    if season_type == SeasonType.post:
        week += N_REGULAR_WEEKS
    return Week(sorted(games, key=lambda g: g.date), week)


# Sixteen games a week, so a whole season is one request. Worth spending it
# in one: the NFL is priced further ahead than anything else here -- 272 of
# 285 games already had a line in early September, out to the following
# January -- and this is what reaches them.
ODDS_CHUNK_DAYS = 180


async def get_nfl_odds(start: date, end: date) -> AsyncIterator[Odds]:
    """
    Get the odds on every NFL game between `start` and `end`, inclusive.

    This used to ask for whatever week ESPN called "this week", by sending
    no dates at all and taking the default. That was a week of visibility
    into a league that prices its whole season by September, so the opening
    line on all but the nearest games was never recorded.
    """
    parameters: RequestParameters = dict(lang="en", region="us")
    async for odd in get_odds_range(
        BASE_URL, parameters, start=start, end=end, chunk_days=ODDS_CHUNK_DAYS
    ):
        yield odd


def move_teams(game: Game) -> Game:
    game_dict = game.to_dict()
    game_dict["away"] = _move_team_name(game_dict["away"])
    game_dict["home"] = _move_team_name(game_dict["home"])
    return Game(**game_dict)


def _move_team_name(old_name: str) -> str:
    tidy_name = (
        old_name.replace("San Diego", "Los Angeles")
        .replace("St. Louis", "Los Angeles")
        .replace("Washington Redskins", "commanders")
        .replace("Washington", "commanders")
        .replace("Oakland Raiders", "Las Vegas Raiders")
        .replace("49ers", "niners")
    )
    return NflTeam[tidy_name.split(" ")[-1].lower()].name
