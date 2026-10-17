from datetime import date

from .cli import _play_days_to_pull

TODAY = date(2026, 11, 20)


def test_a_morning_run_pulls_yesterday_not_today() -> None:
    stored = {date(2026, 11, d) for d in range(13, 19)}
    assert _play_days_to_pull(TODAY, stored, 7) == [date(2026, 11, 19)]


def test_days_a_failed_run_left_missing_are_pulled_again_oldest_first() -> None:
    stored = {date(2026, 11, 13), date(2026, 11, 14), date(2026, 11, 16)}
    assert _play_days_to_pull(TODAY, stored, 7) == [
        date(2026, 11, 15),
        date(2026, 11, 17),
        date(2026, 11, 18),
        date(2026, 11, 19),
    ]


def test_a_day_past_the_look_back_is_left_alone() -> None:
    assert _play_days_to_pull(TODAY, set(), 2) == [
        date(2026, 11, 18),
        date(2026, 11, 19),
    ]
