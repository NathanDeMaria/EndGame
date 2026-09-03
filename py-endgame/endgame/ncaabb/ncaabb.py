from datetime import date, datetime, timedelta, timezone
from enum import Enum
from logging import getLogger
from typing import AsyncIterator, Iterable, List, NamedTuple

import aiohttp

from ..async_tools import apply_in_parallel
from ..constants import DEFAULT_LOOKAHEAD_DAYS, ESPN_SPORTS_API_BASE
from ..date import clamp_to_window, date_range, get_end_year
from ..espn_games import get_games, save_seasons
from ..espn_odds import Odds, get_odds_range
from ..season_cache import SeasonCache
from ..types import Game, Season, Week, supersedes
from ..web import RequestParameters
from .gender import NcaabbGender

logger = getLogger(__name__)


NCAABB_SCOREBOARD = (
    f"{ESPN_SPORTS_API_BASE}/basketball/{{}}-college-basketball/scoreboard"
)
REGULAR_SEASON_START = (11, 1)
REGULAR_SEASON_END = (4, 1)
POST_SEASON_START = (3, 1)
SEASON_END = (4, 30)


class NcaabbGroup(Enum):
    """
    Group in NCAABB is division AND tournament if postseason
    """

    d1 = 50
    # Basketball counts postseason as a different "group"
    ncaa = 100
    nit = 98
    cbi = 55
    cit = 56


POSTSEASON_GROUPS = frozenset(
    [
        NcaabbGroup.ncaa,
        NcaabbGroup.nit,
        NcaabbGroup.cbi,
        NcaabbGroup.cit,
    ]
)


class DayParams(NamedTuple):
    """
    Query parameters for grabbing a day of
    NCAABB games from the ESPN API
    """

    date: date
    gender: NcaabbGender
    group: NcaabbGroup


async def update(gender: NcaabbGender, location=None):
    """
    Update a NCAABB .csv
    """
    if location is None:
        location = f"ncaa{gender.name[0]}bb.csv"

    seasons = await get_seasons(gender)
    # Games go into a season the way they were fetched -- by day -- so group
    # them into weeks for the .csv's week column.
    save_seasons([s._replace(weeks=s.calendar_weeks) for s in seasons], location)


async def get_seasons(gender: NcaabbGender) -> List[Season]:
    """
    Get all seasons for a NCAABB
    """
    end_year = get_end_year(SEASON_END)
    # `apply_in_parallel` binds every parameter of the callable it's handed,
    # including `get_ncaabb_season`'s two defaulted ones, so wrap it to take
    # just the year rather than padding every tuple out with `None`s.
    args = [(y,) for y in range(2001, end_year + 1)]
    return [
        s async for s in apply_in_parallel(lambda y: get_ncaabb_season(y, gender), args)
    ]


def _last_day_so_far(season_so_far: Season | None) -> date | None:
    """
    The day a resuming pull starts from: the last one we have a result for.

    Completed games only. Once a season carries its fixtures, the last game
    in it is one that hasn't been played -- often weeks out -- and resuming
    from *that* starts the walk after the day it ends on. The season would
    then never pick up another result: every run would fetch an empty range,
    write back what it already had, and say nothing.
    """
    if season_so_far is None:
        return None
    # Might get the most recent day's games again unnecessarily.
    # That's fine because we don't know if all the games
    # for that day were done last time this was run.
    last_day_done = max(
        (g.date for w in season_so_far.weeks for g in w.games if g.completed),
        default=None,
    )
    if last_day_done is None:
        return None
    return last_day_done.date()


def _horizon(include_unplayed: bool) -> date:
    """
    The last day worth asking for.

    A results-only pull stops at today, since no earlier day can gain a game
    it doesn't already have. A pull carrying fixtures goes on for
    `DEFAULT_LOOKAHEAD_DAYS`, which each caller still bounds by the end of
    the stretch it's walking.
    """
    today = date.today()
    return today + timedelta(days=DEFAULT_LOOKAHEAD_DAYS) if include_unplayed else today


async def get_ncaabb_season(
    year: int,
    gender: NcaabbGender,
    season_so_far: Season | None = None,
    season_cache: SeasonCache | None = None,
    *,
    include_unplayed: bool = False,
) -> Season:
    """
    Get a season of NCAABB, a day at a time.

    `include_unplayed` keeps the games ESPN hasn't finished, so the season
    carries the fixtures ahead of it as well as the results behind it. A
    season fetched with it holds games with no result yet -- read
    `game.completed` before reading a score, and note that anything walking
    it per-game (possessions, box scores) has to skip the unfinished ones
    itself.

    It does two things, and both are needed: the flag on the fetch, and the
    walk running past today rather than stopping there. NCAABB is pulled by
    day, so a fixture is only visible by asking for the day it falls on.
    """
    logger.info("Getting NCAABB %s season %d", gender.name, year)
    cache = season_cache or SeasonCache(f"ncaa{gender.name[0]}bb")
    season = cache.check_cache(year)
    if season:
        return season.with_season_start(REGULAR_SEASON_START)

    horizon = _horizon(include_unplayed)
    day_params: List[DayParams] = []
    start = _last_day_so_far(season_so_far) or date(year, *REGULAR_SEASON_START)
    end = date(year + 1, *REGULAR_SEASON_END)
    # Don't ask for days past the horizon
    end = min(end, horizon)
    for day in date_range(start, end):
        day_params.append(DayParams(day, gender, NcaabbGroup.d1))
    start = _last_day_so_far(season_so_far) or date(year + 1, *POST_SEASON_START)
    end = date(year + 1, *SEASON_END)
    # Don't ask for days past the horizon
    end = min(end, horizon)
    for group in POSTSEASON_GROUPS:
        for day in date_range(start, end):
            day_params.append(DayParams(day, gender, group))
    # TODO: consider having season_so_far.trouble_params re-checked
    games: List[Game] = []
    trouble_days = []
    for day_param in day_params:
        try:
            games += await get_ncaabb_games(
                *day_param, include_unplayed=include_unplayed
            )
        except aiohttp.ClientResponseError:
            day, gender, group = day_param
            logger.warning(
                "Marking %s for %s %s as trouble", day, gender.name, group.name
            )
            trouble_days.append(day_param)

    season = _build_season(games, year, trouble_days)
    if season_so_far:
        season = merge_seasons([season_so_far, season])

    if datetime.now(timezone.utc) > datetime(
        year + 1, *SEASON_END, tzinfo=timezone.utc
    ):
        cache.save_to_cache(season)

    return season


