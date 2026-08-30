"""
Leagues that are pulled a day at a time.

The NHL and the WNBA both play on a schedule that ESPN only exposes by
date: there's no week parameter to ask for the way there is for the NFL,
and no group codes to separate the postseason the way NCAABB needs. So a
season is a walk over its calendar days, and the weeks the .csv ends up
with are rebuilt from the game dates by `Season.calendar_weeks`.

NCAABB works the same way but has enough of its own shape (genders,
tournament groups, possessions, box scores) that it stays on its own code.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from logging import getLogger
from typing import (
    AsyncIterator,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Tuple,
)

import aiohttp

from .async_tools import apply_in_parallel
from .constants import DEFAULT_LOOKAHEAD_DAYS
from .date import date_range, is_between_dates
from .espn_games import EventFilter, get_games, save_seasons
from .espn_odds import Odds, get_odds
from .season_cache import SeasonCache
from .types import Game, Season, SeasonStart, SeasonType, Week, supersedes
from .web import RequestParameters

logger = getLogger(__name__)


def _keep_name(name: str, day: date) -> str:
    return name


@dataclass(frozen=True)
class DailyLeague:
    """
    Everything that differs between two leagues pulled a day at a time.
    """

    # Used for the season cache directory, the S3 key and the .csv name
    name: str
    scoreboard_url: str
    # The (month, day) week 1 of a season contains, and the (month, day)
    # the season is over by. A season is named for the calendar year it
    # starts in, the way the NFL's 2019 season ends in 2020.
    season_start: SeasonStart
    season_end: Tuple[int, int]
    # 1 when a season runs into the next calendar year (the NHL's October
    # to June), 0 when it starts and finishes in the same one (the WNBA's
    # May to October). Everything that turns a season year into real dates
    # goes through this.
    end_year_offset: int
    # Earliest season worth asking ESPN for
    first_year: int
    # ESPN hands back the occasional "completed" game with no score at all,
    # which NCAABB drops. That's only safe for leagues that can't actually
    # finish 0-0 -- the NHL played to ties before 2005-06, so a 0-0 final
    # there is a real result.
    drop_scoreless: bool
    # Franchises that ESPN lists under more than one name over the years,
    # collapsed onto the current one so a team is one team across seasons.
    #
    # Takes the day the game was played as well as the name, because a name
    # on its own isn't always enough to say which franchise it is: "Winnipeg
    # Jets" is one franchise before 1996 and a different one after 2011.
    rename_team: Callable[[str, date], str] = field(default=_keep_name)
    # Seasons that didn't run when they normally do, as season year ->
    # (first day, the day it's over by). The shared window is deliberately
    # loose, but a season that moves by months rather than days can't be
    # covered by widening it: stretching one season's end past the next
    # one's start is what puts a game in two seasons' files at once. These
    # get the real dates instead.
    odd_seasons: Mapping[int, Tuple[date, date]] = field(default_factory=dict)
    # The competitions that count as this league playing itself *within* a
    # regular season -- see `league_play_filter`, which is the only reader.
    # "STD" is an ordinary game, which is every regular-season game either
    # league has played back to 2002, outdoor ones included. A league with
    # a competition of its own alongside those (the WNBA's Commissioner's
    # Cup) names it here.
    regular_season_competitions: FrozenSet[str] = frozenset({"STD"})
    # Event ids for exhibitions ESPN filed as ordinary games, so nothing in
    # the response marks them. Named one at a time because that's the only
    # thing that separates them -- and kept rare on purpose: every id in
    # here is a game no rule could catch, so a growing list means the rule
    # itself has stopped working.
    untagged_exhibitions: FrozenSet[str] = frozenset()
    # How many days past today a fixture-carrying pull asks for -- see
    # `constants.DEFAULT_LOOKAHEAD_DAYS`. Ignored entirely by a results-only
    # pull, which never looks past today.
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS

    def start_date(self, year: int) -> date:
        """
        The first day of the `year` season worth asking for.
        """
        odd = self.odd_seasons.get(year)
        return odd[0] if odd else date(year, *self.season_start)

    def end_date(self, year: int) -> date:
        """
        The day the `year` season is over by (exclusive).
        """
        odd = self.odd_seasons.get(year)
        return odd[1] if odd else date(year + self.end_year_offset, *self.season_end)

    def is_finished(self, year: int) -> bool:
        """
        Whether the `year` season is over, and so safe to cache.
        """
        end = datetime.combine(self.end_date(year), time.min, tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > end

    def is_in_season(self, day: date) -> bool:
        """
        Whether `day` falls in a stretch of the year this league plays in.

        A day inside an odd season counts even if it's outside the usual
        window -- that's the whole reason it's listed. Days the other way
        round (in the window, but in a year the league started late) are
        left in: the cost is one request that comes back with no odds,
        where the cost of dropping a real day is missing data.
        """
        if any(start <= day < end for start, end in self.odd_seasons.values()):
            return True
        return is_between_dates(day, self.season_start, self.season_end)

    def latest_year(self) -> int:
        """
        The most recent season worth asking for: the last one that's started.

        `get_end_year` can't answer this for both leagues -- it assumes a
        season spans two calendar years -- and asking by start date works
        either way.
        """
        today = date.today()
        started = (today.month, today.day) >= self.season_start
        return today.year if started else today.year - 1


async def update(league: DailyLeague, location: Optional[str] = None) -> None:
    """
    Update a league's .csv with every season we can get.
    """
    if location is None:
        location = f"{league.name}.csv"
    seasons = await get_seasons(league)
    # Games go into a season the way they were fetched -- by day -- so group
    # them into weeks for the .csv's week column.
    save_seasons([s._replace(weeks=s.calendar_weeks) for s in seasons], location)


async def get_seasons(league: DailyLeague) -> List[Season]:
    """
    Get every season of a league.
    """
    args = [(y,) for y in range(league.first_year, league.latest_year() + 1)]
    return [s async for s in apply_in_parallel(lambda y: get_season(league, y), args)]


async def get_season(
    league: DailyLeague,
    year: int,
    season_so_far: Optional[Season] = None,
    season_cache: Optional[SeasonCache] = None,
    *,
    include_unplayed: bool = False,
) -> Season:
    """
    Get a season, a day at a time.

    Pass `season_so_far` to pick up from the last day it already has rather
    than walking the season from the top. That's what makes a daily run
    cheap for a job that starts with an empty web cache.

    `include_unplayed` keeps the games ESPN hasn't finished, so the season
    carries the fixtures ahead of it as well as the results behind it. A
    season fetched with it holds games with no result yet -- read
    `game.completed` before reading a score.

    It does two things, and both are needed: the flag on the fetch, and the
    walk running `league.lookahead_days` past today instead of stopping
    there. A league pulled by day only sees a fixture by asking for the day
    it falls on, so without the second half the flag finds nothing but the
    unfinished games on days already past.

    It needs nothing from the season cache, which is only written once a
    season is over and every game in it is complete: there's no unplayed
    game for a cached season to be missing, so a hit is as good either way
    and the cache doesn't have to know which way it was fetched.
    """
    logger.info("Getting %s season %d", league.name, year)
    cache = season_cache or SeasonCache(league.name)
    cached = cache.check_cache(year)
    if cached:
        return cached.with_season_start(league.season_start)

    start = _last_day_so_far(season_so_far) or league.start_date(year)
    # A results-only pull stops at today, since no earlier day can gain a
    # game it doesn't already have. A pull carrying fixtures goes on for
    # `lookahead_days`, still bounded by the end of the season.
    horizon = date.today()
    if include_unplayed:
        horizon += timedelta(days=league.lookahead_days)
    end = min(league.end_date(year), horizon)

    games: List[Game] = []
    trouble_days: List[date] = []
    for day in date_range(start, end):
        try:
            games += await get_daily_games(
                league, day, include_unplayed=include_unplayed
            )
        except aiohttp.ClientResponseError:
            logger.warning("Marking %s for %s as trouble", day, league.name)
            trouble_days.append(day)

    season = _build_season(league, games, year, trouble_days)
    if season_so_far:
        season = merge_seasons(league, [season_so_far, season])

    if league.is_finished(year):
        cache.save_to_cache(season)

    return season


def merge_seasons(league: DailyLeague, seasons: List[Season]) -> Season:
    """
    Fold seasons of the same year together, keeping the latest copy of a
    game that shows up in more than one of them.
    """
    assert all(s.year == seasons[0].year for s in seasons)

    games: dict[str, Game] = {}
    for season in seasons:
        for week in season.weeks:
            for game in week.games:
                if supersedes(game, games.get(game.game_id)):
                    games[game.game_id] = game

    trouble_params = set(sum((s.trouble_params or [] for s in seasons), []))

    return _build_season(
        league, games.values(), seasons[0].year, sorted(trouble_params)
    )


def _build_season(
    league: DailyLeague, games: Iterable[Game], year: int, trouble_params: List
) -> Season:
    """
    Put a season's games together the way they were fetched.

    These leagues are pulled a day at a time, so there's no week grouping in
    the source to keep: the games go in as one lot, and
    `season.calendar_weeks` builds the weeks from the game dates on the way
    out.
    """
    return Season(
        [Week(sorted(games, key=lambda g: g.date), 1)],
        year,
        trouble_params,
        league.season_start,
    )


def _last_day_so_far(season_so_far: Optional[Season]) -> Optional[date]:
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


def _day_parameters(day: date) -> RequestParameters:
    return dict(
        lang="en",
        region="us",
        calendartype="blacklist",
        limit=300,
        dates=day.strftime("%Y%m%d"),
    )


def league_play_filter(league: DailyLeague) -> EventFilter:
    """
    An `EventFilter` keeping only the games where `league` plays itself.

    Asking ESPN for a *day* is what makes this necessary. A league fetched
    by week names a `seasontype` in the request and can't get back anything
    else; a day is whatever ESPN ran that day -- preseason, the All-Star
    game, a tournament of national sides -- and a rating fit on those has
    teams in it that don't play in the league.

    Telling one from the other takes both fields ESPN gives, because
    neither is enough alone:

      * Preseason is a season type, and it's where the games against
        non-league sides live (Adler Mannheim, Jokerit, Nigeria, the
        Toyota Antelopes).
      * The All-Star game is filed under the *regular* season, so the
        season type can't catch it.
      * Nor can the competition type: the 2023 NHL All-Star ran a bracket
        whose games come back as "SEMI", which is what a conference final
        is called too.

    So the postseason is kept whatever its rounds are named, and the
    regular season keeps only `league.regular_season_competitions`. That's
    an allowlist rather than a blocklist of the exhibitions because the two
    failures aren't equal: an unrecognized competition costs real games,
    which leaves a hole someone notices, while a missed exhibition puts a
    team that doesn't exist into a published rating. The logging is so the
    first one isn't silent either.
    """

    def _is_league_play(event: Dict) -> bool:
        if event.get("id") in league.untagged_exhibitions:
            return False

        season_type = (event.get("season") or {}).get("type")
        if season_type == SeasonType.post.value:
            return True
        if season_type == SeasonType.pre.value:
            return False

        # Anything else is read as the regular season, a response with no
        # season block included: the competition check below is the one
        # that matters, and defaulting the other way would let an event
        # skip it by leaving a field out.
        competition = (event.get("competitions") or [{}])[0]
        competition_type = (competition.get("type") or {}).get("abbreviation")
        if competition_type in league.regular_season_competitions:
            return True
        logger.info(
            "Dropping %s from %s: not league play (season type %s, competition %s)",
            event.get("name", event.get("id")),
            league.name,
            season_type,
            competition_type,
        )
        return False

    return _is_league_play


async def get_daily_games(
    league: DailyLeague, day: date, *, include_unplayed: bool = False
) -> List[Game]:
    """
    Get a single day's games, league play only.

    Results only unless `include_unplayed`, which keeps the fixtures too.
    """
    logger.info("Getting %s games for %s", league.name, day)
    games = await get_games(
        league.scoreboard_url,
        _day_parameters(day),
        league_play_filter(league),
        include_unplayed=include_unplayed,
    )
    games = [_rename_teams(league, g) for g in games]
    if league.drop_scoreless:
        # Only a *finished* 0-0 is the bad data this is here to drop. Every
        # unplayed game is 0-0 -- either ESPN sends 0s for a game that
        # hasn't started, or `parse_game` writes them for a fixture with no
        # score at all -- so dropping on the scoreline alone would throw
        # away the entire schedule this flag exists to fetch.
        games = [
            g for g in games if not g.completed or g.home_score > 0 or g.away_score > 0
        ]
    return games


async def get_daily_odds(league: DailyLeague, day: date) -> AsyncIterator[Odds]:
    """
    Get the odds on a day's games, or nothing at all if the league isn't
    playing then.
    """
    if not league.is_in_season(day):
        logger.info("%s isn't in season on %s, skipping odds", league.name, day)
        return
    logger.info("Getting %s odds for %s", league.name, day)
    async for odd in get_odds(league.scoreboard_url, _day_parameters(day)):
        yield odd


def _rename_teams(league: DailyLeague, game: Game) -> Game:
    game_dict = game.to_dict()
    day = game.date.date()
    game_dict["away"] = league.rename_team(game_dict["away"], day)
    game_dict["home"] = league.rename_team(game_dict["home"], day)
    return Game(**game_dict)
