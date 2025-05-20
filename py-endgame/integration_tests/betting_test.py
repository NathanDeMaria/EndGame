from endgame.ncaabb.box_score.all import get_box_score, Betting
from endgame.ncaabb import NcaabbGender


async def test_betting() -> None:
    test_id = "401488870"
    box_score = await get_box_score(NcaabbGender.womens, test_id)
    assert box_score.betting == Betting(home_spread=11.5, over_under=1.0)
