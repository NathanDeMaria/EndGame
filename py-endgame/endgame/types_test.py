from datetime import datetime, timezone

import pytest

from .types import (
    Game,
    OverlappingWeeksError,
    Season,
    Week,
    check_weeks_dont_overlap,
    iter_weeks,
)


def _game(day: int, game_id: str = "1", month: int = 11, year: int = 2023) -> Game:
    return Game(
        home="Home",
        home_score=1,
        away="Away",
        away_score=0,
        neutral_site=False,
        completed=True,
        date=datetime(year, month, day, tzinfo=timezone.utc),
        game_id=game_id,
    )


def test_games_in_order() -> None:
    late, early = _game(9, "late"), _game(6, "early")
    week = Week([late, early], 1)

    assert [g.game_id for g in week.games_in_order] == ["early", "late"]
    # The raw list is left alone
    assert [g.game_id for g in week.games] == ["late", "early"]


def test_week_start_and_end() -> None:
    week = Week([_game(9), _game(6), _game(7)], 1)
    assert week.start == datetime(2023, 11, 6, tzinfo=timezone.utc)
    assert week.end == datetime(2023, 11, 9, tzinfo=timezone.utc)


def test_empty_week_has_no_start_or_end() -> None:
    week = Week([], 1)
    assert week.start is None
    assert week.end is None


def test_weeks_in_order() -> None:
    first = Week([_game(6)], 1)
    second = Week([_game(13)], 2)
    season = Season([second, first], 2023)

    assert [w.number for w in season.weeks_in_order] == [1, 2]


def test_weeks_in_order_ignores_week_number() -> None:
    """Sorting is on game dates, since the numbering itself isn't trustworthy."""
    earlier_but_numbered_later = Week([_game(6)], 99)
    later_but_numbered_earlier = Week([_game(13)], 2)
    season = Season([later_but_numbered_earlier, earlier_but_numbered_later], 2023)

    assert [w.number for w in season.weeks_in_order] == [99, 2]


def test_weeks_in_order_puts_empty_weeks_last() -> None:
    empty = Week([], 1)
    dated = Week([_game(6)], 2)
    season = Season([empty, dated], 2023)

    assert [w.number for w in season.weeks_in_order] == [2, 1]


def test_check_weeks_dont_overlap__ok() -> None:
    weeks = [Week([_game(6)], 1), Week([_game(13)], 2)]
    check_weeks_dont_overlap(weeks)


def test_check_weeks_dont_overlap__empty_weeks_ignored() -> None:
    weeks = [Week([_game(6)], 1), Week([], 2), Week([_game(13)], 3)]
    check_weeks_dont_overlap(weeks)


def test_check_weeks_dont_overlap__raises() -> None:
    # The shape the ncaabb incremental merge produces: a week that got
    # games from both ends of the season merged into it.
    spans_season = Week([_game(6), _game(20, month=3, year=2024)], 1)
    normal = Week([_game(13)], 2)

    with pytest.raises(OverlappingWeeksError, match="Week 1"):
        check_weeks_dont_overlap([spans_season, normal])


def test_iter_weeks() -> None:
    first = Week([_game(6)], 1)
    second = Week([_game(13)], 2)
    season = Season([second, first], 2023)

    assert [w.number for w in iter_weeks(season)] == [1, 2]


def test_iter_weeks_validates_eagerly() -> None:
    """The check runs on call, not on first iteration."""
    season = Season(
        [Week([_game(6), _game(20, month=3, year=2024)], 1), Week([_game(13)], 2)], 2023
    )

    with pytest.raises(OverlappingWeeksError):
        iter_weeks(season)


def test_iter_weeks_can_skip_validation() -> None:
    season = Season(
        [Week([_game(6), _game(20, month=3, year=2024)], 1), Week([_game(13)], 2)], 2023
    )

    assert len(list(iter_weeks(season, validate=False))) == 2
