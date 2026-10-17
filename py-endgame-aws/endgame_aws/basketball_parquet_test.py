from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq

from .basketball_parquet import build_day_key, conform_to_basketball_schema, season_of
from .basketball_transform import BASKETBALL_PLAY_SCHEMA, transform_day_to_table
from .pbp_parquet import _serialize


def test_key_is_hive_partitioned_by_season_and_date() -> None:
    assert build_day_key("womens", 2025, date(2026, 1, 21)) == (
        "processed/basketball_plays/league=womens/season=2025/date=2026-01-21"
        "/data.parquet"
    )


def test_a_season_is_named_for_the_year_it_starts_in() -> None:
    assert season_of(date(2025, 11, 3)) == 2025
    assert season_of(date(2026, 4, 6)) == 2025
    assert season_of(date(2026, 11, 3)) == 2026


def test_a_day_round_trips_through_parquet() -> None:
    raw = [
        {
            "game_id": "g1",
            "plays": [
                {"id": "p1", "text": "Start game"},
                {"id": "p2", "text": "x", "participants": [{"id": "1"}, {"id": "2"}]},
            ],
        }
    ]
    table = transform_day_to_table(raw, "mens", 2025, date(2026, 3, 1))
    read = pq.read_table(pa.BufferReader(_serialize(table)))
    assert conform_to_basketball_schema(read).equals(table)


def test_a_column_the_file_predates_comes_back_null() -> None:
    table = transform_day_to_table(
        [{"game_id": "g1", "plays": [{"id": "p1"}]}], "mens", 2025, date(2026, 3, 1)
    )
    older = table.drop_columns(["shot_x"])
    conformed = conform_to_basketball_schema(older)
    assert conformed.schema.equals(BASKETBALL_PLAY_SCHEMA)
    assert conformed.column("shot_x").to_pylist() == [None]
