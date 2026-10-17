import gzip
import json

from .cli import _merge_box_scores
from .io import FlattenedBoxScore
from .stores import GameSummaryStore


class _FakeS3:
    """Just enough of an aiobotocore client for the summary store."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_object(self, Bucket: str, Key: str, Body: bytes) -> None:
        self.objects[Key] = Body

    async def get_object(self, Bucket: str, Key: str) -> dict:
        body = self.objects[Key]

        class _Stream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def read(self) -> bytes:
                return body

        return {"Body": _Stream()}

    def get_paginator(self, _name: str):
        objects = self.objects

        class _Paginator:
            async def paginate(self, Bucket: str, Prefix: str):
                yield {
                    "Contents": [{"Key": k} for k in objects if k.startswith(Prefix)]
                }

        return _Paginator()


async def test_a_summary_is_one_gzipped_object_per_game() -> None:
    s3 = _FakeS3()
    store = GameSummaryStore(s3, "bucket")
    await store.save({"boxscore": {}}, "mens", 2025, "401825417")
    assert list(s3.objects) == ["summaries/ncaabb/mens/2025/401825417.json.gz"]
    body = s3.objects["summaries/ncaabb/mens/2025/401825417.json.gz"]
    assert json.loads(gzip.decompress(body)) == {"boxscore": {}}
    assert await store.load("mens", 2025, "401825417") == {"boxscore": {}}


async def test_the_stored_games_are_read_off_one_listing_of_the_season() -> None:
    s3 = _FakeS3()
    store = GameSummaryStore(s3, "bucket")
    for league, season, game in [
        ("mens", 2025, "a"),
        ("mens", 2025, "b"),
        ("mens", 2024, "c"),
        ("womens", 2025, "d"),
    ]:
        await store.save({}, league, season, game)
    assert await store.stored_game_ids("mens", 2025) == {"a", "b"}


def _row(game_id: str, player_id: str) -> FlattenedBoxScore:
    return FlattenedBoxScore.from_dict(
        {
            "player_id": player_id,
            "short_name": player_id,
            "game_id": game_id,
            "team_id": "t",
            **{
                field: None
                for field in [
                    "minutes_played",
                    "field_goal_makes",
                    "field_goal_attempts",
                    "three_point_makes",
                    "three_point_attempts",
                    "free_throw_makes",
                    "free_throw_attempts",
                    "offensive_rebounds",
                    "defensive_rebounds",
                    "rebounds",
                    "assists",
                    "steals",
                    "blocks",
                    "turnovers",
                    "fouls",
                    "points",
                ]
            },
        }
    )


def test_a_game_pulled_again_replaces_its_old_rows_whole() -> None:
    so_far = [_row("g1", "p1"), _row("g1", "p2"), _row("g2", "p3")]
    pulled = [_row("g1", "p1")]
    merged = _merge_box_scores(so_far, pulled, {"g1"})
    assert [(r.game_id, r.player_id) for r in merged] == [("g2", "p3"), ("g1", "p1")]
