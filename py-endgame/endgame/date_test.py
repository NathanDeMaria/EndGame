from datetime import date

import pytest

from .date import chunk_date_range, clamp_to_window, format_dates_param


def test_format_dates_param__single_day_is_not_a_range() -> None:
    """
    A one-day pull has to keep sending exactly what it always sent, so the
    request (and the web cache key built from it) doesn't change.
    """
    assert format_dates_param(date(2026, 9, 3), date(2026, 9, 3)) == "20260903"


def test_format_dates_param__range() -> None:
    assert (
        format_dates_param(date(2026, 9, 3), date(2026, 9, 17)) == "20260903-20260917"
    )


def test_format_dates_param__backwards_range_is_an_error() -> None:
    with pytest.raises(ValueError):
        format_dates_param(date(2026, 9, 17), date(2026, 9, 3))


@pytest.mark.parametrize(
    "start,end,max_days,expected",
    [
        # A span shorter than the chunk is one chunk
        (
            date(2026, 1, 1),
            date(2026, 1, 5),
            14,
            [(date(2026, 1, 1), date(2026, 1, 5))],
        ),
        # A single day is one chunk of one day
        (
            date(2026, 1, 1),
            date(2026, 1, 1),
            14,
            [(date(2026, 1, 1), date(2026, 1, 1))],
        ),
        # Exactly one chunk long
        (
            date(2026, 1, 1),
            date(2026, 1, 14),
            14,
            [(date(2026, 1, 1), date(2026, 1, 14))],
        ),
        # One day over, so the remainder gets its own chunk
        (
            date(2026, 1, 1),
            date(2026, 1, 15),
            14,
            [
                (date(2026, 1, 1), date(2026, 1, 14)),
                (date(2026, 1, 15), date(2026, 1, 15)),
            ],
        ),
    ],
)
def test_chunk_date_range(start, end, max_days, expected) -> None:
    assert chunk_date_range(start, end, max_days) == expected


def test_chunk_date_range__chunks_are_disjoint_and_cover_everything() -> None:
    start, end = date(2026, 11, 1), date(2027, 4, 30)
    chunks = chunk_date_range(start, end, 14)

    # Every chunk picks up the day after the last one ended: no day is asked
    # for twice (which would report the same game's odds twice) and none is
    # skipped.
    assert chunks[0][0] == start
    assert chunks[-1][1] == end
    for (_, previous_end), (next_start, _) in zip(chunks, chunks[1:]):
        assert (next_start - previous_end).days == 1


def test_chunk_date_range__rejects_a_zero_length_chunk() -> None:
    """Otherwise it would loop forever making no progress."""
    with pytest.raises(ValueError):
        chunk_date_range(date(2026, 1, 1), date(2026, 2, 1), 0)


def test_clamp_to_window__narrows_to_the_window() -> None:
    """The NCAA tournament can't be played in December, so don't ask."""
    assert clamp_to_window(date(2026, 12, 1), date(2027, 4, 30), (3, 1), (4, 30)) == (
        date(2027, 3, 1),
        date(2027, 4, 30),
    )


def test_clamp_to_window__keeps_a_range_already_inside() -> None:
    assert clamp_to_window(date(2027, 3, 10), date(2027, 3, 24), (3, 1), (4, 30)) == (
        date(2027, 3, 10),
        date(2027, 3, 24),
    )


def test_clamp_to_window__handles_a_window_that_wraps_the_year() -> None:
    """The regular season runs November to April, across New Year."""
    assert clamp_to_window(date(2026, 9, 3), date(2027, 4, 30), (11, 1), (4, 1)) == (
        date(2026, 11, 1),
        date(2027, 4, 1),
    )


def test_clamp_to_window__none_when_the_range_misses_it() -> None:
    assert clamp_to_window(date(2026, 7, 1), date(2026, 8, 1), (11, 1), (4, 1)) is None
