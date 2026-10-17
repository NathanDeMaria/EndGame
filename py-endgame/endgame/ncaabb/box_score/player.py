from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from dataclasses_json import DataClassJsonMixin

from ..gender import NcaabbGender


def _parse_int(raw: str) -> Optional[int]:
    try:
        return int(raw)
    except ValueError:
        return None


# Each column ESPN's summary sends a box score in, by its key, and the fields
# it fills. A pair like "3-7" fills two.
_KEYS_TO_FIELDS: List[Tuple[str, Tuple[str, ...]]] = [
    ("minutes", ("minutes_played",)),
    ("fieldGoalsMade-fieldGoalsAttempted", ("field_goal_makes", "field_goal_attempts")),
    (
        "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
        ("three_point_makes", "three_point_attempts"),
    ),
    ("freeThrowsMade-freeThrowsAttempted", ("free_throw_makes", "free_throw_attempts")),
    ("offensiveRebounds", ("offensive_rebounds",)),
    ("defensiveRebounds", ("defensive_rebounds",)),
    ("rebounds", ("rebounds",)),
    ("assists", ("assists",)),
    ("steals", ("steals",)),
    ("blocks", ("blocks",)),
    ("turnovers", ("turnovers",)),
    ("fouls", ("fouls",)),
    ("points", ("points",)),
]


@dataclass
class PlayerBoxScore(DataClassJsonMixin):
    """
    The box score for one player in one game
    """

    player_id: str
    # The player's name as it appears in his ESPN url: "trey-kaufman-renn".
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

    # Whether he started, and ESPN's G/F/C. Defaulted because the box scores
    # stored before they were read don't have them -- which is also why a
    # subclass adding fields has to make them keyword-only (endgame_aws's
    # FlattenedBoxScore does).
    starter: Optional[bool] = None
    position: Optional[str] = None

    def get_link(self, gender: NcaabbGender) -> str:
        """
        Get the link to view the player's page on ESPN
        """
        return (
            f"https://www.espn.com/"
            f"{gender.name}-college-basketball/player/_/id/"
            f"{self.player_id}/{self.short_name}"
        )


def parse_player(entry: Mapping[str, Any], keys: List[str]) -> PlayerBoxScore:
    """
    One player's entry in a box score from ESPN's game summary -- the
    athlete, whether he started, and his stats as strings in `keys`' order.
    """
    athlete = entry["athlete"]
    raw = dict(zip(keys, entry.get("stats") or []))
    stats: Dict[str, Optional[int]] = {}
    for key, fields in _KEYS_TO_FIELDS:
        values = raw.get(key, "").split("-") if len(fields) > 1 else [raw.get(key, "")]
        for name, value in zip(fields, values + [""] * len(fields)):
            stats[name] = _parse_int(value)
    position = athlete.get("position") or {}
    return PlayerBoxScore(
        player_id=str(athlete["id"]),
        short_name=_slug(athlete),
        starter=bool(entry.get("starter")),
        position=position.get("abbreviation"),
        **stats,
    )


def _slug(athlete: Mapping[str, Any]) -> str:
    """
    The name in the player's ESPN url, which is what `short_name` has always
    been. Falls back to the short name ESPN shows, for an athlete without a
    link.
    """
    for link in athlete.get("links") or []:
        href = link.get("href") or ""
        if "/player/" in href:
            return href.rstrip("/").split("/")[-1]
    return str(athlete.get("shortName") or athlete["id"])
