from datetime import datetime, timezone
from itertools import groupby
from logging import getLogger
from typing import AsyncIterator, Iterator, List

import aiohttp

from .async_tools import apply_in_parallel
from .date import get_end_year
from .espn_games import get_games, save_seasons
from .espn_odds import Odds, get_odds
from .season_cache import SeasonCache
from .types import Game, NcaaFbGroup, Season, SeasonType, Week, WeekParams
from .web import RequestParameters

logger = getLogger(__name__)


BASE_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
)
# The bound on the weeks we ask ESPN for, and the offset that keeps
# postseason week numbers from colliding with regular season ones.
N_REGULAR_WEEKS = 16
SEASON_END = (2, 1)
# Week 1 is the Monday-Sunday week containing this day. The earliest game in
# any season we have is 2002-08-22, so every game lands in week 1 or later.
#
# ESPN's own week numbers are kept on the Week objects (they're how you find
# the request a game came from), but they aren't chronological: it mislabels
# the odd game, and the whole postseason comes back as one week that runs
# from the early December bowls into January, concurrently with the FCS/D2/D3
# playoffs sitting in regular season weeks 14-16. So the season is tagged
# with a start and walked as calendar weeks instead.
SEASON_START = (8, 20)
# ESPN files the handful of games that open the season -- the Saturday
# before Labour Day weekend -- under week 0, and only from this season on.
# Before it the regular season starts at week 1, and asking for 0 comes back
# empty or 404s for every division: three trouble weeks added to every
# historical season, which would blunt the signal `trouble_params` is there
# to carry.
#
# Gated by year rather than by "an empty week 0 is fine" so that a week 0
# that *should* have games and doesn't still shows up as trouble.
#
# This year is a starting point, not a fact to trust: `backfill_week_zero
# --dry_run` reports what week 0 adds per season, so lower it and re-run the
# dry run if an earlier year turns out to have any. A dry run costs a
# re-fetch and writes nothing.
FIRST_WEEK_ZERO_SEASON = 2016


async def update(location="ncaaf.csv"):
    """
    Update the NCAAFB data
    """
    end_year = get_end_year(SEASON_END)
    args = [(y,) for y in range(1999, end_year + 1)]
    seasons = [s async for s in apply_in_parallel(get_season, args)]
    save_seasons(seasons, location)


def _week_params(year: int) -> List[WeekParams]:
    """
    Every request a season is made of: each division, each week, plus the
    postseason.

    Week 0 only from `FIRST_WEEK_ZERO_SEASON` on -- see the constant.
    """
    first_week = 0 if year >= FIRST_WEEK_ZERO_SEASON else 1
    week_params: List[WeekParams] = []
    for group in NcaaFbGroup:
        for week_num in range(first_week, N_REGULAR_WEEKS + 1):
            week_params.append(WeekParams(year, week_num, SeasonType.regular, group))
        week_params.append(WeekParams(year, 1, SeasonType.post, group))
    return week_params


async def get_season(
    year: int,
    # Keyword-only so the positional signature stays `(year,)`, which is what
    # `apply_in_parallel` unpacks its arg tuples into.
    *,
    use_cache: bool = True,
    season_cache: SeasonCache | None = None,
) -> Season:
    """
    Get the games from a season of NCAAFB

    `use_cache=False` neither reads nor writes the season cache, for a
    caller that is re-pulling precisely because what's cached is known to
    be incomplete. That is not hypothetical: the cache is written for
    every season that has ended, so a machine that has ever pulled these
    years has one, and `backfill_week_zero` -- whose whole job is to fetch
    the week 0 those cached seasons predate -- got an instant cache hit
    and reported that every season gained nothing.

    Note it leaves the cache alone rather than refreshing it, so a dry run
    stays a dry run. Delete `~/.endgame/cache/season/ncaafb/` (or
    `$ENDGAME_CACHE_DIR`) if you want local pulls corrected too.
    """
    logger.info("Getting NCAA season %s", year)
    cache = season_cache or SeasonCache("ncaafb")
    season = cache.check_cache(year) if use_cache else None
    if season:
        return season.with_season_start(SEASON_START)

    week_params = _week_params(year)

    weeks = []
    trouble_weeks: List[WeekParams] = []
    for week_param in week_params:
        try:
            week = await _get_week(*week_param)
            weeks.append(week)
        # Should I raise custom exception instead?
        except aiohttp.ClientResponseError:
            year, week_num, season_type, group = week_param
            msg = (
                f"Marking week as trouble: "
                f"{year=} {week_num=} type={season_type.name} group={group.name}"
            )
            logger.warning(msg)
            trouble_weeks.append(week_param)

    weeks = list(_remove_cross_division_duplicates(weeks))
    season = Season(weeks, year, trouble_weeks, SEASON_START)

    # Cache if the season is over -- and only if we were allowed to read it,
    # since `save_to_cache` refuses to overwrite and a bypassing caller has
    # no business replacing what it deliberately ignored.
    season_end_date = datetime(year + 1, *SEASON_END, tzinfo=timezone.utc)
    if use_cache and datetime.now(timezone.utc) > season_end_date:
        cache.save_to_cache(season)

    return season


async def get_current_odds() -> AsyncIterator[Odds]:
    """
    Get odds for whatever week ESPN currently considers "this week", FBS only
    (betting markets don't really cover FCS/D2/D3).
    """
    parameters: RequestParameters = dict(
        lang="en", region="us", groups=NcaaFbGroup.fbs.value
    )
    async for odd in get_odds(BASE_URL, parameters):
        yield odd


def _remove_cross_division_duplicates(weeks: List[Week]) -> Iterator[Week]:
    # Removes duplicates that come from when teams play across divisions
    # Assumption: those still show up under the same week number
    # ...I'm not totally sure that's the case
    def key(w: Week) -> int:
        return w.number

    for number, matched_weeks in groupby(sorted(weeks, key=key), key=key):
        games: List[Game] = []
        for week in matched_weeks:
            games += week.games
        # set() to drop the cross-division duplicates, then sort so the
        # week's games don't come out in an arbitrary (and run-to-run
        # unstable) hash order.
        yield Week(sorted(set(games), key=lambda g: g.date), number)


async def _get_week(
    year: int, week: int, season_type: SeasonType, group: NcaaFbGroup
) -> Week:
    msg = f"Getting NCAAFB {year} {season_type.name} week {week} for {group.name}"
    logger.info(msg)
    parameters: RequestParameters = dict(
        lang="en",
        region="us",
        calendartype="blacklist",
        limit=300,
        seasontype=season_type.value,
        dates=year,
        week=week,
        groups=group.value,
    )

    games = await get_games(BASE_URL, parameters)
    games = list(map(_rename_teams, games))
    if season_type == SeasonType.post:
        week += N_REGULAR_WEEKS
    return Week(sorted(games, key=lambda g: g.date), week)


def _rename_teams(game: Game) -> Game:
    game_dict = game.to_dict()
    game_dict["away"] = _rename_team(game_dict["away"])
    game_dict["home"] = _rename_team(game_dict["home"])
    return Game(**game_dict)


def _rename_team(name: str) -> str:
    # Adjust any teams that have gone by multiple names
    if name == "Army Knights":
        return "Army Black Knights"
    if name == "Hawaii Warriors":
        return "Hawai'i Rainbow Warriors"
    if name == "Connecticut Huskies":
        return "UConn Huskies"
    if name == "Southern Methodist Mustangs":
        return "SMU Mustangs"
    if name == "Southern University Jaguars":
        return "Southern Jaguars"
    return name
