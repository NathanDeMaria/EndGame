from dataclasses import dataclass
from logging import getLogger
from typing import Dict, Iterable, Iterator, List, Optional
from bs4 import BeautifulSoup, Tag
from dataclasses_json import DataClassJsonMixin

from ..async_tools import apply_in_parallel
from ..cacheable import DiskCache
from ..types import Season
from ..web import get
from .gender import NcaabbGender
from .ncaabb import get_seasons


logger = getLogger(__name__)


_URL_FORMAT = (
    "https://www.espn.com/{gender}-college-basketball/boxscore?gameId={game_id}"
)
_MINUTES_PLAYED_KEY = "MIN"


@dataclass
class PlayerBoxScore(DataClassJsonMixin):
    """
    The box score for one player in one game
    """

    player_id: str
    short_name: str
    minutes_played: Optional[int]

    def get_link(self, gender: NcaabbGender) -> str:
        """
        Get the link to view the player's page on ESPN
        """
        return (
            f"https://www.espn.com/"
            f"{gender.name}-college-basketball/player/_/id/"
            f"{self.player_id}/{self.short_name}"
        )


@dataclass
class TeamBoxScore(DataClassJsonMixin):
    """
    Box score for a whole team. If the game is at a neutral site,
    "is_home" will be true for the team in the "home" slot on ESPN
    """

    players: List[PlayerBoxScore]
    is_home: bool


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

    season: int
    games: List[BoxScore]


BOX_SCORE_CACHE = DiskCache(BoxScoreSeason, ["season"])


async def save_box_scores(gender: NcaabbGender, location: str = None):
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
        scores = await BOX_SCORE_CACHE.check_cache(season=season.year)
        if scores is not None:
            box_scores.extend(scores.games)
            logger.info("Used box scores cache for %d", season.year)
            continue
        season_scores = [g async for g in _get_season_box_scores(season, gender)]
        box_scores.extend(season_scores)
        await BOX_SCORE_CACHE.save_to_cache(BoxScoreSeason(season.year, season_scores))

    serialized = BoxScore.schema().dumps(box_scores, many=True)
    with open(location, "w") as file:
        file.write(serialized)


async def _get_season_box_scores(season: Season, gender: NcaabbGender):
    for week in season.weeks:
        logger.info("Getting box scores for %d %d", season.year, week.number)
        args = [(gender, game.game_id) for game in week.games]
        games = apply_in_parallel(get_box_score, args)
        async for game in games:
            if game is not None:
                yield game


async def get_box_score(gender: NcaabbGender, game_id: str) -> Optional[BoxScore]:
    """
    Get the box score for a game.
    Returns None only (so far) when we run into a known format
    that means, "this game doesn't have a box score"
    get failures will kill this.
    """
    url = _URL_FORMAT.format(gender=gender.name, game_id=game_id)
    content = await get(url)
    soup = BeautifulSoup(content.data, features="html.parser")

    away_table = soup.select_one("div.gamepackage-away-wrap")
    home_table = soup.select_one("div.gamepackage-home-wrap")

    if away_table is None or home_table is None:
        return None

    try:
        raw_away = list(_read_table(away_table))
        if not raw_away:
            return None
        away_box_score = _parse_team_box_score(raw_away, False)
        raw_home = list(_read_table(home_table))
        if not raw_home:
            return None
        home_box_score = _parse_team_box_score(raw_home, True)
    except Exception as err:
        logger.warning("Struggling with %s", url)
        raise err

    await content.save_if_necessary()

    return BoxScore(game_id=game_id, home=home_box_score, away=away_box_score)


@dataclass
class _RawPlayer:
    player_id: str
    short_name: str
    stats: Dict[str, str]


def _read_table(box_score_table: Tag) -> Iterator[_RawPlayer]:
    header, *players = box_score_table.find_all("tr", attrs={"class": None})
    columns = [th.text for th in header.find_all("th")]
    if players[0].text.strip() == "No":
        return
    for player in players:
        stat_values = [td.text for td in player.find_all("td")]
        if len(stat_values) == 2 and stat_values[1].strip() == "Did not play":
            continue
        if not stat_values:
            # This is probably the header row for the bench sub-table
            row_header = player.select_one("th.name").text
            assert row_header == "Bench", "Trouble parsing box score stats table"
            continue
        if stat_values[0] == "TEAM":
            continue
        player_link = player.select_one("td.name a").attrs.get("href")
        *_, player_id, short_name = player_link.split("/")
        assert len(columns) == len(stat_values), "Trouble parsing box score stats table"
        stats = dict(zip(columns, stat_values))
        yield _RawPlayer(player_id, short_name, stats)


def _parse_team_box_score(players: Iterable[_RawPlayer], is_home: bool) -> TeamBoxScore:
    return TeamBoxScore(
        players=[_parse_player(p) for p in players],
        is_home=is_home,
    )


def _parse_player(raw_player: _RawPlayer) -> PlayerBoxScore:
    try:
        minutes_played: Optional[int] = int(raw_player.stats[_MINUTES_PLAYED_KEY])
    except ValueError:
        minutes_played = None
    return PlayerBoxScore(
        player_id=raw_player.player_id,
        short_name=raw_player.short_name,
        minutes_played=minutes_played,
    )
