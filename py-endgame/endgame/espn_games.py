"""
Parsing games from the ESPN API
"""
import json
from csv import DictWriter
from dateutil import parser
from typing import Dict, NamedTuple, List

from .types import Game, Season
from .web import RequestParameters, get


def save_seasons(seasons: List[Season], location: str):
    field_names = ['season', 'week'] + _get_first_game(seasons).column_names
    with open(location, 'w') as f:
        writer = DictWriter(f, fieldnames=field_names)
        writer.writeheader()
        for season in seasons:
            for week in season.weeks:
                for game in week.games:
                    writer.writerow(dict(
                        season=season.year,
                        week=week.number,
                        **game.to_dict(),
                    ))


def _get_first_game(seasons: List[Season]) -> Game:
    for s in seasons:
        for w in s.weeks:
            for g in w.games:
                return g
    raise ValueError("No games to save")


async def get_games(url: str, parameters: RequestParameters) -> List[Game]:
    content = await get(url, parameters)
    tree = json.loads(content.data)

    games: List[Game] = [parse_game(e) for e in tree['events']]

    if all(g.completed for g in games):
        await content.save_if_necessary()

    return [g for g in games if g.completed]


def parse_game(event: Dict) -> Game:
    assert len(event['competitions']) == 1
    competition = event['competitions'][0]
    competitiors = [_parse_competitor(c) for c in competition['competitors']]
    assert len(competitiors) == 2
    completed = event['status']['type']['completed']

    neutral_site = competition['neutralSite']
    if neutral_site:
        # Doesn't matter
        home_index, away_index = 0, 1
    else:
        first_home = competitiors[0].is_home
        if not (first_home ^ competitiors[1].is_home):
            raise ValueError("Not neutral site, and not exactly 1 team is marked as home")
        if first_home:
            home_index, away_index = 0, 1
        else:
            home_index, away_index = 1, 0

    return Game(
        home=competitiors[home_index].name,
        home_score=competitiors[home_index].score,
        away=competitiors[away_index].name,
        away_score=competitiors[away_index].score,
        neutral_site=neutral_site,
        completed=completed,
        date=parser.parse(event['date'])
    )


class Competitior(NamedTuple):
    name: str
    score: int
    is_home: bool


def _parse_competitor(competitor: Dict) -> Competitior:
    return Competitior(
        name=competitor['team']['displayName'],
        score=int(competitor['score']),
        is_home=competitor['homeAway'] == 'home',
    )
