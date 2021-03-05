from dataclasses import dataclass, asdict
from typing import Dict, Union


Primitive = Union[bool, int, float, str]


@dataclass
class PossessionSide:
    """
    Number of possessions resulting in each # of points from a game
    """

    # These are the stats when the "home" team is on offense
    # If it's a neutral site game, this is the team
    # in the "home" slot on ESPN. I'm pretty sure that's consistent
    home_team: bool
    zero_points: int
    one_point: int
    two_points: int
    three_points: int
    game_id: str

    def to_dict(self) -> Dict[str, Primitive]:
        """
        Convert to a serializable dict
        """
        return asdict(self)
