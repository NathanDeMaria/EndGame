from datetime import date

import pytest

from .ncaabb import NcaabbGender
from .plays import get_plays, get_plays_for_day

# These two call ESPN for real and assert exact counts against whatever it
# serves back, so they check the live API rather than this code. Worth
# keeping and worth running -- a `pytest` with no arguments still runs them
# -- but not worth wiring a merge gate to, which is what putting them in CI
# would do: the build would then fail for an ESPN outage, a rate limit, or a
# play-by-play someone upstream revised.
pytestmark = pytest.mark.network


async def test_plays() -> None:
    plays = await get_plays("401825568", NcaabbGender.mens)
    assert len(plays) == 492


async def test_day_plays() -> None:
    pbps = get_plays_for_day(date(2026, 3, 1), NcaabbGender.womens)
    assert sum([len(p["plays"]) async for p in pbps]) == 17720
