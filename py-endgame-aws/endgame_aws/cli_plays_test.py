from datetime import UTC, date, datetime, timedelta

from .cli import _basketball_days_to_process, _play_days_to_pull

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


def test_a_season_processes_the_days_its_parquet_is_missing_or_behind() -> None:
    written = datetime(2026, 3, 2, 13, tzinfo=UTC)
    raw = {
        date(2026, 2, 28): written,  # processed after the raw write
        date(2026, 3, 1): written,  # raw re-written since
        date(2026, 3, 2): written,  # never processed
        date(2025, 3, 1): written,  # last season
    }
    processed = {
        date(2026, 2, 28): written + timedelta(minutes=5),
        date(2026, 3, 1): written - timedelta(days=1),
    }
    assert _basketball_days_to_process(raw, processed, 2025) == [
        date(2026, 3, 1),
        date(2026, 3, 2),
    ]
