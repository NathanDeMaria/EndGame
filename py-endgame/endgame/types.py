from datetime import date, datetime, timedelta
from enum import Enum
from itertools import groupby
from typing import (
    Any,
    Dict,
    Iterable,
    Iterator,
    List,
    NamedTuple,
    Optional,
    Tuple,
)

# The (month, day) of `year` that a season's week numbering counts from
SeasonStart = Tuple[int, int]


class Game(NamedTuple):
    """
    A game...
    """

    home: str
    home_score: int
    away: str
    away_score: int
    neutral_site: bool
    completed: bool
    date: datetime
    game_id: str
    # ESPN's `status.type.name`, verbatim: STATUS_FINAL, STATUS_SCHEDULED,
    # STATUS_IN_PROGRESS, STATUS_CANCELED, STATUS_POSTPONED, ...
    #
    # `completed` only says result / not-a-result, and once unplayed games
    # are kept that isn't enough: a 0-0 game is scheduled, cancelled,
    # scoreless-so-far, or a real NHL tie, and ESPN sends a score for all
    # of them. This is how a reader tells which.
    #
    # A str rather than an enum on purpose. `SeasonType` and `NcaaFbGroup`
    # enumerate parameters we send; this is a value ESPN sends back, and a
    # status nobody has seen before should land in the pickle as an odd
    # string rather than take a season's fetch down.
    #
    # Defaulted because every Game in the bucket was pickled before this
    # field existed: a NamedTuple unpickles by calling the current class
    # with the old values, so a field without one raises TypeError on every
    # season already saved. Empty rather than STATUS_FINAL -- those games
    # really are all final, but writing a status nothing ever read is how a
    # wrong invariant gets baked in.
    #
    # Note it can disagree with `completed`: `parse_game` concludes a game
    # with no score at all is unplayed whatever ESPN's status claims, and
    # doesn't rewrite the status to match. This is what ESPN said; that is
    # what we concluded.
    status: str = ""

    @property
    def column_names(self) -> List[str]:
        """
        Column names, in case we want to put this in a .csv
        """
        return list(self.to_dict().keys())

    def to_dict(self) -> Dict:
        """
        Convert game to a dictionary
        """
        # pylint: disable=no-member
        return self._asdict()


class Week(NamedTuple):
    """
    A set of games in the same week/round of a league
    """

    games: List[Game]
    number: int

    @property
    def games_in_order(self) -> List[Game]:
        """
        The games, sorted chronologically.

        Prefer this over `.games`: the raw list's order depends on how the
        season happened to be built and merged, so it isn't reliably
        chronological.
        """
        return sorted(self.games, key=lambda g: g.date)

    @property
    def start(self) -> Optional[datetime]:
        """
        When the first game of the week happened, or None if there are no games.
        """
        return min((g.date for g in self.games), default=None)

    @property
    def end(self) -> Optional[datetime]:
        """
        When the last game of the week happened, or None if there are no games.
        """
        return max((g.date for g in self.games), default=None)


class NcaaFbGroup(Enum):
    """
    NCAA division/grouping
    """

    fbs = 80
    fcs = 81
    d23 = 35  # two AND three


class SeasonType(Enum):
    """
    Regular, or bowls+playoffs.
    Post probably also includes conference championships?

    `pre` is never asked for -- nothing here wants preseason games. It's
    named because the leagues fetched by day get one back whether they
    asked or not, and recognizing it is how they're dropped.
    """

    pre = 1
    regular = 2
    post = 3


class WeekParams(NamedTuple):
    """
    A set of parameters for a week that'll be used for a single
    GET from the ESPN API.
    """

    year: int
    week: int
    season_type: SeasonType
    # Only have these on the NCAAFB for now...fine?
    group: NcaaFbGroup


