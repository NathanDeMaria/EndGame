"""
NCAABB play-by-play, flattened from the stored ESPN page JSON into columns.

The basketball counterpart to `pbp_transform`. The raw layer
(`endgame_aws.stores.get_pbp_store`, at `plays/ncaabb/{league}/{day}.json`)
keeps each day's games exactly as `endgame.ncaabb.get_plays_for_day` scraped
them: the play list ESPN's play-by-play page carries, with a headshot url,
a team logo and a live win probability repeated on every play. This keeps
what a reader of the game needs -- who did what, when, for which side, and
where the shot was from -- one row per play, typed under
`BASKETBALL_PLAY_SCHEMA`.

Nothing here interprets the plays. Possessions, lineups and anything else
built from them belong to whoever reads this table; the transform's one
opinion is which copy of a game to keep (see `transform_day_to_table`).
"""

from datetime import date
from logging import getLogger
from typing import Any, Iterable, Iterator, Mapping, Sequence

import pyarrow as pa

from .pbp_transform import _as_bool, _as_int, _as_str, _get, parse_clock

logger = getLogger(__name__)

# ESPN's placeholder for "no coordinate": -214748340, which is int32's minimum
# scaled by ten. Anything past this is a placeholder, not a spot on the floor.
_COORDINATE_LIMIT = 1000


BASKETBALL_PLAY_SCHEMA = pa.schema(
    [
        # Identity. `league`/`season`/`date` are also the partition path, and
        # are repeated in the file for the same reason as football's: a table
        # read out of one partition still says what it is.
        pa.field("league", pa.string(), nullable=False),
        pa.field("season", pa.int16(), nullable=False),
        pa.field("date", pa.date32(), nullable=False),
        pa.field("game_id", pa.string(), nullable=False),
        pa.field("play_id", pa.string(), nullable=False),
        # 1-based position in the game, in the order the page lists the plays,
        # which is chronological. Plays at the same clock -- a foul, the subs
        # it allowed, the free throws -- keep that order, and it's the only
        # thing that says which came first.
        pa.field("play_number", pa.int16(), nullable=False),
        # Clock. Mens play halves and womens quarters, so a period means
        # different lengths in the two leagues; overtime periods follow on.
        pa.field("period", pa.int8()),
        pa.field("clock_display", pa.string()),
        pa.field("clock_seconds", pa.int16()),
        # The score after the play, as ESPN stamped it. On the play that
        # scored it's right; on others it is occasionally stale, and now and
        # then it runs backwards for a play. Count points from the scoring
        # plays rather than differencing these.
        pa.field("home_score", pa.int16()),
        pa.field("away_score", pa.int16()),
        # "home" or "away": the side the play is credited to. Null on the
        # plays that belong to neither, like the start of a period or an
        # official timeout.
        pa.field("side", pa.string()),
        # What happened. `play_category_id` is the coarse code that groups
        # plays by meaning -- made two, missed three, defensive rebound,
        # substitution. It is missing on about half the plays -- the shots --
        # through at least 2019-20, and on none by 2022-23; `play_type` is the
        # finer-grained name, and the one that exists in every season.
        pa.field("play_type_id", pa.string()),
        pa.field("play_category_id", pa.string()),
        pa.field("play_type", pa.string()),
        pa.field("text", pa.string()),
        # Who. `athlete_id` is the player the play is about -- the shooter,
        # the rebounder, the player subbing in or out. `participant_ids` is
        # everyone ESPN tied to it, which on a made shot is the shooter then
        # the assister. A steal carries neither: its player is only in `text`.
        pa.field("athlete_id", pa.string()),
        pa.field("athlete_name", pa.string()),
        pa.field("participant_ids", pa.list_(pa.string())),
        pa.field("scoring_play", pa.bool_()),
        pa.field("shooting_play", pa.bool_()),
        pa.field("is_free_throw", pa.bool_()),
        # 1, 2 or 3 on a shot. Absent through at least 2019-20, sent by 2022-23.
        pa.field("points_attempted", pa.int8()),
        # Where the shot was taken from, on shooting plays only: every other
        # play carries a default spot that isn't anywhere the play happened.
        pa.field("shot_x", pa.int16()),
        pa.field("shot_y", pa.int16()),
    ]
)


