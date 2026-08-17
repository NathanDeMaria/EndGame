from datetime import date, datetime, timedelta, timezone
from typing import List, Tuple


def date_range(start: date, end: date) -> List[date]:
    """
    Every day from `start` up to (but not including) `end`.
    """
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(days)]


def is_between_dates(
    day: date, month_day_start: Tuple[int, int], month_day_end: Tuple[int, int]
) -> bool:
    """
    Checks if a given date is between a start and end date (inclusive).
    Handles ranges that wrap around the year end (e.g. start > end).
    """
    day_tuple = (day.month, day.day)

    if month_day_start <= month_day_end:
        # Standard range within the same year
        return month_day_start <= day_tuple <= month_day_end
    # Range wraps around the new year (e.g. Nov to Mar)
    # It's in the range if it's after the start date (late in the year)
    # OR before the end date (early in the year)
    return day_tuple >= month_day_start or day_tuple <= month_day_end


def get_end_year(season_end: Tuple[int, int]) -> int:
    """
    Given the (month, day) end of a season,
    find the last year that it makes sense to try to get games for.

    NOTE: this assumes that these are seasons that span
    parts of two calendar years, and gives the first.
    EX: if the most recent NFL season is 2019-2020,
    this will return 2019.
    """
    now = datetime.now(timezone.utc)
    return now.year - 1 if (now.month, now.day) < season_end else now.year
