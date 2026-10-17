import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator, List, Tuple
from unittest.mock import patch

from ...types import Game, Season, Week
from ..gender import NcaabbGender
from . import all as box_scores


def _game(day: int, game_id: str, *, completed: bool = True) -> Game:
    return Game(
        home="Home",
        home_score=70,
        away="Away",
        away_score=65,
        neutral_site=False,
        completed=completed,
        date=datetime(2025, 11, day, tzinfo=timezone.utc),
        game_id=game_id,
    )


class _CapturingApply:
    """Stands in for `apply_in_parallel` to record what got fetched."""

    def __init__(self) -> None:
        self.calls: List[List[Tuple[Any, ...]]] = []

    def __call__(self, _func: Any, args: Any) -> AsyncIterator[None]:
        self.calls.append(list(args))

        async def _nothing() -> AsyncIterator[None]:
            return
            yield

        return _nothing()


async def _run(season: Season, **kwargs: Any) -> _CapturingApply:
    apply = _CapturingApply()
    with patch.object(box_scores, "apply_in_parallel", apply):
        pulled = [
            box
            async for box in box_scores.get_season_box_scores(
                season, NcaabbGender.mens, **kwargs
            )
        ]
    assert pulled == []
    return apply


class TestSkippingUnfinishedGames:
    """A game that hasn't been played has no box score to ask ESPN for.

    Nothing unplayed reaches here while `get_games` drops them at the fetch.
    This is what has to hold before a season is allowed to carry them: an
    unplayed game left in the args costs a request on every run between now
    and kickoff, and `get_box_score` can only answer None.
    """

    async def test_an_unfinished_game_is_not_fetched(self) -> None:
        season = Season(
            [Week([_game(4, "final"), _game(5, "tipping-off", completed=False)], 1)],
            2025,
        )

        apply = await _run(season)

        assert apply.calls == [[(NcaabbGender.mens, "final")]]

    async def test_a_week_with_nothing_finished_is_not_fetched_at_all(self) -> None:
        """Not an empty request -- no request."""
        season = Season([Week([_game(4, "tipping-off", completed=False)], 1)], 2025)

        apply = await _run(season)

        assert apply.calls == []

    async def test_it_stacks_with_the_already_pulled_filter(self) -> None:
        season = Season(
            [
                Week(
                    [
                        _game(4, "pulled"),
                        _game(5, "new"),
                        _game(6, "tipping-off", completed=False),
                    ],
                    1,
                )
            ],
            2025,
        )

        apply = await _run(season, skip_game_ids={"pulled"})

        assert apply.calls == [[(NcaabbGender.mens, "new")]]


def _summary() -> dict:
    """A game summary trimmed to what the box score reads."""
    keys = [
        "minutes",
        "points",
        "fieldGoalsMade-fieldGoalsAttempted",
        "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
        "freeThrowsMade-freeThrowsAttempted",
        "rebounds",
        "assists",
        "turnovers",
        "steals",
        "blocks",
        "offensiveRebounds",
        "defensiveRebounds",
        "fouls",
    ]

    def athlete(player_id: str, starter: bool, position: str, **extra: Any) -> dict:
        return {
            "athlete": {
                "id": player_id,
                "shortName": f"P. {player_id}",
                "links": [
                    {
                        "href": "https://www.espn.com/mens-college-basketball/"
                        f"player/_/id/{player_id}/player-{player_id}"
                    }
                ],
                "position": {"abbreviation": position},
            },
            "starter": starter,
            "didNotPlay": False,
            "stats": [
                "33",
                "12",
                "5-8",
                "0-1",
                "2-2",
                "7",
                "0",
                "1",
                "0",
                "2",
                "4",
                "3",
                "4",
            ],
            **extra,
        }

    return {
        "header": {
            "competitions": [
                {
                    "competitors": [
                        {"homeAway": "home", "team": {"id": "97"}},
                        {"homeAway": "away", "team": {"id": "59"}},
                    ]
                }
            ]
        },
        "boxscore": {
            "players": [
                {
                    "team": {"id": "59"},
                    "statistics": [
                        {
                            "keys": keys,
                            "athletes": [
                                athlete("1", True, "G"),
                                athlete("2", False, "C"),
                                athlete("3", False, "F", didNotPlay=True, stats=[]),
                            ],
                        }
                    ],
                },
                {
                    "team": {"id": "97"},
                    "statistics": [
                        {"keys": keys, "athletes": [athlete("4", True, "F")]}
                    ],
                },
            ]
        },
    }


