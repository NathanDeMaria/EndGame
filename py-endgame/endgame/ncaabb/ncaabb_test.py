from datetime import date, datetime

import pytest

from .ncaabb import (
    DayParams,
    Game,
    NcaabbGender,
    NcaabbGroup,
    Season,
    Week,
    group_games_into_weeks,
    is_between_dates,
    merge_seasons,
)


@pytest.mark.parametrize(
    "test_date, start, end, expected",
    [
        (date(2025, 1, 1), (11, 1), (4, 1), True),
        (date(2025, 6, 6), (11, 1), (4, 1), False),
        (date(2025, 12, 29), (11, 1), (4, 1), True),
        (date(2025, 1, 1), (6, 1), (6, 31), False),
        (date(2025, 6, 6), (6, 1), (6, 31), True),
        (date(2025, 12, 29), (6, 1), (6, 31), False),
    ],
)
def test_is_between_dates(
    test_date: date, start: tuple[int, int], end: tuple[int, int], expected: bool
) -> None:
    assert is_between_dates(test_date, start, end) == expected


def test_merge_seasons() -> None:
    year = 1989
    # Week numbers come from the date now, so these have to be inside the
    # season they're labelled with: the 1989 season starts Nov 1989.
    g1 = Game("A", 10, "B", 5, False, True, datetime(year, 11, 1), "1")
    g2 = Game("C", 20, "D", 15, False, True, datetime(year, 11, 1), "2")

    # Same ID as g1, but different score (updated)
    updated_home_score = 12
    g1_updated = Game(
        "A", updated_home_score, "B", 5, False, True, datetime(year, 11, 1), "1"
    )

    g3 = Game("E", 30, "F", 25, False, True, datetime(year, 11, 8), "3")

    day_params = [DayParams(date(year, 11, 1), NcaabbGender.mens, NcaabbGroup.d1)]
    s1 = Season(
        weeks=[Week([g1, g2], 1)],
        year=year,
        trouble_params=day_params,
    )

    s2 = Season(
        weeks=[Week([g1_updated], 1), Week([g3], 2)], year=year, trouble_params=None
    )

    merged = merge_seasons([s1, s2])

    assert merged.year == year
    assert len(merged.weeks) == 2

    # Check week 1
    w1 = next(w for w in merged.weeks if w.number == 1)
    assert len(w1.games) == 2

    # Should have the updated g1
    merged_g1 = next(g for g in w1.games if g.game_id == "1")
    assert merged_g1.home_score == updated_home_score

    # Should still have g2
    merged_g2 = next(g for g in w1.games if g.game_id == "2")
    assert merged_g2.home_score == 20

    # Check week 2
    w2 = next(w for w in merged.weeks if w.number == 2)
    assert len(w2.games) == 1
    merged_g3 = next(g for g in w2.games if g.game_id == "3")
    assert merged_g3.home_score == 30

    # Check trouble params
    assert set(merged.trouble_params or []) == set(day_params)


def _dated_game(day: datetime, game_id: str) -> Game:
    return Game("A", 10, "B", 5, False, True, day, game_id)


def test_group_games_into_weeks__numbers_from_season_start() -> None:
    year = 1989
    november = _dated_game(datetime(year, 11, 1), "nov")
    march = _dated_game(datetime(year + 1, 3, 7), "mar")

    weeks = group_games_into_weeks([november, march], year)

    assert [w.number for w in weeks] == [1, 19]


def test_group_games_into_weeks__numbering_is_stable_for_a_partial_season() -> None:
    """The bug: an incremental pull only sees recent games and renumbers from 1.

    Grouping a tail of the season has to give those weeks the same numbers
    they'd get if the whole season were grouped at once.
    """
    year = 1989
    early = _dated_game(datetime(year, 11, 1), "early")
    late = _dated_game(datetime(year + 1, 3, 7), "late")

    full = group_games_into_weeks([early, late], year)
    # What a daily run that only fetched March would produce
    partial = group_games_into_weeks([late], year)

    assert [w.number for w in partial] == [w.number for w in full if w.games[0] is late]
    assert partial[0].number == 19


def test_merge_seasons__repairs_positionally_numbered_weeks() -> None:
    """Seasons saved under the old numbering get regrouped, not carried forward.

    The old code numbered by position, so a March-only incremental pull
    produced a "week 1" that merged into November's week 1.
    """
    year = 1989
    november = _dated_game(datetime(year, 11, 1), "nov")
    march = _dated_game(datetime(year + 1, 3, 7), "mar")

    # Both mislabelled week 1, which is exactly the corrupted shape
    saved = Season(weeks=[Week([november, march], 1)], year=year, trouble_params=None)

    merged = merge_seasons([saved])

    assert [w.number for w in merged.weeks] == [1, 19]
    assert [g.game_id for w in merged.weeks for g in w.games] == ["nov", "mar"]
