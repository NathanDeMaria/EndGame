import json
from dataclasses import dataclass
from logging import getLogger
from typing import Any, AsyncIterator, Dict, Iterator, List, Mapping, Optional

from aiohttp import ClientResponseError
from dataclasses_json import DataClassJsonMixin

from ...async_tools import apply_in_parallel
from ...cacheable import DiskCache
from ...types import Season, iter_weeks
from ...web import get
from ..gender import NcaabbGender
from ..ncaabb import get_seasons
from .player import PlayerBoxScore, parse_player

logger = getLogger(__name__)


_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "{gender}-college-basketball/summary"
)


@dataclass
class TeamBoxScore(DataClassJsonMixin):
    """
    Box score for a whole team. If the game is at a neutral site,
    "is_home" will be true for the team in the "home" slot on ESPN
    """

    players: List[PlayerBoxScore]
    is_home: bool
    team_id: str


@dataclass
class BoxScore(DataClassJsonMixin):
    """
    Box score of a game
    """

    game_id: str
    home: TeamBoxScore
    away: TeamBoxScore


@dataclass
class BoxScoreSeason(DataClassJsonMixin):
    """
    For cache
    """

    gender: str
    season: int
    games: List[BoxScore]


BOX_SCORE_CACHE = DiskCache(BoxScoreSeason, ["season", "gender"])


async def save_box_scores(gender: NcaabbGender, location: Optional[str] = None):
    """
    Save the box scores for all games that we have.

    NOTE: the "cache checking" logic here hits if there's
    ever been a successful run through all the games for this season
    (even if some of them failed). You'll want to manually bust if:
    - Logic for the box score parsing has changed
    - More game ids could be showing up for the same season.
    """
    if location is None:
        location = f"ncaa{gender.name[0]}bb_box.json"

    seasons = await get_seasons(gender)

    box_scores = []
    for season in seasons:
        scores = await BOX_SCORE_CACHE.check_cache(
            season=season.year, gender=gender.name
        )
        if scores is not None:
            box_scores.extend(scores.games)
            logger.info("Used box scores cache for %d", season.year)
            continue
        season_scores = [g async for g in get_season_box_scores(season, gender)]
        box_scores.extend(season_scores)
        await BOX_SCORE_CACHE.save_to_cache(
            BoxScoreSeason(gender.name, season.year, season_scores)
        )

    serialized = BoxScore.schema().dumps(box_scores, many=True)
    with open(location, "w") as file:
        file.write(serialized)


async def get_season_box_scores(
    season: Season, gender: NcaabbGender, skip_game_ids: set[str] | None = None
) -> AsyncIterator[BoxScore]:
    game_id_filter = skip_game_ids or set()
    # A week at a time, so the work is batched (and logged) in chunks
    # rather than firing off a whole season's games at once.
    for week in iter_weeks(season):
        # A game that hasn't finished has no box score to fetch: ESPN serves
        # the summary, there are just no players in it, so `get_box_score`
        # returns None. Skipping them
        # here is what lets a season carry games that haven't been played --
        # otherwise every unplayed game costs a request on every run between
        # now and kickoff.
        finished = [game for game in week.games if game.completed]
        args = [
            (gender, game.game_id)
            for game in finished
            if game.game_id not in game_id_filter
        ]
        if args:
            logger.info("Getting box scores for %d %d", season.year, week.number)
            games = apply_in_parallel(get_box_score, args)
            async for game in games:
                if game is not None:
                    yield game
        else:
            logger.info(
                "Nothing to pull for box scores %d %d (%d of %d games finished)",
                season.year,
                week.number,
                len(finished),
                len(week.games),
            )


async def get_box_score(gender: NcaabbGender, game_id: str) -> Optional[BoxScore]:
    """
    A game's box score, from ESPN's game summary -- the same endpoint the
    football play-by-play reads, under basketball.

    Read from the summary rather than the box score page since the page's
    markup moved (player names into `<th>` cells, the team header out of
    `Gamestrip__Team`) and the scraper found nothing on any game, old or
    new. The summary also says who started and each player's position,
    which the page only showed as layout.

    None for a game with no box score: unplayed, forfeited, or one ESPN
    can't serve. Never saved to the web cache, for the reason
    `football_plays.get_game_plays` gives: a summary is ~430KB, and a
    season of them is gigabytes to save requests nothing repeats.
    """
    url = _SUMMARY_URL.format(gender=gender.name)
    try:
        content = await get(url, {"event": game_id})
    except ClientResponseError as error:
        logger.warning("Skipping box score %s because of error %s", game_id, error)
        return None
    return parse_box_score(game_id, json.loads(content.data))


def parse_box_score(game_id: str, summary: Mapping[str, Any]) -> Optional[BoxScore]:
    """
    The box score in one game summary. Home and away come from the
    header's competitors, which is where ESPN says which team is which; the
    box score's team list itself doesn't.
    """
    competitions = (summary.get("header") or {}).get("competitions") or [{}]
    sides = {
        str(competitor["team"]["id"]): competitor["homeAway"]
        for competitor in competitions[0].get("competitors", [])
    }
    teams: Dict[str, TeamBoxScore] = {}
    for team in (summary.get("boxscore") or {}).get("players", []):
        team_id = str(team["team"]["id"])
        side = sides.get(team_id)
        players = list(_players(team))
        if side is None or not players:
            return None
        teams[side] = TeamBoxScore(
            players=players, is_home=side == "home", team_id=team_id
        )
    if set(teams) != {"home", "away"}:
        return None
    return BoxScore(game_id=game_id, home=teams["home"], away=teams["away"])


def _players(team: Mapping[str, Any]) -> Iterator[PlayerBoxScore]:
    """Everyone who played: an entry marked didNotPlay has no stats to read."""
    seen = set()
    for group in team.get("statistics", []):
        keys = group.get("keys", [])
        for entry in group.get("athletes", []):
            if entry.get("didNotPlay") or not entry.get("stats"):
                continue
            player = parse_player(entry, keys)
            if player.player_id not in seen:
                seen.add(player.player_id)
                yield player
