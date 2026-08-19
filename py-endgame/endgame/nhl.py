"""
NHL games and odds, pulled a day at a time.
"""

from datetime import date
from typing import AsyncIterator, List, Optional

from .constants import ESPN_SPORTS_API_BASE
from .daily import DailyLeague, get_daily_odds, get_season, get_seasons
from .daily import update as update_daily
from .espn_odds import Odds
from .season_cache import SeasonCache
from .types import Season

SCOREBOARD = f"{ESPN_SPORTS_API_BASE}/hockey/nhl/scoreboard"

# A season is named for the year it starts in: 2019 is the 2019-20 season.
#
# The bounds are deliberately loose -- a couple of weeks either side of the
# usual October-to-June season, since openers and Cup finals move around --
# and days with no games just come back empty.
SEASON_START = (9, 15)
SEASON_END = (7, 15)

# The two seasons COVID moved, which the window above can't stretch to
# reach: 2019's playoffs ran past its end and into the days 2020 would
# otherwise start on, so widening 2019 alone would pull the bubble games
# into both seasons' files. Given as real dates instead.
#
# 2019-20 paused on March 12th 2020 and resumed in the Edmonton and
# Toronto bubbles, ending with game 6 of the final on September 28th.
# 2020-21 then opened late, on January 13th 2021, and finished July 7th.
#
# The 2004-05 lockout (no season at all) and the 2012-13 one (January to
# June, inside the usual window) don't need an entry.
ODD_SEASONS = {
    2019: (date(2019, 9, 15), date(2020, 10, 1)),
    2020: (date(2021, 1, 1), date(2021, 7, 15)),
}


def _rename_team(name: str) -> str:
    """
    Collapse a franchise's old names onto its current one, so a team is one
    team across seasons.
    """
    return _RENAMES.get(name, name)


_RENAMES = {
    # Dropped the "Mighty" in 2006
    "Mighty Ducks of Anaheim": "Anaheim Ducks",
    # Atlanta's franchise moved to Winnipeg in 2011
    "Atlanta Thrashers": "Winnipeg Jets",
    # Phoenix -> Arizona in 2014, then Utah in 2024, renamed again in 2025
    "Phoenix Coyotes": "Utah Mammoth",
    "Arizona Coyotes": "Utah Mammoth",
    "Utah Hockey Club": "Utah Mammoth",
}


NHL = DailyLeague(
    name="nhl",
    scoreboard_url=SCOREBOARD,
    season_start=SEASON_START,
    season_end=SEASON_END,
    # October to June, so a season runs into the next calendar year
    end_year_offset=1,
    first_year=2002,
    # The NHL played to ties until 2005-06, so a 0-0 final is a real result
    # and scoreless games can't be thrown away as bad data.
    drop_scoreless=False,
    rename_team=_rename_team,
    odd_seasons=ODD_SEASONS,
)


async def update(location: str = "nhl.csv") -> None:
    """
    Update the nhl.csv
    """
    await update_daily(NHL, location)


async def get_nhl_seasons() -> List[Season]:
    """
    Get every NHL season
    """
    return await get_seasons(NHL)


async def get_nhl_season(
    year: int,
    season_so_far: Optional[Season] = None,
    season_cache: Optional[SeasonCache] = None,
) -> Season:
    """
    Get an NHL season
    """
    return await get_season(NHL, year, season_so_far, season_cache)


async def get_nhl_odds(day: date) -> AsyncIterator[Odds]:
    """
    Get the odds on a day's NHL games
    """
    async for odd in get_daily_odds(NHL, day):
        yield odd
