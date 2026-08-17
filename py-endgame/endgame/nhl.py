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
#
# The one season this doesn't cover is 2019: COVID pushed its playoffs to
# August and September of 2020, past the end here, and into the window the
# 2020 season starts on. Those games land in the 2020 season's file rather
# than 2019's.
SEASON_START = (9, 15)
SEASON_END = (7, 15)


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