class Season(NamedTuple):
    """
    A season of competitions for a league
    """

    weeks: List[Week]
    year: int
    # This is either DayParams (basketball) or WeekParams in practice
    trouble_params: Optional[List] = None
    # When set, `.weeks` is only a record of how the games were fetched, and
    # the chronological view of the season (`calendar_weeks`, `iter_weeks`)
    # is rebuilt from the game dates, counting from this (month, day).
    #
    # Leagues whose source week numbers are already chronological (the NFL,
    # whose weeks run Thursday-Monday) leave this unset and are walked in
    # their own numbering.
    season_start: Optional[SeasonStart] = None

    @property
    def weeks_in_order(self) -> List[Week]:
        """
        The weeks as fetched, sorted by when their first game happened.

        Prefer this over `.weeks`: the raw list's order depends on how the
        season happened to be built and merged. Sorts on game dates rather
        than `week.number` because the numbering itself has been unreliable.

        These are the source's weeks, so for a league with a `season_start`
        they can still overlap in time -- use `calendar_weeks` (or
        `iter_weeks`) to walk the season, and these to trace a game back to
        the request that fetched it.

        Weeks with no games sort last, since they have no date to sort on.
        """
        return sorted(self.weeks, key=_week_sort_key)

    @property
    def calendar_weeks(self) -> List[Week]:
        """
        Every game in the season, regrouped into Monday-Sunday calendar
        weeks numbered from `season_start`.

        Built from the game dates rather than the source's week numbers, so
        weeks can't overlap however badly the source labelled things.

        Raises ValueError if the season has no `season_start`, since there'd
        be nothing to number the weeks from.
        """
        if self.season_start is None:
            raise ValueError(
                f"Season {self.year} has no season_start, so its games can't be "
                "regrouped into calendar weeks. Walk `weeks_in_order` instead."
            )
        # Pool by game_id: the same game can be fetched more than once (a
        # cross-division matchup comes back under both divisions), and the
        # copies aren't guaranteed to be identical -- `supersedes` picks
        # which one stands.
        games: Dict[str, Game] = {}
        for week in self.weeks:
            for game in week.games:
                if supersedes(game, games.get(game.game_id)):
                    games[game.game_id] = game
        return group_games_into_weeks(games.values(), self.year, self.season_start)

    def with_season_start(self, season_start: SeasonStart) -> "Season":
        """
        A copy of this season tagged with the (month, day) its week
        numbering counts from.

        Seasons pickled before that field existed come back untagged, and
        the cache is never overwritten, so leagues tag them on the way out
        of it. Only the tag is added -- `.weeks` is left alone, and the
        calendar weeks get rebuilt from the games.
        """
        return self._replace(season_start=season_start)


def _week_sort_key(week: Week) -> "tuple[int, Any]":
    start = week.start
    if start is None:
        # Tuples compare left-to-right, so the leading 0/1 keeps these
        # week numbers from ever being compared against a datetime.
        return (1, week.number)
    return (0, start)


def _week_end(day: date) -> date:
    # The AP poll is released based on Monday-Sunday games, so I'll default
    # to that grouping, keyed by the Monday that follows it.
    return day + timedelta(days=7 - day.weekday())


def _week_number(week_end: date, year: int, season_start: SeasonStart) -> int:
    """
    Number a week by how far it is from the start of the season.

    Derived from the date rather than from the week's position among the
    games we happen to have, so grouping part of a season gives the same
    numbers as grouping all of it. Numbering positionally is what let an
    incremental pull restart at 1 and merge March games into November's
    week 1.
    """
    return (week_end - _week_end(date(year, *season_start))).days // 7 + 1


def group_games_into_weeks(
    games: Iterable[Game], year: int, season_start: SeasonStart
) -> List[Week]:
    """
    Group games into Monday-Sunday weeks, numbered from the season's start.

    `season_start` is the (month, day) of `year` that week 1 contains, so
    the numbers don't depend on which games happen to be in hand.
    """
    by_week = groupby(
        sorted(games, key=lambda g: g.date), key=lambda g: _week_end(g.date.date())
    )
    return [
        Week(list(week_games), _week_number(week_end, year, season_start))
        for week_end, week_games in by_week
    ]


def supersedes(new: Game, old: Optional[Game]) -> bool:
    """
    Whether `new` should replace `old` as the copy of a game to keep.

    Every dedupe here answers this question -- across divisions, across
    weeks, across a fresh fetch and what's already saved -- so they all
    answer it in one place rather than four.

    Later wins, which is what lets a re-pull correct a score. The exception
    is a game ESPN has finished being replaced by one it hasn't: copies of a
    game in progress are fetched minutes apart and disagree, and a final
    must not be walked back to a live scoreline by whichever request
    happened to run last.

    While unplayed games are dropped at the fetch (`espn_games.get_games`)
    this can't fire -- every copy of every game is complete, so it's plain
    "later wins". It goes in first precisely so that keeping them is a
    change to one filter rather than to every dedupe downstream of it.
    """
    if old is None:
        return True
    return new.completed or not old.completed


