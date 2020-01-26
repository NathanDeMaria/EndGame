from datetime import datetime
from typing import Tuple


def get_end_year(season_end: Tuple[int, int]) -> int:
    """
    Given the (month, day) end of a season,
    find the last year that it makes sense to try to get games for.

    NOTE: this assumes that these are seasons that span
    parts of two calendar years, and gives the first.
    EX: if the most recent NFL season is 2019-2020,
    this will return 2019.
    """
    now = datetime.utcnow()
    return now.year - 1 if (now.month, now.day) < season_end else now.year
