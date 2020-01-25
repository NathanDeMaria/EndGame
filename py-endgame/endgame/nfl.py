import json
import pickle
from csv import DictWriter
from datetime import datetime
from logging import getLogger
from typing import Dict, List, Optional, Union

from .espn_games import get_games
from .season_cache import SeasonCache
from .types import Game, Week, Season, SeasonType
from .web import RequestParameters


logger = getLogger(__name__)


# Say each season ends on March 1st
SEASON_END = (3, 1)
BASE_URL = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard'
REAL_TEAMS = frozenset([
    "Arizona Cardinals", "Atlanta Falcons", "Baltimore Ravens", "Buffalo Bills",
    "Carolina Panthers", "Chicago Bears", "Cincinnati Bengals", "Cleveland Browns",
    "Dallas Cowboys", "Denver Broncos", "Detroit Lions", "Green Bay Packers",
    "Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars", "Kansas City Chiefs",
    "Los Angeles Chargers", "Los Angeles Rams", "Miami Dolphins", "Minnesota Vikings",
    "New England Patriots", "New Orleans Saints", "New York Giants", "New York Jets",
    "Oakland Raiders", "Philadelphia Eagles", "Pittsburgh Steelers", "San Francisco 49ers",
    "Seattle Seahawks", "Tampa Bay Buccaneers", "Tennessee Titans", "Washington Redskins",
])
N_REGULAR_WEEKS = 17


async def update(location: str = 'nfl.csv'):
    now = datetime.utcnow()
    end_year = now.year - 1 if (now.month, now.day) < SEASON_END else now.year

    seasons = [(await get_season(year)) for year in range(1999, end_year + 1)]

    with open(location, 'w') as f:
        writer = DictWriter(f, fieldnames=['season', 'week'] + seasons[0].weeks[0].games[0].column_names)
        writer.writeheader()
        for season in seasons:
            for week in season.weeks:
                for game in week.games:
                    writer.writerow(dict(
                        season=season.year,
                        week=week.number,
                        **game.to_dict(),
                    ))


async def get_season(year: int) -> Season:
    logger.info(f"Getting NFL season {year}")
    cache = SeasonCache('nfl')
    s = cache.check_cache(year)
    if s:
        return s

    # This "season" is 2019 for the season whose Super Bowl is in 2020
    weeks = []
    for week in range(1, N_REGULAR_WEEKS + 1):
        weeks.append(await get_week(year, week, SeasonType.regular))
    for week in range(1, 6):
        weeks.append(await get_week(year, week, SeasonType.post))
    season = Season(weeks, year)

    # Cache if the season is over
    season_end_date = datetime(year + 1, *SEASON_END)
    if datetime.utcnow() > season_end_date:
        cache.save_to_cache(year, season)

    return season


async def get_week(season: int, week: int, season_type: SeasonType) -> Week:
    logger.info(f"Getting NFL {season} week {week}")
    parameters: RequestParameters = dict(
        lang='en',
        region='us',
        calendartype='blacklist',
        limit=32,
        seasontype=season_type.value,
        dates=season,
        week=week,
    )

    games = await get_games(BASE_URL, parameters)
    games = [
        _move_teams(g)
        for g in games
        if g.home in REAL_TEAMS
    ]

    if season_type == SeasonType.post:
        week += N_REGULAR_WEEKS
    return Week(games, week)


def _move_teams(game: Game) -> Game:
    d = game.to_dict()
    d['away'] = _move_team_name(d['away'])
    d['home'] = _move_team_name(d['home'])
    return Game(**d)


def _move_team_name(old_name: str) -> str:
    return old_name.replace('San Diego', 'Los Angeles').replace('St. Louis', 'Los Angeles')
