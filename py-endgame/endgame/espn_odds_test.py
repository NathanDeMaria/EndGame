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


async def test_a_range_inside_one_chunk_is_a_single_request() -> None:
    """
    The whole point: a fortnight of odds costs one request, not fourteen.
    """
    fake = _FakeEspn({})

    with _patch_espn(fake):
        [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 9, 3), end=date(2026, 9, 16), chunk_days=14
            )
        ]

    assert fake.requested == ["20260903-20260916"]


async def test_a_long_range_is_asked_for_a_chunk_at_a_time() -> None:
    fake = _FakeEspn({})

    with _patch_espn(fake):
        [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 9, 1), end=date(2026, 9, 21), chunk_days=7
            )
        ]

    assert fake.requested == [
        "20260901-20260907",
        "20260908-20260914",
        "20260915-20260921",
    ]


async def test_a_truncated_response_is_split_in_half_and_re_asked() -> None:
    """
    ESPN caps a response and says nothing about it, so a full one can't be
    trusted -- see ODDS_PAGE_LIMIT.
    """
    full = [_event(str(i), "2026-09-03") for i in range(ODDS_PAGE_LIMIT)]
    fake = _FakeEspn(
        {"20260901-20260908": full},
        default=[_event("401", "2026-09-03")],
    )

    with _patch_espn(fake):
        odds = [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 9, 1), end=date(2026, 9, 8), chunk_days=8
            )
        ]

    # The halves are consecutive and don't overlap: a shared day would
    # report the same game's price twice.
    assert fake.requested == [
        "20260901-20260908",
        "20260901-20260904",
        "20260905-20260908",
    ]
    # What's kept is the halves' answers, not the truncated response.
    assert len(odds) == 2


async def test_a_split_keeps_splitting_until_the_pieces_fit() -> None:
    full = [_event(str(i), "2026-09-03") for i in range(ODDS_PAGE_LIMIT)]
    fake = _FakeEspn(
        {
            "20260901-20260904": full,
            "20260901-20260902": full,
        },
        default=[_event("401", "2026-09-03")],
    )

    with _patch_espn(fake):
        [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 9, 1), end=date(2026, 9, 4), chunk_days=4
            )
        ]

    assert fake.requested == [
        "20260901-20260904",
        "20260901-20260902",
        "20260901",
        "20260902",
        "20260903-20260904",
    ]


async def test_a_single_day_that_still_comes_back_full_is_kept_and_logged(
    caplog,
) -> None:
    """
    Nothing left to split. Better to keep the (probably incomplete) day and
    say so than to drop it silently.
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

    assert seen == [ODDS_PAGE_LIMIT] * 3