class TestParsingTheSummary:
    def test_home_and_away_come_from_the_header(self) -> None:
        box = box_scores.parse_box_score("g1", _summary())
        assert box is not None
        assert (box.home.team_id, box.home.is_home) == ("97", True)
        assert (box.away.team_id, box.away.is_home) == ("59", False)

    def test_a_player_keeps_his_stats_start_and_position(self) -> None:
        box = box_scores.parse_box_score("g1", _summary())
        assert box is not None
        first = box.away.players[0]
        assert (first.player_id, first.short_name) == ("1", "player-1")
        assert (first.starter, first.position) == (True, "G")
        assert (first.minutes_played, first.points) == (33, 12)
        assert (first.field_goal_makes, first.field_goal_attempts) == (5, 8)
        assert (first.three_point_makes, first.three_point_attempts) == (0, 1)
        assert (first.offensive_rebounds, first.defensive_rebounds, first.fouls) == (
            4,
            3,
            4,
        )
        assert box.away.players[1].starter is False

    def test_a_player_who_didnt_play_is_left_out(self) -> None:
        box = box_scores.parse_box_score("g1", _summary())
        assert box is not None
        assert [p.player_id for p in box.away.players] == ["1", "2"]

    def test_a_game_with_no_players_has_no_box_score(self) -> None:
        summary = _summary()
        summary["boxscore"] = {"players": []}
        assert box_scores.parse_box_score("g1", summary) is None

    def test_a_team_with_no_starter_flagged_has_unknown_starters(self) -> None:
        # How 2010-11 comes back for most teams: everyone `starter: false`.
        summary = _summary()
        home = summary["boxscore"]["players"][1]["statistics"][0]["athletes"]
        home[0]["starter"] = False
        box = box_scores.parse_box_score("g1", summary)
        assert box is not None
        assert [p.starter for p in box.home.players] == [None]
        # the other team said who started, so its bench is known to be bench
        assert [p.starter for p in box.away.players] == [True, False]

    def test_a_player_with_no_id_is_left_out(self) -> None:
        # ESPN's stripped records: no id, no link.
        summary = _summary()
        away = summary["boxscore"]["players"][0]["statistics"][0]["athletes"]
        away[1]["athlete"] = {"links": [], "shortName": "A. Nobody"}
        box = box_scores.parse_box_score("g1", summary)
        assert box is not None
        assert [p.player_id for p in box.away.players] == ["1"]


class _Content:
    def __init__(self, payload: dict) -> None:
        self.data = json.dumps(payload).encode()


class TestKeepingTheSummary:
    async def test_the_parts_about_the_fetch_not_the_game_are_dropped(self) -> None:
        dropped = {"news": [1], "standings": {}, "videos": []}
        payload = {**_summary(), **dropped, "plays": []}

        async def _get(url: str, parameters: Any = None) -> _Content:
            assert parameters == {"event": "g1"}
            return _Content(payload)

        with patch.object(box_scores, "get", _get):
            kept = await box_scores.get_game_summary(NcaabbGender.mens, "g1")
        assert kept is not None
        assert set(kept) == {"header", "boxscore", "plays"}

    async def test_each_summary_comes_back_with_its_own_game(self) -> None:
        season = Season(
            [Week([_game(3, "a"), _game(3, "b"), _game(3, "c")], 1)], 2025, []
        )

        async def _summary_for(_gender: NcaabbGender, game_id: str) -> dict | None:
            return None if game_id == "b" else {"for": game_id}

        with patch.object(box_scores, "get_game_summary", _summary_for):
            pulled = [
                pair
                async for pair in box_scores.get_season_summaries(
                    season, NcaabbGender.mens, skip_game_ids={"c"}
                )
            ]
        assert pulled == [("a", {"for": "a"})]

    async def test_a_game_listed_twice_is_fetched_once(self) -> None:
        # A postseason game is in the season under D1 and its tournament.
        season = Season([Week([_game(3, "a"), _game(3, "a")], 1)], 2025, [])
        asked: list[str] = []

        async def _summary_for(_gender: NcaabbGender, game_id: str) -> dict | None:
            asked.append(game_id)
            return {"for": game_id}

        skip: set[str] = set()
        with patch.object(box_scores, "get_game_summary", _summary_for):
            pulled = [
                pair
                async for pair in box_scores.get_season_summaries(
                    season, NcaabbGender.mens, skip_game_ids=skip
                )
            ]
        assert asked == ["a"]
        assert pulled == [("a", {"for": "a"})]
        # and the caller's set is left as it was
        assert skip == set()
