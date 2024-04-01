from dataclasses import dataclass
from logging import getLogger
from typing import Iterable, Iterator, List, Optional
from bs4 import BeautifulSoup, Tag
from dataclasses_json import DataClassJsonMixin
from aiohttp.client_exceptions import ClientResponseError

from ...async_tools import apply_in_parallel
from ...cacheable import DiskCache
from ...types import Season
from ...web import get
from ..gender import NcaabbGender
from ..ncaabb import get_seasons
from .player import RawPlayer, PlayerBoxScore, parse_player

logger = getLogger(__name__)


_URL_FORMAT = (
    "https://www.espn.com/{gender}-college-basketball/boxscore?gameId={game_id}"
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
class Betting(DataClassJsonMixin):
    home_spread: Optional[float]
    over_under: Optional[float]


@dataclass
class BoxScore(DataClassJsonMixin):
    """
    Box score of a game
    """

    game_id: str
    home: TeamBoxScore
    away: TeamBoxScore
    betting: Optional[Betting]


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
        season_scores = [g async for g in _get_season_box_scores(season, gender)]
        box_scores.extend(season_scores)
        await BOX_SCORE_CACHE.save_to_cache(
            BoxScoreSeason(gender.name, season.year, season_scores)
        )

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
    try:
        content = await get(url)
    except ClientResponseError as error:
        logger.warning("Skipping %s because of error %s", url, str(error))
        return None
    soup = BeautifulSoup(content.data, features="html.parser")

    tables = soup.select("div.Boxscore.ResponsiveTable")
    if len(tables) != 2:
        return None
    away_table, home_table = tables

    if away_table is None or home_table is None:
        return None

    away_header = soup.select_one("div.Gamestrip__Team--away a")
    home_header = soup.select_one("div.Gamestrip__Team--home a")

    if away_header is None or home_header is None:
        return None

    try:
        away_id = _get_team_id(away_header)
        home_id = _get_team_id(home_header)
        if raw_away := list(_read_table(away_table, away_id)):
            away_box_score = _parse_team_box_score(raw_away, False, away_id)
        else:
            return None
        if raw_home := list(_read_table(home_table, home_id)):
            home_box_score = _parse_team_box_score(raw_home, True, home_id)
        else:
            return None
        betting = _parse_betting(soup)
    except Exception as err:
        logger.warning("Struggling with %s", url)
        raise err

    await content.save_if_necessary()

    return BoxScore(game_id=game_id, home=home_box_score, away=away_box_score, betting=betting)


def _parse_betting(soup: BeautifulSoup) -> Optional[Betting]:
    betting_item = soup.select("div.GameInfo__BettingItem")
    if not betting_item:
        return None
    betting_info = dict(d.text.split(":") for d in betting_item)
    over_under = _parse_over_under(betting_info)
    home_spread = _parse_spread(betting_info, soup)

    return Betting(
        home_spread=home_spread,
        over_under=over_under,
    )


def _parse_spread(betting_info: dict, soup: BeautifulSoup) -> Optional[float]:
    if "Line" not in betting_info:
        return None
    line = betting_info["Line"].strip()
    favorite_short_name, raw_spread = line.split(" ")
    spread = float(raw_spread)
    overview = soup.select_one("div.Gamestrip__Overview")
    if overview is None:
        raise _ParseError
    away_short, home_short = _parse_short_names(overview)
    if favorite_short_name == away_short:
        return -spread
    elif favorite_short_name == home_short:
        return spread
    raise _SpreadMatchingError(home_short, away_short, favorite_short_name)


def _parse_over_under(betting_info: dict) -> Optional[float]:
    if (over_under := betting_info.get("Over/Under") is not None):
        return float(over_under)
    return None


class _SpreadMatchingError(Exception):
    def __init__(self, home: str, away: str, match: str) -> None:
        super().__init__(f"Failed to assign spead {home=} {away=} {match=}")


def _parse_short_names(overview: Tag) -> tuple[str, str]:
    _, away_row, home_row = overview.select("tr")
    return _get_td_text(away_row), _get_td_text(home_row)


def _get_td_text(tag: Tag) -> str:
    td = tag.select_one("td")
    if td is None:
        raise _ParseError
    return td.text

def _get_team_id(team_name_tag: Tag) -> str:
    team_link = team_name_tag.attrs["href"]
    # This link hopefully always looks like:
    # /womens-college-basketball/team/_/id/12/arizona-wildcats
    return team_link.split("/")[-2]


def _read_table(box_score_table: Tag, team_id: str) -> Iterator[RawPlayer]:
    names_table, stats_table = box_score_table.select("table")
    header = stats_table.select_one("tr:has(td.Table__customHeader)")
    if header is None:
        raise _ParseError
    columns = [
        col.text
        for col in header.select("td")
    ]
    player_names = names_table.select("td:not(.Table__customHeader)")
    if not player_names:
        # Blank for a forfeit, like
        # https://www.espn.com/womens-college-basketball/boxscore/_/gameId/401498641
        return
    if player_names[0].text.strip() == "No":
        return
    player_stats = stats_table.select("tr:has(td:not(.Table__customHeader))")
    assert len(player_names) == len(player_stats)
    for player_name, player_stat in zip(player_names, player_stats):
        stat_values = [td.text for td in player_stat.find_all("td")]
        if _is_did_not_play_row(stat_values):
            continue
        if stat_values[0] == "TEAM":
            continue
        yield _parse_player(player_name, columns, stat_values, team_id)


def _parse_player(
    player: Tag, columns: List[str], stat_values: List[str], team_id: str
) -> RawPlayer:
    if player_link := player.select_one("td a"):
        href = player_link.attrs["href"]
        *_, player_id, short_name = href.split("/")
    else:
        # If there's no ID, use the team+player name to make an ID
        # Transfers will be counted as new players, but I can't do better right now
        short_name = player.text
        player_id = f"{team_id}-{short_name}"
    assert len(columns) == len(stat_values), "Trouble parsing box score stats table"
    stats = dict(zip(columns, stat_values))
    return RawPlayer(player_id, short_name, stats)


def _is_did_not_play_row(stat_values: List[str]) -> bool:
    # Some player rows are just "Did not play" with a bunch of blank cells
    return len(stat_values) == 2 and stat_values[1].strip() == "Did not play"


def _parse_team_box_score(
    players: Iterable[RawPlayer], is_home: bool, team_id: str
) -> TeamBoxScore:
    return TeamBoxScore(
        players=[parse_player(p) for p in players], is_home=is_home, team_id=team_id
    )


class _ParseError(Exception):
    pass
