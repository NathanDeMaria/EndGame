from datetime import datetime
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


class Season(NamedTuple):
    weeks: List[Week]
    year: int
