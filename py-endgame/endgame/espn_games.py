"""
Parsing games from the ESPN API
"""
import json
from csv import DictWriter
from typing import Dict, NamedTuple, List, Optional
from dateutil import parser

from .types import Game, Season
from .web import RequestParameters, get


def save_seasons(seasons: List[Season], location: str):
    """
    Save seasons for some ESPN-formatted sport.
    """
    field_names = ["season", "week"] + _get_first_game(seasons).column_names
    with open(location, "w") as file:
        writer = DictWriter(file, fieldnames=field_names)
        writer.writeheader()
        for season in seasons:
            for week in season.weeks:
                for game in week.games:
                    writer.writerow(
                        dict(
                            season=season.year,
                            week=week.number,
                            **game.to_dict(),
                        )
                    )


def _get_first_game(seasons: List[Season]) -> Game:
    for season in seasons:
        for week in season.weeks:
            for game in week.games:
                return game
    raise ValueError("No games to save")


async def get_games(url: str, parameters: RequestParameters) -> List[Game]:
    """
    Get games for a set of parameters (probably a week or something)
    from the ESPN API
    """
    content = await get(url, parameters)
    tree = json.loads(content.data)

    attempted_games: List[Optional[Game]] = [parse_game(e) for e in tree["events"]]
    games = [g for g in attempted_games if g is not None]

    # Don't cache games if there are none here.
    # I ran into an issue with this when getting a postseason week
    # that would eventually have games, but the matchups weren't scheduled yet.
    if all(g.completed for g in games) and games:
        await content.save_if_necessary()

    return [g for g in games if g.completed]


def parse_game(event: Dict) -> Optional[Game]:
    """
    Parse data for a game out of the ESPN JSON response
    """
    if not event:
        return None
    # I'm not sure what causes this, but some games are empty
    # Ex: Butler vs. Providence on
    # https://www.espn.com/mens-college-basketball/scoreboard/_/date/20140121/seasontype/2/group/50
    # The game happened, but there's no play-by-play?
    # We're not using it here, just seems sus
    assert len(event["competitions"]) == 1
    competition = event["competitions"][0]
    competitiors = [_parse_competitor(c) for c in competition["competitors"]]
    assert len(competitiors) == 2
    completed = event["status"]["type"]["completed"]

    neutral_site = competition["neutralSite"]
    if neutral_site:
        # Doesn't matter
        home_index, away_index = 0, 1
    else:
        first_home = competitiors[0].is_home
        if not first_home ^ competitiors[1].is_home:
            raise ValueError(
                "Not neutral site, and not exactly 1 team is marked as home"
            )
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
        date=parser.parse(event["date"]),
        game_id=event["id"],
    )


class _Competitior(NamedTuple):
    name: str
    score: int
    is_home: bool


def _parse_competitor(competitor: Dict) -> _Competitior:
    return _Competitior(
        name=competitor["team"]["displayName"],
        score=int(competitor["score"]),
        is_home=competitor["homeAway"] == "home",
    )
