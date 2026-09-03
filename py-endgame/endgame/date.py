from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple


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


def format_dates_param(start: date, end: date) -> str:
    """
    The `dates` value ESPN's scoreboard wants for `start` through `end`,
    both inclusive.

    A single day is sent as one date rather than as a range of one, so the
    common case keeps producing byte-identical requests to the ones this
    codebase has always sent -- same URL, same web-cache key.
    """
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    if start == end:
        return start.strftime("%Y%m%d")
    return f"{start:%Y%m%d}-{end:%Y%m%d}"


def chunk_date_range(start: date, end: date, max_days: int) -> List[Tuple[date, date]]:
    """
    Split `start`..`end` (inclusive) into consecutive spans of at most
    `max_days` days each.

    A whole season asked for in one request runs into ESPN's cap on how many
    events it will return -- see `espn_odds.ODDS_PAGE_LIMIT` -- so a caller
    that wants a long stretch asks for it a chunk at a time.
    """
    if max_days < 1:
        raise ValueError(f"max_days must be at least 1, got {max_days}")
    chunks = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=max_days - 1), end)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)
    return chunks


def clamp_to_window(
    start: date,
    end: date,
    month_day_start: Tuple[int, int],
    month_day_end: Tuple[int, int],
) -> Optional[Tuple[date, date]]:
    """
    Narrow `start`..`end` to the part of it inside a (month, day) window,
    or None if none of it is.

    Used to keep a long odds range from asking a competition about days it
    can't have been played on -- the NCAA tournament in December, say.

    Returns the first and last matching day, so a range long enough to
    contain the window twice comes back spanning the gap between them. That
    costs a few requests that find nothing, never a day that's dropped, and
    a range that long is a season's worth of odds asked for at once, which
    nothing here does.
    """
    days = [
        day
        for day in date_range(start, end + timedelta(days=1))
        if is_between_dates(day, month_day_start, month_day_end)
    ]
    if not days:
        return None
    return days[0], days[-1]
