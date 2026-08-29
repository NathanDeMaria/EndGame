"""
WNBA games and odds, pulled a day at a time.
"""

from datetime import date
from typing import AsyncIterator, List, Optional

from .constants import ESPN_SPORTS_API_BASE
from .daily import DailyLeague, get_daily_odds, get_season, get_seasons
from .daily import update as update_daily
from .espn_odds import Odds
from .season_cache import SeasonCache
from .types import Season

SCOREBOARD = f"{ESPN_SPORTS_API_BASE}/basketball/wnba/scoreboard"

# Unlike every other league here, a WNBA season starts and finishes inside
# one calendar year: May to a Finals that's run as late as October.
SEASON_START = (5, 1)
SEASON_END = (10, 31)


def _rename_team(name: str) -> str:
    """
    Collapse a franchise's old names onto its current one, so a team is one
    team across seasons.
    """
    return _RENAMES.get(name, name)


_RENAMES = {
    # Detroit -> Tulsa in 2010 -> Dallas in 2016
    "Detroit Shock": "Dallas Wings",
    "Tulsa Shock": "Dallas Wings",
    # Utah -> San Antonio in 2003, renamed in 2014, Las Vegas in 2018
    "Utah Starzz": "Las Vegas Aces",
    "San Antonio Silver Stars": "Las Vegas Aces",
    "San Antonio Stars": "Las Vegas Aces",
    # Orlando -> Connecticut in 2003
    "Orlando Miracle": "Connecticut Sun",
}


WNBA = DailyLeague(
    name="wnba",
    scoreboard_url=SCOREBOARD,
    season_start=SEASON_START,
    season_end=SEASON_END,
    # The whole season is inside the year it's named for
    end_year_offset=0,
    first_year=2002,
    # Basketball can't finish 0-0, so a scoreless "completed" game is ESPN
    # handing back something bogus -- same as NCAABB.
    drop_scoreless=True,
    rename_team=_rename_team,
    # "CC" is the Commissioner's Cup final: one game a year, played by two
    # real teams for something, so it counts as league play even though it
    # sits outside the standings. The All-Star game, which shares its
    # week, does not -- that one is "ALLSTAR" and gets dropped.
    regular_season_competitions=frozenset({"STD", "CC"}),
    # The 2002 All-Star game, EAST v WEST on July 15th. ESPN tagged every
    # All-Star game from 2003 on, and both leagues', but filed this one as
    # an ordinary regular-season game -- so it's the single game in either
    # league's history that no rule can tell from league play, and without
    # it "EAST" and "WEST" end up rated off one game apiece.
    untagged_exhibitions=frozenset({"220715098"}),
)


async def update(location: str = "wnba.csv") -> None:
    """
    Update the wnba.csv
    """
    await update_daily(WNBA, location)


async def get_wnba_seasons() -> List[Season]:
    """
    Get every WNBA season
    """
    return await get_seasons(WNBA)


async def get_wnba_season(
    year: int,
    season_so_far: Optional[Season] = None,
    season_cache: Optional[SeasonCache] = None,
) -> Season:
    """
    Get a WNBA season
    """
    return await get_season(WNBA, year, season_so_far, season_cache)


async def get_wnba_odds(day: date) -> AsyncIterator[Odds]:
    """
    Get the odds on a day's WNBA games
    """
    async for odd in get_daily_odds(WNBA, day):
        yield odd
