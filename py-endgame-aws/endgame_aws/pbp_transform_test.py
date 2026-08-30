import pyarrow as pa

from .pbp_transform import (
    PLAY_SCHEMA,
    normalize_yardline,
    parse_clock,
    parse_wallclock,
    transform_game_to_table,
    transform_week_to_table,
)


def _drive(*plays: dict) -> dict:
    return {
        "id": "d1",
        "team": {"id": "22"},
        "result": "PUNT",
        "yards": 18,
        "offensivePlays": len(plays),
        "isScore": False,
        "plays": list(plays),
    }


def _play(**overrides) -> dict:
    play = {
        "id": "p1",
        "sequenceNumber": "4000",
        "type": {"id": "24", "text": "Pass Reception"},
        "text": "a pass",
        "awayScore": 0,
        "homeScore": 7,
        "period": {"number": 1},
        "clock": {"displayValue": "12:17"},
        "scoringPlay": False,
        "isPenalty": False,
        "isTurnover": False,
        "statYardage": 8,
        "wallclock": "2025-09-26T00:15:34Z",
        "teamParticipants": [
            {"id": "22", "type": "offense"},
            {"id": "26", "type": "defense"},
        ],
        "start": {
            "down": 1,
            "distance": 10,
            "yardLine": 26,
            "yardsToEndzone": 74,
            "team": {"id": "22"},
        },
        "end": {
            "down": 2,
            "distance": 2,
            "yardLine": 34,
            "yardsToEndzone": 66,
            "team": {"id": "22"},
        },
    }
    play.update(overrides)
    return play


def test_one_play_lands_in_every_column() -> None:
    table = transform_game_to_table([_drive(_play())], "g1", "nfl", 2025, 4)
    (row,) = table.to_pylist()

    assert row["league"] == "nfl"
    assert row["season"] == 2025
    assert row["week"] == 4
    assert row["game_id"] == "g1"
    assert row["play_id"] == "p1"
    assert row["drive_number"] == 1
    assert row["play_number"] == 1
    assert row["sequence_number"] == 4000
    assert row["clock_seconds"] == 737
    assert row["offense_team_id"] == "22"
    assert row["defense_team_id"] == "26"
    assert row["down"] == 1
    assert row["distance"] == 10
    assert row["yardline"] == 26
    assert row["end_yardline"] == 34
    assert row["yards_gained"] == 8
    assert row["drive_result"] == "PUNT"
    assert row["drive_plays"] == 1


def test_types_are_the_declared_ones() -> None:
    table = transform_game_to_table([_drive(_play())], "g1", "nfl", 2025, 4)
    assert table.schema.equals(PLAY_SCHEMA)
    # Not float64, which is where a nullable int column lands by default and
    # where a down starts reading as 3.0.
    assert table.schema.field("down").type == pa.int8()
    assert table.schema.field("wallclock").type == pa.timestamp("ms", tz="UTC")


def test_yardline_is_possession_relative_in_both_leagues() -> None:
    """
    The same field position, spelled the way each league spells it, has to
    normalize to the same number. ESPN's `yardLine` is on a fixed field axis
    for the NFL and already possession-relative for college; only
    `yardsToEndzone` means one thing in both.
    """
    nfl = {"yardLine": 44, "yardsToEndzone": 56, "team": {"id": "18"}}
    college = {"yardLine": 56, "yardsToEndzone": 56, "team": {"id": "2635"}}
    assert normalize_yardline(nfl) == normalize_yardline(college) == 44


def test_yardline_is_null_without_a_possession_team() -> None:
    """
    END QUARTER / END GAME carry `yardsToEndzone: 0` and no team, which would
    otherwise normalize to 100 -- "snapped in the opponent's end zone".
    """
    assert normalize_yardline({"down": 0, "distance": 0, "yardsToEndzone": 0}) is None


def test_a_missing_field_leaves_a_null_not_a_shifted_row() -> None:
    play = _play()
    del play["statYardage"]
    del play["wallclock"]
    play["start"] = {"down": 0, "distance": 0}
    (row,) = transform_game_to_table([_drive(play)], "g1", "nfl", 2025, 4).to_pylist()

    assert row["yards_gained"] is None
    assert row["wallclock"] is None
    assert row["yardline"] is None
    # down 0 is ESPN for "no down" -- a kickoff or an extra point -- not a
    # down anyone would filter for.
    assert row["down"] is None
    # ...and the columns after the gaps still hold their own values.
    assert row["play_type"] == "Pass Reception"
    assert row["drive_result"] == "PUNT"


def test_a_game_with_no_plays_keeps_the_schema() -> None:
    """
    Most of an NCAAFB week is D2/D3 games ESPN has no play-by-play for. They
    have to concatenate with the rest of the week rather than break it.
    """
    empty = transform_game_to_table([], "g1", "ncaafb", 2025, 4)
    assert empty.num_rows == 0
    assert empty.schema.equals(PLAY_SCHEMA)

    real = transform_game_to_table([_drive(_play())], "g2", "ncaafb", 2025, 4)
    assert pa.concat_tables([empty, real]).num_rows == 1


def test_play_number_counts_across_drives() -> None:
    game = [_drive(_play(), _play()), _drive(_play())]
    table = transform_game_to_table(game, "g1", "nfl", 2025, 4)
    assert table.column("play_number").to_pylist() == [1, 2, 3]
    assert table.column("drive_number").to_pylist() == [1, 1, 2]


def test_a_week_comes_out_sorted_by_game() -> None:
    """
    What the single-game read depends on: rows sorted by game mean parquet's
    per-row-group min/max can skip the games nobody asked for.
    """
    week = [
        {"game_id": "g3", "drives": [_drive(_play())]},
        {"game_id": "g1", "drives": [_drive(_play(), _play())]},
        {"game_id": "g2", "drives": []},
    ]
    table = transform_week_to_table(week, "nfl", 2025, 4)
    assert table.column("game_id").to_pylist() == ["g1", "g1", "g3"]


def test_unparseable_clock_and_wallclock_are_null() -> None:
    assert parse_clock("15:00") == 900
    assert parse_clock("") is None
    assert parse_clock(None) is None
    assert parse_clock("halftime") is None
    wallclock = parse_wallclock("2025-09-26T00:15:34Z")
    assert wallclock is not None
    assert wallclock.isoformat() == "2025-09-26T00:15:34+00:00"
    assert parse_wallclock("") is None
    assert parse_wallclock("not a time") is None
