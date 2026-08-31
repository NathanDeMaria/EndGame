"""
Football play-by-play, flattened from ESPN's drive JSON into columns.

The raw side of this pipeline (`endgame_aws.stores.get_football_plays_store`)
stores drives exactly as ESPN sends them, which is the right thing for a
bronze layer and the wrong thing for anything that wants to read a game. This
turns one game's drives into a typed, rectangular table: one row per play,
every column a scalar, every type spelled out in `PLAY_SCHEMA`.

The schema is a pyarrow one rather than pandas dtypes on purpose. Most of
these columns are nullable integers -- down and distance are absent on a
kickoff, yardline is absent on an administrative play -- and pandas' default
casting turns a nullable int column into float64, which quietly makes a down
`3.0`. Arrow holds `int8` and `null` in the same column, and it's what gets
written to parquet anyway. Callers who want a DataFrame call `.to_pandas()`
on the result.
"""

from datetime import datetime, timezone
from logging import getLogger
from typing import Any, Iterable, Mapping, Sequence

import pyarrow as pa

logger = getLogger(__name__)


PLAY_SCHEMA = pa.schema(
    [
        # Identity. `league`/`season`/`week` are also the partition path, and
        # are repeated in the file so a table read straight out of one
        # partition still says what it is. They cost nothing: one dictionary
        # entry per column per row group.
        pa.field("league", pa.string(), nullable=False),
        pa.field("season", pa.int16(), nullable=False),
        pa.field("week", pa.int8(), nullable=False),
        pa.field("game_id", pa.string(), nullable=False),
        pa.field("play_id", pa.string(), nullable=False),
        pa.field("drive_id", pa.string()),
        # 1-based position in the game, from the order ESPN sends. Drives come
        # back chronological in both leagues; `sequence_number` sorts the same
        # way but is ESPN's own numbering, with gaps.
        pa.field("drive_number", pa.int16(), nullable=False),
        pa.field("play_number", pa.int16(), nullable=False),
        pa.field("sequence_number", pa.int64()),
        # Clock and score. Scores are cumulative *after* the play.
        pa.field("period", pa.int8()),
        pa.field("clock_display", pa.string()),
        pa.field("clock_seconds", pa.int16()),
        # Milliseconds, not seconds: ESPN's wallclock has second precision,
        # but parquet has no second-resolution timestamp and widens one to
        # ms on write. Declaring what round-trips means a table read back
        # out of the bucket still matches this schema, which is what lets
        # `merge_weeks` concatenate it with a freshly transformed one.
        pa.field("wallclock", pa.timestamp("ms", tz="UTC")),
        pa.field("home_score", pa.int16()),
        pa.field("away_score", pa.int16()),
        # Situation at the snap.
        pa.field("offense_team_id", pa.string()),
        pa.field("defense_team_id", pa.string()),
        pa.field("down", pa.int8()),
        # int16 rather than int8 like the rest: ESPN's distance is mostly
        # 1-25, but it sends the occasional negative one (a penalty enforced
        # from behind the spot), and a value that doesn't fit the type raises
        # on write -- which would fail the whole week over one odd play.
        pa.field("distance", pa.int16()),
        pa.field("yardline", pa.int8()),
        # ...and after it.
        pa.field("end_offense_team_id", pa.string()),
        pa.field("end_down", pa.int8()),
        pa.field("end_distance", pa.int16()),
        pa.field("end_yardline", pa.int8()),
        # The play itself.
        pa.field("play_type_id", pa.string()),
        pa.field("play_type", pa.string()),
        pa.field("text", pa.string()),
        pa.field("yards_gained", pa.int16()),
        pa.field("scoring_play", pa.bool_()),
        pa.field("scoring_type", pa.string()),
        pa.field("is_penalty", pa.bool_()),
        pa.field("is_turnover", pa.bool_()),
        pa.field("yards_after_catch", pa.int16()),
        # The drive the play belongs to, denormalized onto every play so a
        # drive-level question doesn't need a second table.
        pa.field("drive_team_id", pa.string()),
        pa.field("drive_result", pa.string()),
        pa.field("drive_yards", pa.int16()),
        pa.field("drive_plays", pa.int16()),
        pa.field("drive_is_score", pa.bool_()),
    ]
)


