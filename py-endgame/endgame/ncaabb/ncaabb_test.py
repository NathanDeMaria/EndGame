from datetime import date, datetime
import pytest

from .ncaabb import (
    is_between_dates,
    Game,
    Season,
    Week,
    merge_seasons,
    DayParams,
    NcaabbGender,
    NcaabbGroup,
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
    g1 = Game("A", 10, "B", 5, False, True, datetime(year, 1, 1), "1")
    g2 = Game("C", 20, "D", 15, False, True, datetime(year, 1, 1), "2")

    # Same ID as g1, but different score (updated)
    updated_home_score = 12
    g1_updated = Game(
        "A", updated_home_score, "B", 5, False, True, datetime(year, 1, 1), "1"
    )

    g3 = Game("E", 30, "F", 25, False, True, datetime(year, 1, 8), "3")

    day_params = [DayParams(date(year, 1, 1), NcaabbGender.mens, NcaabbGroup.d1)]
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
