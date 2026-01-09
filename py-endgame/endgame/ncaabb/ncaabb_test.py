from datetime import date
import pytest

from .ncaabb import is_between_dates


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