def transform_day_to_table(
    games: Iterable[Mapping[str, Any]],
    league: str,
    season: int,
    day: date,
) -> pa.Table:
    """
    A whole stored day -- `[{"game_id": ..., "plays": [...]}, ...]` -- as one
    table, sorted by game.

    A game listed more than once is kept once. `get_plays_for_day` used to
    ask for each of ESPN's groups in turn, and a postseason game is listed
    under both d1 and its tournament, so every postseason day stored before
    it learned not to has each of those games twice. The copies are the
    same page fetched twice; the first is kept.
    """
    seen: set[str] = set()
    rows = []
    for game in games:
        game_id = str(game["game_id"])
        if game_id in seen:
            continue
        seen.add(game_id)
        rows.extend(
            iter_game_rows(game.get("plays") or [], game_id, league, season, day)
        )
    rows.sort(key=lambda row: (row["game_id"], row["play_number"]))
    return pa.Table.from_pylist(rows, schema=BASKETBALL_PLAY_SCHEMA)


def iter_game_rows(
    plays: Sequence[Mapping[str, Any]],
    game_id: str,
    league: str,
    season: int,
    day: date,
) -> Iterator[dict[str, Any]]:
    """One dict per play, keyed by `BASKETBALL_PLAY_SCHEMA`'s field names."""
    for play_number, play in enumerate(plays, start=1):
        shooting = _as_bool(play.get("shootingPlay"))
        yield {
            "league": league,
            "season": season,
            "date": day,
            "game_id": game_id,
            "play_id": _as_str(play.get("id")) or f"{game_id}-{play_number}",
            "play_number": play_number,
            "period": _as_int(_get(play, "period", "number")),
            "clock_display": _as_str(_get(play, "clock", "displayValue")),
            "clock_seconds": _clock_seconds(play),
            "home_score": _as_int(play.get("hmScr")),
            "away_score": _as_int(play.get("awScr")),
            "side": _side(play.get("homeAway")),
            "play_type_id": _as_str(_get(play, "type", "id")),
            "play_category_id": _as_str(_get(play, "type", "categoryId")),
            "play_type": _as_str(_get(play, "type", "txt")),
            "text": _as_str(play.get("text")),
            "athlete_id": _as_str(_get(play, "athlete", "id")),
            "athlete_name": _as_str(_get(play, "athlete", "name")),
            "participant_ids": [
                str(participant["id"])
                for participant in play.get("participants") or []
                if isinstance(participant, Mapping) and participant.get("id")
            ],
            "scoring_play": _as_bool(play.get("scoringPlay")),
            "shooting_play": shooting,
            "is_free_throw": _as_bool(play.get("isFreeThrow")),
            "points_attempted": _as_int(play.get("pointsAttempted")),
            "shot_x": _coordinate(play, "x") if shooting else None,
            "shot_y": _coordinate(play, "y") if shooting else None,
        }


def _clock_seconds(play: Mapping[str, Any]) -> int | None:
    """
    Seconds left in the period. The page sends a number alongside the
    display string; the string is the fallback for a play without one.
    """
    value = _as_int(_get(play, "clock", "value"))
    if value is not None:
        return value
    return parse_clock(_get(play, "clock", "displayValue"))


def _side(value: Any) -> str | None:
    return value if value in ("home", "away") else None


def _coordinate(play: Mapping[str, Any], axis: str) -> int | None:
    value = _as_int(_get(play, "coordinate", axis))
    if value is None or abs(value) > _COORDINATE_LIMIT:
        return None
    return value