def _build_season(games: Iterable[Game], year: int, trouble_params: List) -> Season:
    """
    Put a season's games together the way they were fetched.

    NCAABB is pulled a day at a time, so there's no week grouping in the
    source to keep: the games go in as one lot, and `season.calendar_weeks`
    builds the weeks from the game dates on the way out.
    """
    return Season(
        [Week(sorted(games, key=lambda g: g.date), 1)],
        year,
        trouble_params,
        REGULAR_SEASON_START,
    )


def merge_seasons(seasons: List[Season]) -> Season:
    assert all(s.year == seasons[0].year for s in seasons)

    games: dict[str, Game] = {}
    for season in seasons:
        for week in season.weeks:
            for game in week.games:
                # If a game showed up multiple times, keep the best version:
                # the latest, except that a game already finished is never
                # replaced by one that isn't. Plain "latest wins" is what
                # would walk a final back to a live scoreline the first time
                # a run re-fetched a day mid-game.
                if supersedes(game, games.get(game.game_id)):
                    games[game.game_id] = game

    # Merge trouble params
    trouble_params = set(sum((s.trouble_params or [] for s in seasons), []))

    return _build_season(games.values(), seasons[0].year, list(trouble_params))


async def get_ncaabb_games(
    game_date: date,
    gender: NcaabbGender,
    group: NcaabbGroup,
    *,
    include_unplayed: bool = False,
) -> List[Game]:
    logger.info("Getting NCAABB %s %s %s", gender.value, game_date, group.name)
    parameters: RequestParameters = dict(
        lang="en",
        region="us",
        calendartype="blacklist",
        limit=300,
        dates=game_date.strftime("%Y%m%d"),
        groups=group.value,
    )
    games = await get_games(
        NCAABB_SCOREBOARD.format(gender.name),
        parameters,
        include_unplayed=include_unplayed,
    )
    # Filtering thanks to Montana State Bobcats at Northern Arizona
    # Lumberjacks on 2003-02-28 and a bunch of NCAAWBB games.
    #
    # Only a *finished* 0-0 is that bad data. Every unplayed game is 0-0 --
    # either ESPN sends 0s for a game that hasn't started, or `parse_game`
    # writes them for a fixture with no score at all -- so dropping on the
    # scoreline alone would throw away the entire schedule.
    return [g for g in games if not g.completed or g.home_score > 0 or g.away_score > 0]


# The busiest league here by a distance: ~50 D1 games on a January
# Saturday, and a measured 670 events over a fortnight. Two weeks is the
# most that reliably fits in one response, which is what sets the default
# every other league then raises.
ODDS_CHUNK_DAYS = 14


class _OddsParams(NamedTuple):
    """
    One gender and competition, over the stretch of days it could have been
    played on.
    """

    start: date
    end: date
    gender: NcaabbGender
    group: NcaabbGroup


async def _get_ncaabb_odds(
    start: date, end: date, gender: NcaabbGender, group: NcaabbGroup
) -> AsyncIterator[Odds]:
    logger.info(
        "Getting NCAABB %s odds %s..%s %s", gender.value, start, end, group.name
    )
    parameters: RequestParameters = dict(
        lang="en",
        region="us",
        calendartype="blacklist",
        groups=group.value,
    )
    odds = get_odds_range(
        NCAABB_SCOREBOARD.format(gender.name),
        parameters,
        start=start,
        end=end,
        chunk_days=ODDS_CHUNK_DAYS,
    )
    async for odd in odds:
        yield odd


def _odds_params(start: date, end: date) -> List[_OddsParams]:
    """
    The requests that cover `start`..`end`, one per gender and competition.

    Each is narrowed to its own window rather than given the whole range:
    the tournaments only run in March and April, so asking them about
    December would be four wasted requests per gender per chunk. A range
    that misses a window entirely drops it.
    """
    params = []
    for gender in NcaabbGender:
        regular = clamp_to_window(start, end, REGULAR_SEASON_START, REGULAR_SEASON_END)
        if regular:
            params.append(_OddsParams(*regular, gender, NcaabbGroup.d1))
        postseason = clamp_to_window(start, end, POST_SEASON_START, SEASON_END)
        if postseason:
            for group in POSTSEASON_GROUPS:
                params.append(_OddsParams(*postseason, gender, group))
    return params


async def get_ncaabb_spreads(start: date, end: date) -> AsyncIterator[Odds]:
    """
    Get the odds on every NCAABB game between `start` and `end`, inclusive.
    """
    for params in _odds_params(start, end):
        async for odd in _get_ncaabb_odds(*params):
            yield odd
