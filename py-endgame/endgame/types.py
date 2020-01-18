from datetime import datetime
from typing import NamedTuple, Optional, List, Dict


class Game(NamedTuple):
    team: str
    team_score: int
    opponent: str
    opponent_score: int
    is_home: Optional[bool]  # None if neutral site
    completed: bool
    datetime: datetime

    @property
    def column_names(self) -> List[str]:
        return list(self._asdict().keys())
    
    def to_dict(self) -> Dict:
        return self._asdict()


class Week(NamedTuple):
    games: List[Game]
    number: int


class Season(NamedTuple):
    weeks: List[Week]
    year: int
