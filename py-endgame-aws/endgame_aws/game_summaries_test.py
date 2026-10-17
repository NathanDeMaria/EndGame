import gzip
import json
from contextlib import ExitStack, asynccontextmanager
from typing import Any, AsyncGenerator, AsyncIterator
from unittest.mock import AsyncMock, patch

from endgame.ncaabb import NcaabbGender

from . import cli
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


def _row(game_id: str, player_id: str, points: int | None = None) -> FlattenedBoxScore:
    row = FlattenedBoxScore.from_dict(
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
    row.points = points
    return row


def test_a_game_pulled_again_replaces_the_rows_of_the_players_in_it() -> None:
    so_far = [_row("g1", "p1", 10), _row("g2", "p3", 30)]
    pulled = [_row("g1", "p1", 11)]
    merged = _merge_box_scores(so_far, pulled)
    assert [(r.game_id, r.player_id, r.points) for r in merged] == [
        ("g2", "p3", 30),
        ("g1", "p1", 11),
    ]


def test_a_player_missing_from_the_new_box_score_keeps_his_row() -> None:
    # ESPN stripped p2's record: his entry has no id and no stats now.
    so_far = [_row("g1", "p1", 10), _row("g1", "p2", 20)]
    merged = _merge_box_scores(so_far, [_row("g1", "p1", 10)])
    assert [(r.game_id, r.player_id) for r in merged] == [("g1", "p2"), ("g1", "p1")]


def test_a_player_with_a_new_id_is_kept_once() -> None:
    old = _row("g1", "p2", 20)
    new = _row("g1", "p9", 20)
    old.short_name = new.short_name = "amaya-oliver"
    merged = _merge_box_scores([_row("g1", "p1"), old], [_row("g1", "p1"), new])
    assert [(r.game_id, r.player_id) for r in merged] == [("g1", "p1"), ("g1", "p9")]


def test_a_game_with_no_box_score_now_keeps_its_old_rows() -> None:
    # ESPN serves the summary with everyone marked didNotPlay, so nothing
    # was pulled for g1: its stored stats are the only copy.
    so_far = [_row("g1", "p1"), _row("g2", "p2")]
    merged = _merge_box_scores(so_far, [_row("g2", "p2")])
    assert [(r.game_id, r.player_id) for r in merged] == [("g1", "p1"), ("g2", "p2")]


def test_a_player_stored_twice_in_a_game_is_kept_once() -> None:
    so_far = [_row("g1", "p1"), _row("g1", "p2"), _row("g1", "p1")]
    merged = _merge_box_scores(so_far, [])
    assert [(r.game_id, r.player_id) for r in merged] == [("g1", "p1"), ("g1", "p2")]


def _summary_of(*rows: FlattenedBoxScore) -> dict:
    return {"box": [row.to_dict() for row in rows]}


def _box_score_in(_game_id: str, summary: dict) -> list[FlattenedBoxScore] | None:
    rows = summary.get("box")
    return [FlattenedBoxScore.from_dict(row) for row in rows] if rows else None


class _Run:
    """
    The box score commands against a fake store and file: what was stored
    before, what ESPN's summaries say now, and what got written.
    """

    def __init__(
        self,
        so_far: list[FlattenedBoxScore],
        summaries_now: list[tuple[str, dict]],
        stored: dict[str, dict] | None = None,
    ) -> None:
        self.so_far = so_far
        self.summaries_now = summaries_now
        self.store = GameSummaryStore(_FakeS3(), "bucket")
        self.stored = stored or {}
        self.skipped: set[str] | None = None
        self.written: list[dict] | None = None

    async def __aenter__(self) -> "_Run":
        for game_id, summary in self.stored.items():
            await self.store.save(summary, "mens", 2011, game_id)
        store = self.store

        @asynccontextmanager
        async def _store() -> AsyncGenerator[GameSummaryStore]:
            yield store

        async def _summaries(
            _season: Any, _gender: Any, skip: set[str]
        ) -> AsyncIterator[tuple[str, dict]]:
            self.skipped = set(skip)
            for pair in self.summaries_now:
                yield pair

        async def _write(rows: list[dict], _bucket: str, _key: str) -> None:
            self.written = rows

        never = AsyncMock(side_effect=AssertionError("not for a box score refresh"))
        self._stack = ExitStack()
        for name, value in [
            ("_load_season", AsyncMock(return_value=object())),
            ("_load_box_scores", AsyncMock(return_value=self.so_far)),
            ("get_game_summary_store", _store),
            ("get_season_summaries", _summaries),
            # A summary here is {"box": rows as dicts}, or {} for one with
            # no box score.
            ("parse_box_score", _box_score_in),
            ("_flatten", lambda rows: rows),
            ("save_csv_to_s3", _write),
            ("get_ncaabb_season", never),
            ("save_to_s3", never),
            ("_load_possessions", never),
        ]:
            self._stack.enter_context(patch.object(cli, name, value))
        return self

    async def __aexit__(self, *_: object) -> None:
        self._stack.close()

    def rows(self) -> list[tuple[str, str, int | None]]:
        assert self.written is not None
        return [(r["game_id"], r["player_id"], r["points"]) for r in self.written]


async def test_a_refresh_rewrites_only_games_with_a_box_score_now() -> None:
    so_far = [_row("g1", "p1", 10), _row("g2", "p2", 20), _row("g3", "p3", 30)]
    # g2: ESPN now marks everyone didNotPlay. g3: no longer in the season.
    now = [("g1", _summary_of(_row("g1", "p1", 11))), ("g2", {})]
    async with _Run(so_far, now) as run:
        await cli.refresh_box_scores("mens", 2011)
    assert run.skipped == set()
    assert run.rows() == [("g2", "p2", 20), ("g3", "p3", 30), ("g1", "p1", 11)]
    assert await run.store.stored_game_ids("mens", 2011) == {"g1", "g2"}


async def test_a_refresh_leaves_the_season_and_possessions_alone() -> None:
    # `_Run` fails the test if the season is pulled or saved, or the
    # possessions read; this is that check with nothing else going on.
    async with _Run([_row("g1", "p1", 10)], []) as run:
        await cli.refresh_box_scores("mens", 2011)
    assert run.rows() == [("g1", "p1", 10)]


async def test_a_summary_stored_but_not_in_the_file_is_read_back_not_fetched() -> None:
    # A run stored g1's summary and died before writing the file.
    stored = {"g1": _summary_of(_row("g1", "p1", 10)), "g2": {}}
    async with _Run([_row("g2", "p2", 20)], [], stored) as run:
        await cli._pull_box_scores(
            object(),  # ty: ignore[invalid-argument-type]
            NcaabbGender.mens,
            2011,
            refresh=False,
        )
    assert run.skipped == {"g1", "g2"}
    assert run.rows() == [("g2", "p2", 20), ("g1", "p1", 10)]
