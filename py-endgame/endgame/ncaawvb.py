"""
NCAA women's college volleyball games, pulled a day at a time.
"""

from datetime import date
from typing import List, Optional

from .constants import ESPN_SPORTS_API_BASE
from .daily import DailyLeague, get_season, get_seasons
from .daily import update as update_daily
from .season_cache import SeasonCache
from .types import Season

SCOREBOARD = f"{ESPN_SPORTS_API_BASE}/volleyball/womens-college-volleyball/scoreboard"

# The whole season sits inside one calendar year: the first matches are in
# the last week of August, and the national championship is played the week
# before Christmas. ESPN's own scoreboard calendar for a season runs
# 08-01 to 12-31, and the window here is the same, so the days either side
# of the real season come back empty rather than being guessed at.
SEASON_START = (8, 1)
SEASON_END = (12, 31)

# COVID moved the 2020 season out of the window entirely, and by months
# rather than days, so it can't be reached by widening the one above -- see
# `DailyLeague.odd_seasons`. Play started in September 2020, stopped for
# December, and most conferences ran their schedule in the spring; the
# championship was played in Omaha on April 25th 2021.
#
# ESPN files all 785 of those matches under season year 2020, spring ones
# included, and the 2021 season opens in August 2021, so nothing here
# overlaps the season after it.
ODD_SEASONS = {
    2020: (date(2020, 8, 1), date(2021, 5, 1)),
}

# Games played as part of a named event rather than as a standalone
# fixture: the early-season invitationals (the Ameritas Players Challenge,
# the Oregon Invitational) and every round of the NCAA championship.
#
# It has to be allowed, and that's worth being explicit about, because the
# same abbreviation is an *exhibition* for the NHL -- it's what the 4
# Nations Face-Off came back as. Here it's ~30% of every season and the
# entire postseason, so dropping it would throw away the tournament.
_TOURNAMENT = "QRR"

# What ESPN sends instead of a competition type for 2011 through 2014,
# where nothing is classified at all. It's the only value those four
# seasons have, so leaving it out drops them outright rather than trimming
# them.
#
# It's a literal "N/A" string in the response, not a missing field, so an
# event with no competition block still fails the check the way
# `league_play_filter` intends.
_UNCLASSIFIED = "N/A"


NCAAWVB = DailyLeague(
    name="ncaawvb",
    scoreboard_url=SCOREBOARD,
    season_start=SEASON_START,
    season_end=SEASON_END,
    # August to December, so a season starts and finishes in the same year
    end_year_offset=0,
    # ESPN's volleyball coverage starts here: 2010 and earlier come back
    # empty for every day of the season. Note that "starts" isn't "is
    # complete" -- 2012 and 2013 hold about 90 matches each, against ~1,500
    # in 2024, so the early seasons are a thin sample of the sport rather
    # than a record of it. Nothing downstream is obliged to rate them.
    first_year=2011,
    # A match is won by taking three sets, so no result can finish 0-0 and
    # a scoreless "completed" game is ESPN handing back something bogus.
    drop_scoreless=True,
    odd_seasons=ODD_SEASONS,
    # ESPN stopped tagging the NCAA tournament as the postseason after
    # 2016: from 2017 on, the championship comes back under the *regular*
    # season with a competition type of "QRR". So the postseason arriving
    # under season type 3 (2011-2016) and under type 2 (2017 on) both have
    # to be kept, which is why the tournament abbreviation is allowed here
    # rather than left to `league_play_filter`'s season-type check.
    #
    # Unlike the NHL and the WNBA, none of this risks admitting a team that
    # isn't in the league: every one of the 359 sides ESPN has served since
    # 2011 is a college, mostly D1 with the occasional D2 or NAIA school
    # playing a D1 opponent. There are no national or club teams in the
    # feed to guard against.
    regular_season_competitions=frozenset({"STD", _TOURNAMENT, _UNCLASSIFIED}),
    # No `rename_team`: ESPN back-fills a program's current name across its
    # whole history here -- a 2011 match already comes back as "IU
    # Indianapolis Jaguars", a name the school only took in 2024. The four
    # teams that stop appearing mid-history are programs that left D1
    # (Savannah State, Hartford, La Salle, Wayne State), not renames.
    #
    # No odds either, which is why there's no `get_ncaawvb_odds` beside the
    # season calls below: not one of the ~12,700 matches ESPN has served
    # since 2011 carries an odds block. `DailyLeague` would happily fetch
    # them, but a scheduled job for it would write an empty file a day.
)


async def update(location: str = "ncaawvb.csv") -> None:
    """
    Update the ncaawvb.csv
    """
    await update_daily(NCAAWVB, location)


async def get_ncaawvb_seasons() -> List[Season]:
    """
    Get every NCAA women's volleyball season
    """
    return await get_seasons(NCAAWVB)


async def get_ncaawvb_season(
    year: int,
    season_so_far: Optional[Season] = None,
    season_cache: Optional[SeasonCache] = None,
    *,
    include_unplayed: bool = False,
) -> Season:
    """
    Get an NCAA women's volleyball season

    `include_unplayed` carries the fixtures as well as the results -- see
    `daily.get_season`.
    """
    return await get_season(
        NCAAWVB, year, season_so_far, season_cache, include_unplayed=include_unplayed
    )
