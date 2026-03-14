from datetime import date

from .plays import get_plays, get_plays_for_day
from .ncaabb import NcaabbGender


async def test_plays() -> None:
    plays = await get_plays("401825568", NcaabbGender.mens)
    assert len(plays) == 492


async def test_day_plays() -> None:
    pbps = get_plays_for_day(date(2026, 3, 1), NcaabbGender.womens)
    assert sum([len(p["plays"]) async for p in pbps]) == 17720
