from datetime import datetime
from enum import Enum
from typing import NamedTuple, Optional, List, Dict


class Game(NamedTuple):
    home: str
    home_score: int
    away: str
    away_score: int
    neutral_site: bool
    completed: bool
    date: datetime

    @property
    def column_names(self) -> List[str]:
        return list(self.to_dict().keys())
    
    def to_dict(self) -> Dict:
        return self._asdict()


class Week(NamedTuple):
    games: List[Game]
    number: int


class NcaaFbGroup(Enum):
    fbs = 80
    fcs = 81
    d23 = 35  # two AND three


class SeasonType(Enum):
    regular = 2
    post = 3


class WeekParams(NamedTuple):
    year: int
    week: int
    season_type: SeasonType
    # Only have these on the NCAAFB for now...fine?
    group: NcaaFbGroup


class Season(NamedTuple):
    weeks: List[Week]
    year: int
    trouble_weeks: List[WeekParams] = None
