"""The guard that stops a broken odds pull from overwriting a good one."""

import json
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from . import cli as cli_module
from .cli import OddsCoverageDropped, _check_odds_coverage

_BUCKET = "test-bucket"
_CHICAGO = ZoneInfo("America/Chicago")
_AS_OF = datetime(2026, 9, 12, 10, 2, tzinfo=_CHICAGO)
_NEW_KEY = "odds/ncaafb/2026-09-12/10-02-near.json"
_OLD_KEY = "odds/ncaafb/2026-09-11/09-32-near.json"


def _records(day: str, n: int) -> list:
    return [{"competition_id": str(i), "date": f"{day}T23:00Z"} for i in range(n)]


# A Saturday evening's ncaafb slate as ESPN dates it: 7pm, 8pm, 9pm and 10pm
# Chicago kickoffs, all on the *next* UTC calendar day.
_SATURDAY_NIGHT = [
    {"competition_id": f"{hour}-{i}", "date": f"2026-09-20T{hour:02d}:00Z"}
    for hour, n in ((0, 4), (1, 1), (2, 4), (3, 3))
    for i in range(n)
]
_EVENING_KEY = "odds/ncaafb/2026-09-19/19-01-today.json"
_EIGHT_PM = datetime(2026, 9, 19, 20, 0, tzinfo=_CHICAGO)


def _patch_s3(keys: list, previous: list):
    async def fake_list(bucket, prefix):
        for key in keys:
            yield key

    async def fake_read(bucket, key):
        return json.dumps(previous).encode()

    return patch.multiple(cli_module, list_all_keys=fake_list, read_bytes=fake_read)


async def test_a_future_date_losing_most_of_its_games_raises() -> None:
    """
    The shape every silent failure here takes: the pull succeeds, the
    snapshot looks like a quiet day, and a date that already had 40 games
    now has 9.
    """
    with _patch_s3([_OLD_KEY], _records("2026-09-19", 40)):
        with pytest.raises(OddsCoverageDropped, match="2026-09-19"):
            await _check_odds_coverage(
                _BUCKET,
                "ncaafb",
                "near",
                _NEW_KEY,
                _records("2026-09-19", 9),
                _AS_OF,
            )


async def test_a_future_date_holding_steady_passes() -> None:
    with _patch_s3([_OLD_KEY], _records("2026-09-19", 40)):
        await _check_odds_coverage(
            _BUCKET,
            "ncaafb",
            "near",
            _NEW_KEY,
            _records("2026-09-19", 39),
            _AS_OF,
        )


async def test_a_past_date_shedding_games_is_fine() -> None:
    """
    Once a game is final ESPN stops carrying a price for it, so yesterday
    emptying out is what working looks like.
    """
    with _patch_s3([_OLD_KEY], _records("2026-09-05", 40)):
        await _check_odds_coverage(_BUCKET, "ncaafb", "near", _NEW_KEY, [], _AS_OF)


async def test_an_evening_slate_thinning_at_kickoff_is_fine() -> None:
    """
    The 2026-09-19 false alarm: 12 priced games at 7pm Chicago, 8 at 8pm, 3
    at 10pm, every drop a kickoff. The games share a UTC date that the
    Chicago clock still calls tomorrow, so a calendar cutoff couldn't see
    that most of them had already started.
    """
    with _patch_s3([_EVENING_KEY], _SATURDAY_NIGHT):
        await _check_odds_coverage(
            _BUCKET, "ncaafb", "today", _NEW_KEY, _SATURDAY_NIGHT[5:], _EIGHT_PM
        )


async def test_an_evening_slate_losing_its_unstarted_games_still_raises() -> None:
    """Kickoffs excuse the games that kicked off, not the ones that didn't."""
    with _patch_s3([_EVENING_KEY], _SATURDAY_NIGHT):
        with pytest.raises(OddsCoverageDropped, match="had 7 .* has 2"):
            await _check_odds_coverage(
                _BUCKET, "ncaafb", "today", _NEW_KEY, _SATURDAY_NIGHT[-2:], _EIGHT_PM
            )


async def test_a_small_date_is_not_evidence() -> None:
    """2 -> 0 is a book changing its mind, not a pipeline breaking."""
    with _patch_s3([_OLD_KEY], _records("2026-09-19", 2)):
        await _check_odds_coverage(_BUCKET, "ncaafb", "near", _NEW_KEY, [], _AS_OF)


async def test_the_first_pull_of_a_horizon_has_nothing_to_compare() -> None:
    with _patch_s3([], []):
        await _check_odds_coverage(_BUCKET, "ncaafb", "season", _NEW_KEY, [], _AS_OF)


async def test_only_the_same_horizon_is_compared() -> None:
    """
    `today` reaches one day and `season` reaches months, so comparing across
    them would share almost no dates and quietly check nothing.
    """
    other = "odds/ncaafb/2026-09-11/09-32-today.json"
    with _patch_s3([other], _records("2026-09-19", 40)):
        await _check_odds_coverage(_BUCKET, "ncaafb", "near", _NEW_KEY, [], _AS_OF)
