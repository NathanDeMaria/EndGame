from datetime import datetime
from enum import Enum
from typing import Any, Dict, Iterator, List, NamedTuple, Optional


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
    """

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

    @property
    def weeks_in_order(self) -> List[Week]:
        """
        The weeks, sorted by when their first game happened.

        Prefer this over `.weeks`: the raw list's order depends on how the
        season happened to be built and merged. Sorts on game dates rather
        than `week.number` because the numbering itself has been unreliable.

        Weeks with no games sort last, since they have no date to sort on.
        """
        return sorted(self.weeks, key=_week_sort_key)


def _week_sort_key(week: Week) -> "tuple[int, Any]":
    start = week.start
    if start is None:
        # Tuples compare left-to-right, so the leading 0/1 keeps these
        # week numbers from ever being compared against a datetime.
        return (1, week.number)
    return (0, start)


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
    `.games` directly gives you whatever order the season was built in.

    Raises OverlappingWeeksError if the weeks overlap in time, which means
    the grouping is wrong rather than merely unsorted. Pass validate=False
    to walk the weeks anyway.
    """
    weeks = season.weeks_in_order
    if validate:
        check_weeks_dont_overlap(weeks)
    # Deliberately not a generator, so validation happens when this is
    # called rather than when the caller first iterates.
    return iter(weeks)
