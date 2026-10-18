from pathlib import Path

import pytest

from . import web
from .web import CACHE_BUSTER_PARAM, _cache_busted, get

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


def test_espn_api_requests_carry_a_fresh_cache_buster() -> None:
    week: dict[str, str | int] = {"dates": 2026, "week": 6}

    first = _cache_busted(SCOREBOARD, week)
    second = _cache_busted(SCOREBOARD, week)

    assert first is not None and second is not None
    assert first[CACHE_BUSTER_PARAM] != second[CACHE_BUSTER_PARAM]
    assert {k: v for k, v in first.items() if k != CACHE_BUSTER_PARAM} == week
    # The caller's dict is what names the cache file, so it mustn't change.
    assert week == {"dates": 2026, "week": 6}


def test_a_request_without_parameters_still_gets_one() -> None:
    busted = _cache_busted(SCOREBOARD, None)

    assert busted is not None and set(busted) == {CACHE_BUSTER_PARAM}


def test_other_hosts_are_sent_as_given() -> None:
    page = "https://www.espn.com/mens-college-basketball/matchup?gameId=401263346"

    assert _cache_busted(page, None) is None
    assert _cache_busted(page, {"a": 1}) == {"a": 1}


class _Response:
    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *_) -> None:
        pass

    async def read(self) -> bytes:
        return b'{"events": []}'


class _Session:
    sent: list[dict] = []

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_) -> None:
        pass

    def get(self, url: str, params: dict, raise_for_status: bool) -> _Response:
        self.sent.append(params)
        return _Response()


async def test_the_cache_file_is_named_without_the_buster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENDGAME_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(web.aiohttp, "ClientSession", _Session)
    _Session.sent = []

    content = await get(SCOREBOARD, {"dates": 20261018})
    await content.save_if_necessary()
    again = await get(SCOREBOARD, {"dates": 20261018})

    assert len(_Session.sent) == 1
    assert CACHE_BUSTER_PARAM in _Session.sent[0]
    assert again.data == b'{"events": []}'
    (cached,) = (tmp_path / "web").iterdir()
    assert CACHE_BUSTER_PARAM + "=" not in cached.name
