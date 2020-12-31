from datetime import datetime
from enum import Enum
from typing import NamedTuple, Optional, List, Dict


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
