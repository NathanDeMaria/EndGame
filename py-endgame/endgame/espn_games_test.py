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
