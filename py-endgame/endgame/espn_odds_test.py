import json
from datetime import date
from typing import Dict, List, Optional
from unittest.mock import patch

from . import espn_odds as espn_odds_module
from .espn_odds import ODDS_PAGE_LIMIT, get_odds_range

_URL = "https://example.test/scoreboard"


def _event(competition_id: str, day: str, priced: bool = True) -> dict:
    competition: dict = {"id": competition_id}
    if priced:
        competition["odds"] = [{"details": "HOME -3.5"}]
    return {"date": f"{day}T23:00Z", "competitions": [competition]}


class _FakeContent:
    def __init__(self, tree: dict):
        self.data = json.dumps(tree).encode()


class _FakeEspn:
    """
    Stands in for `web.get`, recording the `dates` of every request and
    answering each one from `responses`.
    """

    def __init__(
        self, responses: Dict[str, List[dict]], default: Optional[List[dict]] = None
    ):
        self.responses = responses
        self.default = default if default is not None else []
        self.requested: List[str] = []

    async def __call__(self, url, parameters):
        dates = parameters["dates"]
        self.requested.append(dates)
        return _FakeContent({"events": self.responses.get(dates, self.default)})


def _patch_espn(fake: _FakeEspn):
    return patch.object(espn_odds_module, "get", fake)


async def test_odds_carry_the_competition_and_the_day_it_is_played() -> None:
    """
    A snapshot can now span months, so the record has to say which game it
    is about -- the S3 key it lands in only says when it was read.
    """
    fake = _FakeEspn({"20260903": [_event("401", "2026-09-03")]})

    with _patch_espn(fake):
        odds = [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 9, 3), end=date(2026, 9, 3)
            )
        ]

    assert odds == [
        {
            "competition_id": "401",
            "date": "2026-09-03T23:00Z",
            "odds": [{"details": "HOME -3.5"}],
        }
    ]


async def test_games_with_no_price_are_skipped() -> None:
    fake = _FakeEspn(
        {
            "20260903": [
                _event("401", "2026-09-03"),
                _event("402", "2026-09-03", priced=False),
            ]
        }
    )

    with _patch_espn(fake):
        odds = [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 9, 3), end=date(2026, 9, 3)
            )
        ]

    assert [o["competition_id"] for o in odds] == ["401"]


async def test_a_range_is_one_request_per_day() -> None:
    """
    ESPN 400s a `dates` range as of 2026-09-16, so a fortnight of odds is
    fourteen requests. Both ends are included.
    """
    fake = _FakeEspn({})

    with _patch_espn(fake):
        [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 9, 3), end=date(2026, 9, 16), chunk_days=14
            )
        ]

    assert sorted(fake.requested) == [f"202609{day:02d}" for day in range(3, 17)]


async def test_no_request_ever_asks_for_a_span() -> None:
    """
    The regression guard. A `dates` of `20260901-20260921` is a 400 from
    ESPN and a day of missing odds here, and it took two days to notice.
    """
    fake = _FakeEspn({})

    with _patch_espn(fake):
        [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 9, 1), end=date(2026, 9, 21), chunk_days=7
            )
        ]

    assert fake.requested
    assert not [dates for dates in fake.requested if "-" in dates]


async def test_a_single_day_is_still_a_single_request() -> None:
    fake = _FakeEspn({})

    with _patch_espn(fake):
        [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 9, 3), end=date(2026, 9, 3)
            )
        ]

    assert fake.requested == ["20260903"]


async def test_odds_come_back_in_date_order_though_the_days_are_parallel() -> None:
    """
    The days are fetched concurrently, but a snapshot is appended to rather
    than keyed by game, so the order it is written in is the order it is
    read back in.
    """
    fake = _FakeEspn(
        {
            "20260901": [_event("401", "2026-09-01")],
            "20260902": [_event("402", "2026-09-02")],
            "20260903": [_event("403", "2026-09-03")],
        }
    )

    with _patch_espn(fake):
        odds = [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 9, 1), end=date(2026, 9, 3)
            )
        ]

    assert [o["competition_id"] for o in odds] == ["401", "402", "403"]


async def test_a_day_that_comes_back_full_is_kept_and_logged(
    caplog,
) -> None:
    """
    A day is the narrowest request ESPN takes, so there is nothing left to
    split. Better to keep the (probably incomplete) day and say so than to
    drop it silently.
    """
    full = [_event(str(i), "2026-09-03") for i in range(ODDS_PAGE_LIMIT)]
    fake = _FakeEspn({"20260903": full})

    with _patch_espn(fake), caplog.at_level("WARNING"):
        odds = [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 9, 3), end=date(2026, 9, 3)
            )
        ]

    assert len(odds) == ODDS_PAGE_LIMIT
    assert fake.requested == ["20260903"]
    assert "probably incomplete" in caplog.text


async def test_the_cap_is_asked_for_on_every_request() -> None:
    """
    The old limit of 300 silently cut a week of NCAABB short.
    """
    seen = []

    async def fake_get(url, parameters):
        seen.append(parameters["limit"])
        return _FakeContent({"events": []})

    with patch.object(espn_odds_module, "get", fake_get):
        [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 9, 1), end=date(2026, 9, 21), chunk_days=7
            )
        ]

    assert seen == [ODDS_PAGE_LIMIT] * 21
