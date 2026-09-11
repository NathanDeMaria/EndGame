from datetime import date, datetime, timezone
from logging import getLogger
from typing import AsyncIterator, Optional

from endgame.async_tools import apply_in_parallel
from endgame.date import get_end_year
from endgame.espn_games import get_games, save_seasons
from endgame.espn_odds import Odds, get_odds_range
from endgame.season_cache import SeasonCache
from endgame.types import Game, Season, SeasonType, Week
from endgame.web import RequestParameters

from .teams import NflTeam

logger = getLogger(__name__)


# Say each season ends on March 1st
SEASON_END = (3, 1)
BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
# The regular season ran 17 weeks (16 games) through 2020 and 18 weeks (17
# games) from 2021 on -- one more game, not just one more bye.
FIRST_18_WEEK_SEASON = 2021
N_POST_WEEKS = 5


def n_regular_weeks(season: int) -> int:
    """
    How many weeks the regular season ran in `season`.

    Not a constant: asking 2021 and later for 17 weeks left every team's
    last game of the year out of the season, and numbering the postseason
    from a fixed 17 would then have collided week 18 with the wild card
    round.
    """
    return 18 if season >= FIRST_18_WEEK_SEASON else 17


async def update(location: str = "nfl.csv"):
    """
    Update the nfl.csv
    """
    end_year = get_end_year(SEASON_END)
    args = [(y,) for y in range(1999, end_year + 1)]
    seasons = [s async for s in apply_in_parallel(get_season, args)]
    save_seasons(seasons, location)


async def get_season(
    year: int,
    # Keyword-only so the positional signature stays `(year,)`, which is what
    # `apply_in_parallel` unpacks its arg tuples into.
    *,
    include_unplayed: bool = False,
) -> Season:
    """
    Get an NFL season

    `include_unplayed` keeps the games ESPN hasn't finished, so the season
    carries the fixtures ahead of it as well as the results behind it. A
    season fetched with it holds games with no result yet -- read
    `game.completed` before reading a score.

    It costs nothing here: the season is already every week, and a week's
    request comes back with its fixtures whether or not they've been played.

    It needs nothing from the season cache, which is only written once a
    season is over and every game in it is complete: there's no unplayed
    game for a cached season to be missing, so a hit is as good either way
    and the cache doesn't have to know which way it was fetched.
    """
    logger.info("Getting NFL season %d", year)
    cache = SeasonCache("nfl")
    season = cache.check_cache(year)
    if season:
        return season

    # This "season" is 2019 for the season whose Super Bowl is in 2020
    weeks = []
    for week in range(1, n_regular_weeks(year) + 1):
        weeks.append(
            await _get_week(
                year, week, SeasonType.regular, include_unplayed=include_unplayed
            )
        )
    for week in range(1, N_POST_WEEKS + 1):
        weeks.append(
            await _get_week(
                year, week, SeasonType.post, include_unplayed=include_unplayed
            )
        )
    season = Season(weeks, year)

    # Cache if the season is over
    season_end_date = datetime(year + 1, *SEASON_END, tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > season_end_date:
        cache.save_to_cache(season)

    return season


async def _get_week(
    season: int,
    week: int,
    season_type: SeasonType,
    *,
    include_unplayed: bool = False,
) -> Week:
    logger.info("Getting NFL %d %s week %d", season, season_type.name, week)
    parameters: RequestParameters = dict(
        lang="en",
        region="us",
        calendartype="blacklist",
        limit=32,
        seasontype=season_type.value,
        dates=season,
        week=week,
    )

    games = await get_games(BASE_URL, parameters, include_unplayed=include_unplayed)
    # Drop what isn't two NFL franchises -- the Pro Bowl, whose sides ESPN
    # calls "AFC"/"NFC" -- and move the rest onto their current franchise.
    #
    # The test is the move itself, on both sides, rather than a list of
    # acceptable names. A list can only hold one spelling of a franchise
    # ESPN has since renamed, and the one this used to keep was a mix of
    # eras: it had "Oakland Raiders" but not "Las Vegas", "Los Angeles
    # Chargers" but not "San Diego". Every home game of a franchise on the
    # wrong side of one of those renames was silently dropped -- the
    # Raiders from 2020 on, Washington from 2022 on, and the San Diego and
    # St. Louis years of the Chargers and Rams. 528 games in all, which
    # read as those teams having played half a season.
    #
    # `_get_team` already knows every spelling, so routing the filter
    # through it is what keeps the two from drifting apart again.
    games = [
        move_teams(g)
        for g in games
        if _get_team(g.home) is not None and _get_team(g.away) is not None
    ]

    if season_type == SeasonType.post:
        week += n_regular_weeks(season)
    return Week(sorted(games, key=lambda g: g.date), week)


# Sixteen games a week, so a whole season is one request. Worth spending it
# in one: the NFL is priced further ahead than anything else here -- 272 of
# 285 games already had a line in early September, out to the following
# January -- and this is what reaches them.
ODDS_CHUNK_DAYS = 180


async def get_nfl_odds(start: date, end: date) -> AsyncIterator[Odds]:
    """
    Get the odds on every NFL game between `start` and `end`, inclusive.

    This used to ask for whatever week ESPN called "this week", by sending
    no dates at all and taking the default. That was a week of visibility
    into a league that prices its whole season by September, so the opening
    line on all but the nearest games was never recorded.
    """
    parameters: RequestParameters = dict(lang="en", region="us")
    async for odd in get_odds_range(
        BASE_URL, parameters, start=start, end=end, chunk_days=ODDS_CHUNK_DAYS
    ):
        yield odd


def move_teams(game: Game) -> Game:
    game_dict = game.to_dict()
    game_dict["away"] = _move_team_name(game_dict["away"])
    game_dict["home"] = _move_team_name(game_dict["home"])
    return Game(**game_dict)


def _move_team_name(old_name: str) -> str:
    team = _get_team(old_name)
    if team is None:
        raise ValueError(f"Not an NFL franchise: {old_name!r}")
    return team


def _get_team(old_name: str) -> Optional[str]:
    """
    The current franchise `old_name` played for, under any spelling ESPN
    has used for it, or None if it isn't an NFL franchise at all.

    None is how the Pro Bowl's conference sides are recognized; it is not a
    "probably fine, skip it" for a name that should have matched.
    """
    tidy_name = (
        old_name.replace("San Diego", "Los Angeles")
        .replace("St. Louis", "Los Angeles")
        .replace("Washington Redskins", "commanders")
        .replace("Washington", "commanders")
        .replace("Oakland Raiders", "Las Vegas Raiders")
        .replace("49ers", "niners")
    )
    try:
        return NflTeam[tidy_name.split(" ")[-1].lower()].name
    except KeyError:
        return None
