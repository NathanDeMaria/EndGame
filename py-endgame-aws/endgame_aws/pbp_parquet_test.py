import pyarrow as pa
import pyarrow.parquet as pq

from .pbp_parquet import (
    _ROW_GROUP_SIZE,
    _serialize,
    build_week_key,
    conform_to_schema,
    merge_weeks,
)
from .pbp_transform import PLAY_SCHEMA, transform_week_to_table


def _week(*games: tuple[str, int]) -> pa.Table:
    """
    A week built from (game_id, number of plays) pairs, so a test can say how
    the games overlap without spelling out play JSON.
    """
    raw = [
        {
            "game_id": game_id,
            "drives": [
                {
                    "id": f"{game_id}-d1",
                    "plays": [
                        {"id": f"{game_id}-{i}", "text": f"play {i}"}
                        for i in range(n_plays)
                    ],
                }
            ],
        }
        for game_id, n_plays in games
    ]
    return transform_week_to_table(raw, "nfl", 2025, 4)


def test_key_is_hive_partitioned_and_zero_padded() -> None:
    assert (
        build_week_key("nfl", 2025, 4)
        == "processed/plays/league=nfl/season=2025/week=04/data.parquet"
    )


def test_merge_adds_new_games() -> None:
    merged = merge_weeks(_week(("g1", 2)), _week(("g2", 3)))
    assert merged.column("game_id").to_pylist() == ["g1"] * 2 + ["g2"] * 3


def test_merge_replaces_a_game_rather_than_appending_it() -> None:
    """
    A re-pull of a game ESPN revised has to replace it whole. Merging row by
    row would leave the old version's extra plays behind.
    """
    merged = merge_weeks(_week(("g1", 5), ("g2", 2)), _week(("g1", 3)))
    counts = merged.column("game_id").to_pylist()
    assert counts.count("g1") == 3
    assert counts.count("g2") == 2


def test_merge_keeps_the_week_sorted_by_game() -> None:
    merged = merge_weeks(_week(("g2", 2)), _week(("g1", 2), ("g3", 1)))
    assert merged.column("game_id").to_pylist() == sorted(
        merged.column("game_id").to_pylist()
    )


def test_merge_into_nothing_is_the_incoming_week() -> None:
    incoming = _week(("g1", 2))
    merged = merge_weeks(PLAY_SCHEMA.empty_table(), incoming)
    assert merged.num_rows == incoming.num_rows
    assert merged.schema.equals(PLAY_SCHEMA)


def test_written_file_is_split_into_prunable_row_groups() -> None:
    """
    The property `load_single_game` rests on: more than one row group, each
    carrying game_id statistics, so the reader can skip the ones whose games
    don't match. One row group for the whole week -- pyarrow's default -- is
    a correct read that downloads everything.
    """
    week = _week(*[(f"g{i:03d}", 60) for i in range(80)])
    assert week.num_rows > _ROW_GROUP_SIZE

    parquet_file = pq.ParquetFile(pa.BufferReader(_serialize(week)))
    metadata = parquet_file.metadata
    assert metadata.num_row_groups > 1

    game_id_column = metadata.schema.names.index("game_id")
    statistics = [
        metadata.row_group(i).column(game_id_column).statistics
        for i in range(metadata.num_row_groups)
    ]
    assert all(s is not None and s.has_min_max for s in statistics)
    # Sorted rows mean the ranges don't overlap, which is what makes a single
    # game land in one or two row groups instead of all of them.
    assert [s.min for s in statistics] == sorted(s.min for s in statistics)


def test_a_filtered_read_returns_only_that_game() -> None:
    week = _week(("g1", 3), ("g2", 4), ("g3", 5))
    table = pq.read_table(
        pa.BufferReader(_serialize(week)), filters=[("game_id", "==", "g2")]
    )
    assert table.num_rows == 4
    assert set(table.column("game_id").to_pylist()) == {"g2"}


def test_a_week_survives_a_parquet_round_trip() -> None:
    """
    The schema a week is written with has to be the schema it reads back as,
    or `merge_weeks` can't concatenate a stored week with a new one. Parquet
    is what makes this non-obvious: it has no second-resolution timestamp and
    widens one to milliseconds on the way in.
    """
    week = _week(("g1", 3))
    read_back = pq.read_table(pa.BufferReader(_serialize(week)))
    assert read_back.schema.equals(PLAY_SCHEMA)
    assert merge_weeks(read_back, _week(("g2", 2))).num_rows == 5


def test_conform_fills_in_a_column_written_before_it_existed() -> None:
    week = _week(("g1", 2))
    older = week.drop_columns(["yards_after_catch"])

    conformed = conform_to_schema(older)

    assert conformed.schema.equals(PLAY_SCHEMA)
    assert conformed.column("yards_after_catch").null_count == 2
    assert conformed.column("game_id").to_pylist() == ["g1", "g1"]


def test_conform_drops_a_column_the_schema_no_longer_has() -> None:
    week = _week(("g1", 2))
    with_extra = week.append_column("since_removed", pa.array(["x", "y"], pa.string()))
    assert conform_to_schema(with_extra).schema.equals(PLAY_SCHEMA)
