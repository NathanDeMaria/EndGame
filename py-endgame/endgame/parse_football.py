from typing import Dict, NamedTuple
from dateutil import parser

from .types import Game


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
