import json
from datetime import date
from typing import Dict, List, Optional
from unittest.mock import patch

import pytest

from . import espn_odds as espn_odds_module
from .espn_odds import (
    ESPN_DEFAULT_LIMIT,
    MIN_EVENTS_TO_EXPECT_A_PRICE,
    ODDS_PAGE_LIMIT,
    NoPricesFound,
    OddsTruncated,
    get_odds_range,
)

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


class _FakeEspnByLimit:
    """
    Stands in for `web.get` for the days where the answer depends on what
    `limit` was asked for -- which is the whole failure this guards.
    """

    def __init__(self, by_limit: Dict[tuple, List[dict]]):
        self.by_limit = by_limit
        self.requested: List[tuple] = []

    async def __call__(self, url, parameters):
        key = (parameters["dates"], int(parameters["limit"]))
        self.requested.append(key)
        return _FakeContent({"events": self.by_limit[key]})


async def test_a_day_that_comes_back_full_raises() -> None:
    """
    A day is the narrowest request ESPN takes, so there is nothing left to
    split. A short day is invisible downstream -- it looks exactly like a
    quiet one -- so the run stops instead of writing a snapshot that claims
    to be a whole day.
    """
    full = [_event(str(i), "2026-09-03") for i in range(ODDS_PAGE_LIMIT)]
    fake = _FakeEspn({"20260903": full})

    with _patch_espn(fake), pytest.raises(OddsTruncated, match="full"):
        [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 9, 3), end=date(2026, 9, 3)
            )
        ]


async def test_a_genuine_twenty_five_event_day_is_kept() -> None:
    """
    25 is ESPN's default page, so a day that size is ambiguous -- but a real
    one answers the same however it is asked, and must not fail the run.
    """
    real = [_event(str(i), "2026-09-03") for i in range(ESPN_DEFAULT_LIMIT)]
    fake = _FakeEspnByLimit(
        {
            ("20260903", ODDS_PAGE_LIMIT): real,
            ("20260903", ESPN_DEFAULT_LIMIT * 2): real,
        }
    )

    with patch.object(espn_odds_module, "get", fake):
        odds = [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 9, 3), end=date(2026, 9, 3)
            )
        ]

    assert len(odds) == ESPN_DEFAULT_LIMIT
    # The second request is the cost of telling the two cases apart, and it
    # is only paid on a day that lands exactly on the default.
    assert len(fake.requested) == 2


async def test_a_limit_espn_ignores_raises() -> None:
    """
    The 1000 -> 25 fallback, which is what this whole check is for.

    Asking college-football for a single Saturday at limit=1000 returned 25
    of the day's 80 events, under the cap and so indistinguishable from a
    quiet day. Asking for a number ESPN honours returns the other 55, and
    the disagreement between the two is the only evidence there is.
    """
    served = [_event(str(i), "2026-09-03") for i in range(ESPN_DEFAULT_LIMIT)]
    real = [_event(str(i), "2026-09-03") for i in range(80)]
    fake = _FakeEspnByLimit(
        {
            ("20260903", ODDS_PAGE_LIMIT): served,
            ("20260903", ESPN_DEFAULT_LIMIT * 2): real,
        }
    )

    with patch.object(espn_odds_module, "get", fake):
        with pytest.raises(OddsTruncated, match="ignoring the limit"):
            [
                o
                async for o in get_odds_range(
                    _URL, start=date(2026, 9, 3), end=date(2026, 9, 3)
                )
            ]


async def test_the_cap_asked_for_is_one_espn_honours() -> None:
    """
    Not just any high number: 1000 is *above* the threshold on a single-day
    request and gets silently swapped for 25. The measured honoured band on
    college-football/groups=80 is limit<=500.
    """
    assert ODDS_PAGE_LIMIT <= 500
    assert ODDS_PAGE_LIMIT > ESPN_DEFAULT_LIMIT * 2


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


async def test_a_range_that_lists_games_and_prices_none_raises() -> None:
    """
    What a schema change looks like from in here.

    `_get_odds_page` skips an event with no `odds` key, so if ESPN renames
    or moves that key every event goes quiet at once and the pull writes an
    empty snapshot -- which reads exactly like an off-season day.
    """
    unpriced = [
        _event(str(i), "2026-09-03", priced=False)
        for i in range(MIN_EVENTS_TO_EXPECT_A_PRICE)
    ]
    fake = _FakeEspn({"20260903": unpriced})

    with _patch_espn(fake), pytest.raises(NoPricesFound, match="priced none"):
        [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 9, 3), end=date(2026, 9, 3)
            )
        ]


async def test_an_out_of_season_range_is_not_a_failure() -> None:
    """
    No games is the ordinary answer for most of the year, and has to stay
    distinguishable from games-but-no-prices.
    """
    fake = _FakeEspn({}, default=[])

    with _patch_espn(fake):
        odds = [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 6, 1), end=date(2026, 6, 3)
            )
        ]

    assert odds == []


async def test_a_couple_of_unpriced_games_are_not_a_failure() -> None:
    """
    An exhibition or an all-star day comes back listed and unpriced, and a
    scheduled pull should not die over it -- see the floor's comment.
    """
    few = [
        _event(str(i), "2026-09-03", priced=False)
        for i in range(MIN_EVENTS_TO_EXPECT_A_PRICE - 1)
    ]
    fake = _FakeEspn({"20260903": few})

    with _patch_espn(fake):
        odds = [
            o
            async for o in get_odds_range(
                _URL, start=date(2026, 9, 3), end=date(2026, 9, 3)
            )
        ]

    assert odds == []