def transform_game_to_table(
    drives: Sequence[Mapping[str, Any]],
    game_id: str,
    league: str,
    season: int,
    week: int,
) -> pa.Table:
    """
    One game's drives, as a table of plays under `PLAY_SCHEMA`.

    `drives` is what `endgame.football_plays.get_game_plays` returns. A game
    ESPN has no play-by-play for comes back as an empty list, and gets an
    empty table with the full schema -- so it still concatenates with the rest
    of the week rather than breaking the column layout.
    """
    rows = list(iter_game_rows(drives, game_id, league, season, week))
    return _rows_to_table(rows)


def transform_week_to_table(
    games: Iterable[Mapping[str, Any]],
    league: str,
    season: int,
    week: int,
) -> pa.Table:
    """
    A whole stored week -- `[{"game_id": ..., "drives": [...]}, ...]`, the
    shape `FootballPlaysWeek` holds -- as one table.

    Rows come out sorted by `game_id`, which is what makes the single-game
    read cheap: parquet keeps min/max stats per row group, so a row group
    whose games don't include the one being asked for is skipped without
    being fetched. Sorting here rather than at write time keeps that property
    a fact about the data instead of a step someone can forget.
    """
    rows = [
        row
        for game in games
        for row in iter_game_rows(
            game.get("drives") or [], game["game_id"], league, season, week
        )
    ]
    rows.sort(key=lambda row: (row["game_id"], row["play_number"]))
    return _rows_to_table(rows)


def iter_game_rows(
    drives: Sequence[Mapping[str, Any]],
    game_id: str,
    league: str,
    season: int,
    week: int,
):
    """
    One dict per play, keyed by `PLAY_SCHEMA`'s field names.

    Separate from the table build so a caller assembling a whole week pays
    one Arrow conversion instead of one per game.
    """
    play_number = 0
    for drive_number, drive in enumerate(drives, start=1):
        drive_common = {
            "drive_id": _as_str(drive.get("id")),
            "drive_number": drive_number,
            "drive_team_id": _as_str(_get(drive, "team", "id")),
            "drive_result": _as_str(drive.get("result")),
            "drive_yards": _as_int(drive.get("yards")),
            "drive_plays": _as_int(drive.get("offensivePlays")),
            "drive_is_score": _as_bool(drive.get("isScore")),
        }
        for play in drive.get("plays") or []:
            play_number += 1
            start = play.get("start") or {}
            end = play.get("end") or {}
            yield {
                "league": league,
                "season": season,
                "week": week,
                "game_id": game_id,
                "play_id": _as_str(play.get("id")) or f"{game_id}-{play_number}",
                "play_number": play_number,
                "sequence_number": _as_int(play.get("sequenceNumber")),
                "period": _as_int(_get(play, "period", "number")),
                "clock_display": _as_str(_get(play, "clock", "displayValue")),
                "clock_seconds": parse_clock(_get(play, "clock", "displayValue")),
                "wallclock": parse_wallclock(play.get("wallclock")),
                "home_score": _as_int(play.get("homeScore")),
                "away_score": _as_int(play.get("awayScore")),
                "offense_team_id": _as_str(_get(start, "team", "id")),
                "defense_team_id": _defense_team_id(play),
                "down": _as_down(start.get("down")),
                "distance": _as_int(start.get("distance")),
                "yardline": normalize_yardline(start, is_start=True),
                "end_offense_team_id": _as_str(_get(end, "team", "id")),
                "end_down": _as_down(end.get("down")),
                "end_distance": _as_int(end.get("distance")),
                "end_yardline": normalize_yardline(end),
                "play_type_id": _as_str(_get(play, "type", "id")),
                "play_type": _as_str(_get(play, "type", "text")),
                "text": _as_str(play.get("text")),
                "yards_gained": _as_int(play.get("statYardage")),
                "scoring_play": _as_bool(play.get("scoringPlay")),
                "scoring_type": _as_str(_get(play, "scoringType", "abbreviation")),
                "is_penalty": _as_bool(play.get("isPenalty")),
                "is_turnover": _as_bool(play.get("isTurnover")),
                "yards_after_catch": _as_int(play.get("yardsAfterCatch")),
                **drive_common,
            }


