from datetime import date

from .basketball_transform import BASKETBALL_PLAY_SCHEMA, transform_day_to_table

DAY = date(2026, 3, 1)


def _shot(play_id: str, **overrides) -> dict:
    """A made jumper as the page sends it, trimmed to the fields read."""
    play = {
        "id": play_id,
        "period": {"number": 1},
        "clock": {"value": 1146, "displayValue": "19:06"},
        "text": "Trey Kaufman-Renn makes 6-foot jumper (Braden Smith assists)",
        "homeAway": "away",
        "type": {"categoryId": "1002", "id": "558", "txt": "JumpShot"},
        "awScr": 2,
        "hmScr": 0,
        "athlete": {"id": "4897438", "name": "Trey Kaufman-Renn"},
        "participants": [{"id": "4897438"}, {"id": "5105854"}],
        "scoringPlay": True,
        "shootingPlay": True,
        "pointsAttempted": 2,
        "coordinate": {"x": 19, "y": 3},
        # Riding along on every play, and nothing the table keeps
        "favoredTeam": {"logo": "https://a.espncdn.com/...", "winProbability": 65.5},
    }
    play.update(overrides)
    return play


def _rows(*games: tuple[str, list[dict]]) -> list[dict]:
    raw = [{"game_id": game_id, "plays": plays} for game_id, plays in games]
    return transform_day_to_table(raw, "mens", 2025, DAY).to_pylist()


def test_a_play_is_flattened_into_the_schema() -> None:
    (row,) = _rows(("g1", [_shot("p1")]))
    assert row == {
        "league": "mens",
        "season": 2025,
        "date": DAY,
        "game_id": "g1",
        "play_id": "p1",
        "play_number": 1,
        "period": 1,
        "clock_display": "19:06",
        "clock_seconds": 1146,
        "home_score": 0,
        "away_score": 2,
        "side": "away",
        "play_type_id": "558",
        "play_category_id": "1002",
        "play_type": "JumpShot",
        "text": "Trey Kaufman-Renn makes 6-foot jumper (Braden Smith assists)",
        "athlete_id": "4897438",
        "athlete_name": "Trey Kaufman-Renn",
        "participant_ids": ["4897438", "5105854"],
        "scoring_play": True,
        "shooting_play": True,
        "is_free_throw": None,
        "points_attempted": 2,
        "shot_x": 19,
        "shot_y": 3,
    }


def test_a_postseason_game_listed_twice_is_kept_once() -> None:
    rows = _rows(("g1", [_shot("p1"), _shot("p2")]), ("g1", [_shot("p1"), _shot("p2")]))
    assert [row["play_id"] for row in rows] == ["p1", "p2"]


def test_only_a_shot_keeps_its_coordinates() -> None:
    steal = _shot(
        "p2",
        text="Fletcher Loyer Steal.",
        type={"categoryId": "1022", "id": "607", "txt": "Steal"},
        athlete=None,
        participants=None,
        shootingPlay=False,
        coordinate={"x": 25, "y": 0},
    )
    placeholder = _shot("p3", coordinate={"x": -214748340, "y": -214748365})
    rows = _rows(("g1", [steal, placeholder]))
    assert [(row["shot_x"], row["shot_y"]) for row in rows] == [
        (None, None),
        (None, None),
    ]
    assert rows[0]["athlete_id"] is None
    assert rows[0]["participant_ids"] == []


def test_a_play_for_neither_side_has_no_side() -> None:
    start = _shot("p0", text="Start game", homeAway=None, athlete=None)
    (row,) = _rows(("g1", [start]))
    assert row["side"] is None


def test_the_day_is_sorted_by_game_and_keeps_each_games_order() -> None:
    rows = _rows(("g2", [_shot("b1"), _shot("b2")]), ("g1", [_shot("a1")]))
    assert [(row["game_id"], row["play_number"]) for row in rows] == [
        ("g1", 1),
        ("g2", 1),
        ("g2", 2),
    ]


def test_a_day_without_plays_is_an_empty_table_with_the_schema() -> None:
    table = transform_day_to_table([{"game_id": "g1", "plays": []}], "mens", 2025, DAY)
    assert table.num_rows == 0
    assert table.schema.equals(BASKETBALL_PLAY_SCHEMA)
