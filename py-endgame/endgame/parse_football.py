from typing import Dict, NamedTuple
from dateutil import parser

from .types import Game


def parse_game(event: Dict) -> Game:
    assert len(event['competitions']) == 1
    competition = event['competitions'][0]
    competitiors = [_parse_competitor(c) for c in competition['competitors']]
    assert len(competitiors) == 2
    completed = event['status']['type']['completed']

    if competition['neutralSite']:
        is_home = None
    else:
        is_home = competitiors[0].is_home
        if not (is_home ^ competitiors[1].is_home):
            raise ValueError("Not neutral site, and not exactly 1 team is marked as home")

    return Game(
        team=competitiors[0].name,
        team_score=competitiors[0].score,
        opponent=competitiors[1].name,
        opponent_score=competitiors[1].score,
        is_home=is_home,
        completed=completed,
        datetime=parser.parse(event['date'])
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
