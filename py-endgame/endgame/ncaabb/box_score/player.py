from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple
from dataclasses_json import DataClassJsonMixin

from ..gender import NcaabbGender


# Parse an item from the box score table
# into any relevant stats
_Parser = Callable[[str], Dict[str, Optional[int]]]


def _parse_int(raw: str) -> Optional[int]:
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_fraction(raw: str) -> Tuple[Optional[int], Optional[int]]:
    numerator, denominator = raw.split("-")
    return _parse_int(numerator), _parse_int(denominator)


def _create_int_parser(name: str) -> _Parser:
    return lambda raw: {name: _parse_int(raw)}


def _create_fraction_parser(numerator_name: str, denominator_name: str) -> _Parser:
    def _parser(raw: str):
        numerator, denominator = _parse_fraction(raw)
        return {
            numerator_name: numerator,
            denominator_name: denominator,
        }

    return _parser


# Mapping from the column name on the box score page to a field
_KEYS_TO_STATS: List[Tuple[str, _Parser]] = [
    ("MIN", _create_int_parser("minutes_played")),
    ("FG", _create_fraction_parser("field_goal_makes", "field_goal_attempts")),
    ("3PT", _create_fraction_parser("three_point_makes", "three_point_attempts")),
    ("FT", _create_fraction_parser("free_throw_makes", "free_throw_attempts")),
    ("OREB", _create_int_parser("offensive_rebounds")),
    ("DREB", _create_int_parser("defensive_rebounds")),
    ("REB", _create_int_parser("rebounds")),
    ("AST", _create_int_parser("assists")),
    ("STL", _create_int_parser("steals")),
    ("BLK", _create_int_parser("blocks")),
    ("TO", _create_int_parser("turnovers")),
    ("PF", _create_int_parser("fouls")),
    ("PTS", _create_int_parser("points")),
]


@dataclass
class RawPlayer:
    """
    All the fields for a player coming out of the box score page, in raw form.
    """

    player_id: str
    short_name: str
    stats: Dict[str, str]


@dataclass
class PlayerBoxScore(DataClassJsonMixin):
    """
    The box score for one player in one game
    """

    player_id: str
    short_name: str
    minutes_played: Optional[int]

    field_goal_makes: Optional[int]
    field_goal_attempts: Optional[int]
    three_point_makes: Optional[int]
    three_point_attempts: Optional[int]
    free_throw_makes: Optional[int]
    free_throw_attempts: Optional[int]
    offensive_rebounds: Optional[int]
    defensive_rebounds: Optional[int]
    rebounds: Optional[int]
    assists: Optional[int]
    steals: Optional[int]
    blocks: Optional[int]
    turnovers: Optional[int]
    fouls: Optional[int]
    points: Optional[int]

    def get_link(self, gender: NcaabbGender) -> str:
        """
        Get the link to view the player's page on ESPN
        """
        return (
            f"https://www.espn.com/"
            f"{gender.name}-college-basketball/player/_/id/"
            f"{self.player_id}/{self.short_name}"
        )


def parse_player(raw_player: RawPlayer) -> PlayerBoxScore:
    """
    Parse all the stats in the raw row
    for a player in the box score table.
    """
    parsed_stats = {}
    for stat_field, parser in _KEYS_TO_STATS:
        parsed_stats.update(parser(raw_player.stats[stat_field]))
    return PlayerBoxScore(
        player_id=raw_player.player_id,
        short_name=raw_player.short_name,
        **parsed_stats,
    )
