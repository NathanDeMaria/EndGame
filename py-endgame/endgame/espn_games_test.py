"""
`get_games` and the event filter it takes.

What makes an event worth parsing is the league's business, not this
module's -- these cover the plumbing, and `daily_test` covers the rule
the day-pulled leagues pass in.
"""

import json
import pickle
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from . import espn_games as espn_games_module
from .espn_games import get_games, keep_every_event
from .types import Game

_WHEN = datetime(2024, 1, 20, tzinfo=timezone.utc)


def _event(
    event_id: str,
    home: str = "Home",
    away: str = "Away",
    *,
    completed: bool = True,
    status: str | None = None,
) -> dict:
    if status is None:
        status = "STATUS_FINAL" if completed else "STATUS_SCHEDULED"
    return {
        "id": event_id,
        "name": f"{away} at {home}",
        "date": "2024-01-20T00:00Z",
        "status": {"type": {"completed": completed, "name": status}},
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


def _patch_get(events: list[dict]):
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


def _unscored_event(
    event_id: str, completed: bool = False, status: str = "STATUS_TBD"
) -> dict:
    """
    A fixture ESPN listed and never filled in: no `score` on either side.

    Modelled on the two in the 2002 WNBA postseason (ids 220820009 and
    220820004), which are "STATUS_TBD" with no score anywhere.
    """
    event = _event(event_id)
    event["status"] = {"type": {"completed": completed, "name": status}}
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


class TestStatus:
    """ESPN's own word for what state a game is in.

    `completed` says result / not-a-result and stops there. ESPN sends a
    score for every game whatever its state -- scheduled and cancelled
    games both come back 0-0 -- so once unplayed games are kept, this is
    the only thing separating them.
    """

    async def test_it_is_carried_through(self) -> None:
        with _patch_get([_event("played")]):
            [game] = await get_games("http://espn", {})

        assert game.status == "STATUS_FINAL"

    async def test_it_separates_games_completed_cannot(self) -> None:
        """Scheduled, cancelled and scoreless-so-far are all 0-0, unplayed."""
        events = [
            _event("scheduled", completed=False, status="STATUS_SCHEDULED"),
            _event("called-off", completed=False, status="STATUS_CANCELED"),
            _event("underway", completed=False, status="STATUS_IN_PROGRESS"),
        ]
        with _patch_get(events):
            games = await get_games("http://espn", {}, include_unplayed=True)

        assert {g.completed for g in games} == {False}
        assert [g.status for g in games] == [
            "STATUS_SCHEDULED",
            "STATUS_CANCELED",
            "STATUS_IN_PROGRESS",
        ]

    async def test_a_missing_status_is_empty_not_an_error(self) -> None:
        """Every status ESPN has sent is a string we've never had to know."""
        event = _event("odd")
        del event["status"]["type"]["name"]
        with _patch_get([event]):
            [game] = await get_games("http://espn", {})

        assert game.status == ""

    async def test_it_is_not_rewritten_to_agree_with_completed(self) -> None:
        """An unscored fixture is unplayed; ESPN still called it final.

        `completed` is what we concluded, `status` is what ESPN said. A
        reader that needs to know they disagreed can only see it if this
        stays verbatim.
        """
        event = _unscored_event("tbd", completed=True, status="STATUS_FINAL")
        with _patch_get([event]):
            [game] = await get_games("http://espn", {}, include_unplayed=True)

        assert (game.completed, game.status) == (False, "STATUS_FINAL")

    async def test_a_real_unscored_fixture_keeps_its_own_status(self) -> None:
        """The 2002 WNBA ones, which ESPN never called final either."""
        with _patch_get([_unscored_event("tbd")]):
            [game] = await get_games("http://espn", {}, include_unplayed=True)

        assert (game.completed, game.status) == (False, "STATUS_TBD")


class TestPickleCompatibility:
    """Every Game in the bucket was pickled before `status` existed.

    A NamedTuple unpickles by calling the current class with the values it
    was saved with, so a new field without a default raises TypeError on
    every season already saved -- which is most of what the pipeline owns.
    """

    def test_pickle_loads_a_game_by_calling_the_class(self) -> None:
        """The mechanism the test below depends on, pinned."""
        game = Game("Home", 3, "Away", 2, False, True, _WHEN, "id", "STATUS_FINAL")

        assert game.__getnewargs__() == tuple(game)  # ty: ignore[unresolved-attribute]
        assert pickle.loads(pickle.dumps(game)) == game

    def test_a_game_saved_without_a_status_still_loads(self) -> None:
        saved = ("Home", 3, "Away", 2, False, True, _WHEN, "old")
        assert len(saved) == len(Game._fields) - 1

        game = Game(*saved)

        assert game.game_id == "old"
        assert game.status == ""

    def test_status_is_the_only_defaulted_field(self) -> None:
        """A second one would make the field order load-bearing."""
        assert set(Game._field_defaults) == {"status"}


class TestIncludeUnplayed:
    """Keeping the games ESPN hasn't finished.

    Off by default: most callers want results, and the ones that walk a
    season game by game (NCAABB's possessions and box scores) have nothing
    to fetch for a game that hasn't been played.
    """

    async def test_unplayed_games_are_dropped_by_default(self) -> None:
        with _patch_get([_event("played"), _event("kicking-off", completed=False)]):
            games = await get_games("http://espn", {})

        assert [g.game_id for g in games] == ["played"]

    async def test_they_are_kept_when_asked_for(self) -> None:
        with _patch_get([_event("played"), _event("kicking-off", completed=False)]):
            games = await get_games("http://espn", {}, include_unplayed=True)

        assert [g.game_id for g in games] == ["played", "kicking-off"]
        assert [g.completed for g in games] == [True, False]

    async def test_an_unscored_fixture_comes_through_too(self) -> None:
        """The one ESPN listed and never filled in, not merely unplayed."""
        with _patch_get([_unscored_event("tbd")]):
            games = await get_games("http://espn", {}, include_unplayed=True)

        assert [(g.game_id, g.completed) for g in games] == [("tbd", False)]

    async def test_it_still_keeps_a_live_week_out_of_the_cache(self) -> None:
        """The guard reads everything parsed, not what's returned.

        Cache a week while a game in it is in progress and the response is
        frozen mid-game: `save_if_necessary` never overwrites, so that
        week's final scores would never be fetched again.
        """
        content = AsyncMock()
        content.data = json.dumps(
            {"events": [_event("played"), _event("kicking-off", completed=False)]}
        )
        with patch.object(espn_games_module, "get", AsyncMock(return_value=content)):
            games = await get_games("http://espn", {}, include_unplayed=True)

        assert len(games) == 2
        content.save_if_necessary.assert_not_awaited()

    async def test_a_fully_played_week_is_still_cached(self) -> None:
        content = AsyncMock()
        content.data = json.dumps({"events": [_event("played")]})
        with patch.object(espn_games_module, "get", AsyncMock(return_value=content)):
            await get_games("http://espn", {}, include_unplayed=True)

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


async def test_padded_names_are_collapsed() -> None:
    """
    ESPN's pre-1999 hockey rows are column-padded.
    """
    event = _event(
        "padded", home="Boston          Bruins", away="Quebec        Nordiques"
    )
    with _patch_get([event]):
        games = await get_games("http://espn", {})

    assert [(g.home, g.away) for g in games] == [("Boston Bruins", "Quebec Nordiques")]


async def test_unpadded_names_are_untouched() -> None:
    with _patch_get(
        [_event("plain", home="Boston Bruins", away="Toronto Maple Leafs")]
    ):
        games = await get_games("http://espn", {})

    assert [(g.home, g.away) for g in games] == [
        ("Boston Bruins", "Toronto Maple Leafs")
    ]
