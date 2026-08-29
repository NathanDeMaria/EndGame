"""
Parsing games from the ESPN API
"""

import json
from csv import DictWriter
from logging import getLogger
from typing import Dict, List, NamedTuple, Optional

from dateutil import parser

from .types import Game, Season
from .web import RequestParameters, get

logger = getLogger(__name__)

# ESPN files a game under a season type -- 1 preseason, 2 regular, 3
# postseason -- and files its competition under a type of its own. Telling a
# league game from an exhibition takes both, because neither is enough alone:
#
#   * Preseason is where the games against European clubs and national teams
#     live (Adler Mannheim, Jokerit, Nigeria, the Toyota Antelopes), so the
#     season type is what catches those.
#   * The All-Star game is filed under the *regular* season, not its own, so
#     the season type can't catch it.
#   * Neither can the competition type on its own: the 2023 NHL All-Star ran
#     a bracket whose games come back as "SEMI", which is exactly what a
#     conference final is called too.
#
# So it takes the pair, and the postseason is trusted whatever its
# competitions are called.
_PRESEASON, _REGULAR_SEASON, _POSTSEASON = 1, 2, 3

# The competitions that are league play *within* a regular season. "STD" is
# an ordinary game, in both leagues and in every season back to 2002. "CC" is
# the WNBA's Commissioner's Cup final -- one game a year, between two real
# teams, so it stays.
#
# An allowlist rather than a blocklist of the exhibitions, because the way
# this fails matters: an unrecognized competition costs a handful of real
# games, which shows up as a hole. Missing an exhibition puts a team that
# doesn't exist into the ratings, which shows up as Team Staal in a
# published top ten. The logging below is so the first failure isn't silent.
_REGULAR_SEASON_COMPETITIONS = frozenset({"STD", "CC"})


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


def is_league_game(event: Dict) -> bool:
    """
    Whether an event is the league actually playing itself.

    False for preseason, for the All-Star game and whatever tournament has
    replaced it in a given year, and so for the club and national sides that
    only ever turn up in those -- none of which belong in a rating.

    Only the leagues pulled a day at a time need this. The ones pulled by
    week ask ESPN for a `seasontype` up front, so their requests can't
    return a preseason game in the first place.
    """
    season_type = (event.get("season") or {}).get("type")
    if season_type == _POSTSEASON:
        return True
    if season_type == _PRESEASON:
        return False

    # Anything else is treated as the regular season, including a response
    # with no season block at all: the competition allowlist below is the
    # check that matters, and defaulting the other way would let an event
    # skip it by omitting a field.
    competition = (event.get("competitions") or [{}])[0]
    competition_type = (competition.get("type") or {}).get("abbreviation")
    if competition_type in _REGULAR_SEASON_COMPETITIONS:
        return True
    logger.info(
        "Dropping %s: not league play (season type %s, competition %s)",
        event.get("name", event.get("id")),
        season_type,
        competition_type,
    )
    return False


async def get_games(
    url: str, parameters: RequestParameters, league_games_only: bool = False
) -> List[Game]:
    """
    Get games for a set of parameters (probably a week or something)
    from the ESPN API

    `league_games_only` drops the exhibitions -- see `is_league_game`. It's
    off by default because the leagues pulled by week scope their requests
    with a `seasontype` instead, and would only pay to re-check it.
    """
    content = await get(url, parameters)
    tree = json.loads(content.data)

    events = tree["events"]
    if league_games_only:
        events = [e for e in events if is_league_game(e)]

    attempted_games: List[Optional[Game]] = [parse_game(e) for e in events]
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
