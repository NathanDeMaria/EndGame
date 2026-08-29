"""
`get_games` and the event filter it takes.

What makes an event worth parsing is the league's business, not this
module's -- these cover the plumbing, and `daily_test` covers the rule
the day-pulled leagues pass in.
"""

import json
from typing import Dict, List
from unittest.mock import AsyncMock, patch

from . import espn_games as espn_games_module
from .espn_games import get_games, keep_every_event


def _event(event_id: str, home: str = "Home", away: str = "Away") -> Dict:
    return {
        "id": event_id,
        "name": f"{away} at {home}",
        "date": "2024-01-20T00:00Z",
        "status": {"type": {"completed": True}},
        "competitions": [
            {
                "neutralSite": False,
                "competitors": [
                    {
                        "team": {"displayName": home},
                        "score": "3",
                        "homeAway": "home",
                    },
                    {
                        "team": {"displayName": away},
                        "score": "2",
                        "homeAway": "away",
                    },
                ],
            }
        ],
    }


def _patch_get(events: List[Dict]):
    content = AsyncMock()
    content.data = json.dumps({"events": events})
    return patch.object(espn_games_module, "get", AsyncMock(return_value=content))


async def test_every_event_is_parsed_by_default() -> None:
    """
    The leagues that scope their request want everything that came back.
    """
    with _patch_get([_event("a"), _event("b")]):
        games = await get_games("http://espn", {})
    assert [g.game_id for g in games] == ["a", "b"]


async def test_filter_chooses_what_is_parsed() -> None:
    with _patch_get([_event("a"), _event("b"), _event("c")]):
        games = await get_games("http://espn", {}, lambda e: e["id"] != "b")
    assert [g.game_id for g in games] == ["a", "c"]


async def test_filter_can_drop_everything() -> None:
    """
    A day of nothing but exhibitions is empty, not an error.
    """
    with _patch_get([_event("a")]):
        games = await get_games("http://espn", {}, lambda e: False)
    assert games == []


def test_keep_every_event_is_the_default() -> None:
    assert keep_every_event(_event("a"))
    assert keep_every_event({})


def _unscored_event(event_id: str, completed: bool = False) -> Dict:
    """
    A fixture ESPN listed and never filled in: no `score` on either side.

    Modelled on the two in the 2002 WNBA postseason (ids 220820009 and
    220820004), which are "STATUS_TBD" with no score anywhere.
    """
    event = _event(event_id)
    event["status"] = {"type": {"completed": completed}}
    for competitor in event["competitions"][0]["competitors"]:
        del competitor["score"]
    return event


async def test_unscored_event_does_not_raise() -> None:
    """
    A missing score used to take a whole season's fetch down on int(None).
    """
    with _patch_get([_unscored_event("tbd")]):
        games = await get_games("http://espn", {})
    assert games == []


async def test_unscored_event_is_unfinished_even_if_espn_says_otherwise() -> None:
    """
    No score means it hasn't been played, whatever `completed` claims.
    """
    with _patch_get([_unscored_event("tbd", completed=True)]):
        games = await get_games("http://espn", {})
    # get_games only returns completed games, so an unscored one never
    # reaches a caller either way.
    assert games == []


async def test_unscored_event_keeps_the_day_out_of_the_cache() -> None:
    """
    The reason an unscored game is kept as unfinished rather than dropped:
    a response holding one mustn't be cached, or a week gets frozen before
    its games have been played.
    """
    content = AsyncMock()
    content.data = json.dumps({"events": [_event("played"), _unscored_event("tbd")]})
    with patch.object(espn_games_module, "get", AsyncMock(return_value=content)):
        games = await get_games("http://espn", {})

    assert [g.game_id for g in games] == ["played"]
    content.save_if_necessary.assert_not_awaited()


async def test_a_fully_played_day_is_cached() -> None:
    content = AsyncMock()
    content.data = json.dumps({"events": [_event("played")]})
    with patch.object(espn_games_module, "get", AsyncMock(return_value=content)):
        await get_games("http://espn", {})

    content.save_if_necessary.assert_awaited()


async def test_a_zero_zero_game_is_still_a_result() -> None:
    """
    Missing isn't zero: the NHL played to ties until 2005-06, so a real 0-0
    has to survive.
    """
    event = _event("tie")
    for competitor in event["competitions"][0]["competitors"]:
        competitor["score"] = "0"
    with _patch_get([event]):
        games = await get_games("http://espn", {})

    assert [(g.game_id, g.home_score, g.away_score, g.completed) for g in games] == [
        ("tie", 0, 0, True)
    ]
