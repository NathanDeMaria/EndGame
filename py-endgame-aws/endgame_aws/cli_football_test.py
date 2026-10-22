from datetime import UTC, datetime, timedelta

from endgame.types import Game, Season, Week

from .cli import _merge_pulled, _plays_to_pull, file_into_weeks, recent_days

NOW = datetime(2026, 10, 11, 13, 0, tzinfo=UTC)


def _game(game_id: str, hours_ago: float, completed: bool = True) -> Game:
    return Game(
        home="Home",
        home_score=21,
        away="Away",
        away_score=14,
        neutral_site=False,
        completed=completed,
        date=NOW - timedelta(hours=hours_ago),
        game_id=game_id,
    )


def test_by_default_only_finished_games_not_stored_are_pulled() -> None:
    games = [_game("stored", 10), _game("new", 10), _game("live", 1, completed=False)]
    assert _plays_to_pull(games, {"stored"}, None) == ["new"]


def test_a_refresh_re_pulls_recent_finished_games_even_when_stored() -> None:
    games = [
        _game("last-night", 10),
        _game("last-week", 24 * 7),
        _game("live", 1, completed=False),
        _game("new-old", 24 * 7),
    ]
    stored = {"last-night", "last-week"}
    refresh_after = NOW - timedelta(hours=36)
    assert _plays_to_pull(games, stored, refresh_after) == ["last-night", "new-old"]


def test_a_naive_kickoff_is_read_as_utc() -> None:
    naive = _game("naive", 10)._replace(
        date=(NOW - timedelta(hours=10)).replace(tzinfo=None)
    )
    assert _plays_to_pull([naive], {"naive"}, NOW - timedelta(hours=36)) == ["naive"]


def test_a_re_pull_replaces_the_stored_copy_in_place() -> None:
    stored = [
        {"game_id": "a", "drives": ["old"]},
        {"game_id": "b", "drives": ["kept"]},
    ]
    merged = _merge_pulled(stored, {"a": ["new"], "c": ["first"]})
    assert merged == [
        {"game_id": "a", "drives": ["new"]},
        {"game_id": "b", "drives": ["kept"]},
        {"game_id": "c", "drives": ["first"]},
    ]


def test_an_empty_re_pull_keeps_the_plays_it_already_had() -> None:
    stored = [{"game_id": "a", "drives": ["old"]}, {"game_id": "b", "drives": []}]
    merged = _merge_pulled(stored, {"a": [], "b": []})
    assert merged == [
        {"game_id": "a", "drives": ["old"]},
        {"game_id": "b", "drives": []},
    ]


def test_a_recent_pull_reads_the_eastern_days_the_window_spans() -> None:
    # 13:00Z is 09:00 ET on the 11th; twelve hours back is 21:00 ET on the 10th.
    assert recent_days(NOW - timedelta(hours=12), NOW) == ["20261010", "20261011"]
    assert recent_days(NOW - timedelta(hours=2), NOW) == ["20261011"]


def test_a_days_games_go_under_the_weeks_the_store_has_them_in() -> None:
    stored = Season(
        [
            Week([_game("tonight", 3, completed=False), _game("saturday", 72)], 8),
            # The post-season's one source week, filed after the regular's.
            Week([_game("bowl", 5, completed=False)], 17),
        ],
        2026,
    )
    fetched = [
        _game("tonight", 3),
        # A stale copy of a game a request has already seen finish.
        _game("tonight", 3, completed=False),
        _game("bowl", 5),
        _game("added-since", 2),
    ]
    weeks, unknown = file_into_weeks(stored, fetched)
    assert [(w.number, [g.game_id for g in w.games]) for w in weeks] == [
        (8, ["tonight"]),
        (17, ["bowl"]),
    ]
    assert all(g.completed for w in weeks for g in w.games)
    assert unknown == 1
