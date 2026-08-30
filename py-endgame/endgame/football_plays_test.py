import pytest

from .football_plays import FootballLeague, get_game_plays

# These call ESPN for real and assert counts against whatever it serves back,
# so they check the live API rather than this code -- same trade as the
# ncaabb play-by-play tests, and marked the same way so CI skips them.
pytestmark = pytest.mark.network


async def test_nfl_plays() -> None:
    # Chiefs-Ravens, the 2024 season opener
    drives = await get_game_plays("401671789", FootballLeague.nfl)

    assert len(drives) == 20
    assert sum(len(drive["plays"]) for drive in drives) == 188
    assert drives[0]["plays"][0]["type"]["text"] == "Kickoff"


async def test_ncaafb_plays() -> None:
    # Stanford at USC, 2005: old enough that ESPN sends a thinner play -- no
    # statYardage, no isTurnover -- which is the case for keeping these raw
    drives = await get_game_plays("253090030", FootballLeague.ncaafb)

    assert sum(len(drive["plays"]) for drive in drives) == 182


async def test_plays_for_a_game_espn_has_none_for() -> None:
    """
    A game with no play-by-play comes back empty, not as an error.

    This one is Lycoming at Moravian, a 2024 D3 game: the NCAAFB seasons are
    full of them, and a puller that treated an empty response as a failure
    would never get through a week.
    """
    assert await get_game_plays("401673416", FootballLeague.ncaafb) == []