def normalize_yardline(
    side: Mapping[str, Any], *, is_start: bool = False
) -> int | None:
    """
    A play's field position on one absolute scale: 1 is the offense's own
    1-yard line, 99 is the defense's.

    ESPN's own `yardLine` can't be used for this. The NFL sends it on a fixed
    field axis -- a snap at SEA 35 with Seattle on offense is 65 -- while
    college sends it already relative to whoever has the ball, so the same
    number means different things in the two leagues this pulls. Only
    `yardsToEndzone` is possession-relative in both, and this scale is its
    complement.

    None when the play has no possession team. The two or three
    administrative plays in a game (END QUARTER, END GAME) carry
    `yardsToEndzone: 0` with no team, which would otherwise normalize to 100
    and read as "snapped in the opponent's end zone". 0 and 100 are real
    values for a genuine play, though: a snap on your own goal line, and the
    end of a play that reached the end zone.

    Clamped to that 0-100 range, because `yardsToEndzone` is noisy. Measured
    on NCAAFB 2026 week 2 (12,762 plays), it agrees with ESPN's own
    `possessionText` on 97% of plays and is self-consistent with
    `statYardage` on 95%, with the disagreement spread evenly across play
    types rather than concentrated anywhere. The out-of-range values are the
    same noise where it crosses from wrong into impossible -- five in that
    week, from -30 to 130. A yardline outside the field isn't a yardline, so
    it's pinned to the nearest goal line and logged.
    """
    if _get(side, "team", "id") is None:
        return None
    to_endzone = _as_int(side.get("yardsToEndzone"))
    if to_endzone is None:
        return None
    if is_start and to_endzone == 0:
        # Unpopulated, not "on the goal line". A play can end at the goal
        # line -- that's what a touchdown is, and it's 444 of one college
        # week's plays -- but nothing is ever snapped from it. So a zero
        # means something different on each side of the play, and only the
        # start side can conclude the field is missing.
        #
        # Two sources of it. ESPN's 2002-2004 seasons zero the field for
        # every play while `possessionText` still carries the spot, which
        # would otherwise make every yardline in those seasons a confident
        # 100. And in every era the non-snap rows -- official timeouts, the
        # two-minute warning, declined penalties -- carry it too: 360 of the
        # 2,548 plays in an NFL week, none of them a snap.
        return None
    yardline = 100 - to_endzone
    if not 0 <= yardline <= 100:
        logger.warning(
            "Clamping a yardline of %d (yardsToEndzone %d) into 0-100",
            yardline,
            to_endzone,
        )
        return min(max(yardline, 0), 100)
    return yardline


def parse_clock(display: Any) -> int | None:
    """
    "12:17" -> 737, the seconds left in the period.

    ESPN only sends the display string -- there's no numeric clock on a
    football play the way there is elsewhere -- so this is the only way to
    get an orderable clock. Anything that doesn't parse comes back None
    rather than raising: a clock nobody has seen before shouldn't take a
    week's ingest down.
    """
    if not isinstance(display, str):
        return None
    minutes, _, seconds = display.partition(":")
    try:
        return int(minutes) * 60 + int(seconds)
    except ValueError:
        logger.warning("Couldn't parse a play clock: %r", display)
        return None


def parse_wallclock(value: Any) -> datetime | None:
    """
    ESPN's `wallclock` -- "2025-09-26T00:15:34Z" -- as an aware datetime.

    Parsed here rather than left as a string so the column is a real
    timestamp: a range over it is then a comparison rather than a
    lexicographic accident, and parquet keeps min/max stats it can prune on.
    A play without one (a couple per NFL game) or one that doesn't parse
    comes back None.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Couldn't parse a play wallclock: %r", value)
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _defense_team_id(play: Mapping[str, Any]) -> str | None:
    for participant in play.get("teamParticipants") or []:
        if participant.get("type") == "defense":
            return _as_str(participant.get("id"))
    return None


def _get(mapping: Any, *keys: str) -> Any:
    """
    `mapping[a][b]`, or None as soon as anything along the way is missing or
    isn't a mapping. Most of these fields are two or three levels down and
    any of the levels can be absent.
    """
    for key in keys:
        if not isinstance(mapping, Mapping):
            return None
        mapping = mapping.get(key)
    return mapping


def _as_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    return None if value is None else bool(value)


def _as_down(value: Any) -> int | None:
    """
    Down, with ESPN's 0 read as "no down". Kickoffs, extra points and the
    administrative plays all carry down 0, which isn't a down anyone would
    filter for.
    """
    down = _as_int(value)
    return None if down == 0 else down


def _rows_to_table(rows: Sequence[Mapping[str, Any]]) -> pa.Table:
    """
    Rows to a table under `PLAY_SCHEMA`, including when there are none.

    `from_pylist` with an explicit schema does the type casting and the
    missing-key handling in one pass: a row without `yards_after_catch` gets
    a null there rather than shifting the layout, and an int that arrived as
    the string ESPN sent lands as an int.
    """
    return pa.Table.from_pylist(list(rows), schema=PLAY_SCHEMA)