def merge_weekly_seasons(seasons: List[Season]) -> Season:
    """
    Fold seasons of the same year together, keeping the best copy of a
    game that shows up in more than one of them.

    `supersedes` decides which that is: the later season wins per game, so
    a re-pull corrects a score, except that a game already finished is
    never replaced by one that isn't. What a merge cannot do is *drop* a
    game: anything only an earlier season knows about
    survives. That is the property that makes a partial fetch safe to write
    over a complete one -- before this, a pull that lost a week to an ESPN
    5xx replaced the season with a smaller one and said nothing.

    Unlike the merge in `daily.py`, this keeps the week each game was
    fetched under. Leagues pulled a week at a time carry the source's week
    numbers on their Week objects -- they're how you trace a game back to
    the request that brought it -- and rebuilding the weeks from game dates
    would quietly throw that away. Leagues pulled a day at a time put
    everything in one week, so folding them through here is a no-op on
    their grouping and works the same.
    """
    assert all(s.year == seasons[0].year for s in seasons)

    latest: Dict[str, Tuple[int, Game]] = {}
    for season in seasons:
        for week in season.weeks:
            for game in week.games:
                previous = latest.get(game.game_id)
                if supersedes(game, previous[1] if previous else None):
                    latest[game.game_id] = (week.number, game)

    by_number: Dict[int, List[Game]] = {}
    for number, game in latest.values():
        by_number.setdefault(number, []).append(game)

    # dict.fromkeys rather than a sorted set: trouble params hold enums
    # (SeasonType, NcaaFbGroup), and enums don't order, so sorting them
    # raises. This dedupes and keeps a stable order without asking them to
    # compare.
    trouble: Dict[Any, None] = {}
    for season in seasons:
        for param in season.trouble_params or []:
            trouble[param] = None

    return Season(
        [
            Week(sorted(games, key=lambda g: g.date), number)
            for number, games in sorted(by_number.items())
        ],
        seasons[0].year,
        list(trouble),
        # The freshest tag wins. A season pickled before `season_start`
        # existed comes back untagged, and merging one of those in must not
        # untag the season it's being folded into.
        next(
            (s.season_start for s in reversed(seasons) if s.season_start is not None),
            None,
        ),
    )


class OverlappingWeeksError(ValueError):
    """
    Raised when a season's weeks cover overlapping stretches of time.

    This means games are grouped into the wrong week, which re-sorting
    can't fix -- the grouping itself has to be rebuilt.
    """


def check_weeks_dont_overlap(weeks: List[Week]) -> None:
    """
    Check that no two weeks cover overlapping stretches of time.

    Assumes `weeks` is already in chronological order.
    """
    dated_weeks = [w for w in weeks if w.games]
    for earlier, later in zip(dated_weeks, dated_weeks[1:]):
        earlier_end, later_start = earlier.end, later.start
        # Both weeks have games, so these are set.
        assert earlier_end is not None and later_start is not None
        if earlier_end > later_start:
            raise OverlappingWeeksError(
                f"Week {earlier.number} ({earlier.start} to {earlier_end}) "
                f"overlaps week {later.number} ({later_start} to {later.end}). "
                "Games are probably grouped into the wrong week."
            )


def iter_weeks(season: Season, validate: bool = True) -> Iterator[Week]:
    """
    Walk a season's weeks in chronological order. Use `week.games_in_order`
    to walk the games inside each week.

    This is the intended way to traverse a season -- iterating `.weeks` and
    `.games` directly gives you whatever order the season was built in, in
    whatever weeks the source put the games in.

    A season with a `season_start` is walked as calendar weeks rebuilt from
    the game dates, so `week.number` here won't always match the number the
    source used (`.weeks` keeps those). Without one, the source's weeks are
    walked in date order.

    Raises OverlappingWeeksError if the weeks overlap in time, which means
    the grouping is wrong rather than merely unsorted. Pass validate=False
    to walk the weeks anyway. Calendar weeks can't overlap, so this only
    fires for a season walked in its source's numbering.
    """
    weeks = season.calendar_weeks if season.season_start else season.weeks_in_order
    if validate:
        check_weeks_dont_overlap(weeks)
    # Deliberately not a generator, so validation happens when this is
    # called rather than when the caller first iterates.
    return iter(weeks)
