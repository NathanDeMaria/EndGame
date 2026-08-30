from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from ..season_cache import SeasonCache
from ..types import Game, Season
from . import games as games_module
from .games import N_REGULAR_WEEKS, get_season, move_teams


def _game(home: str, away: str) -> Game:
    return Game(
        home=home,
        home_score=17,
        away=away,
        away_score=10,
        neutral_site=False,
        completed=True,
        date=datetime(2005, 11, 6, tzinfo=timezone.utc),
        game_id="1",
    )


def test_move_teams_renames_both_sides() -> None:
    moved = move_teams(_game(home="Chicago Bears", away="Green Bay Packers"))

    assert moved.home == "bears"
    assert moved.away == "packers"


def test_move_teams_keeps_everything_else() -> None:
    game = _game(home="Chicago Bears", away="Green Bay Packers")

    moved = move_teams(game)

    assert moved._replace(home=game.home, away=game.away) == game


@pytest.mark.parametrize(
    "old_name,expected",
    [
        # Teams that moved cities keep their current franchise's name, so a
        # franchise is one team across the whole history.
        ("San Diego Chargers", "chargers"),
        ("St. Louis Rams", "rams"),
        ("Oakland Raiders", "raiders"),
        ("Los Angeles Raiders", "raiders"),
        # Renamed in place
        ("Washington Redskins", "commanders"),
        # The one nickname that isn't a valid Python identifier
        ("San Francisco 49ers", "niners"),
        # Already current
        ("Los Angeles Chargers", "chargers"),
        ("Los Angeles Rams", "rams"),
        ("Las Vegas Raiders", "raiders"),
        ("Washington Commanders", "commanders"),
        ("Washington", "commanders"),
    ],
)
def test_move_teams_moved_franchises(old_name: str, expected: str) -> None:
    assert move_teams(_game(home=old_name, away="Chicago Bears")).home == expected
    assert move_teams(_game(home="Chicago Bears", away=old_name)).away == expected


@pytest.mark.parametrize(
    "name",
    [
        "Arizona Cardinals",
        "Atlanta Falcons",
        "Baltimore Ravens",
        "Buffalo Bills",
        "Carolina Panthers",
        "Chicago Bears",
        "Cincinnati Bengals",
        "Cleveland Browns",
        "Dallas Cowboys",
        "Denver Broncos",
        "Detroit Lions",
        "Green Bay Packers",
        "Houston Texans",
        "Indianapolis Colts",
        "Jacksonville Jaguars",
        "Kansas City Chiefs",
        "Miami Dolphins",
        "Minnesota Vikings",
        "New England Patriots",
        "New Orleans Saints",
        "New York Giants",
        "New York Jets",
        "Philadelphia Eagles",
        "Pittsburgh Steelers",
        "Seattle Seahawks",
        "Tampa Bay Buccaneers",
        "Tennessee Titans",
    ],
)
def test_move_teams_handles_every_team_that_stayed_put(name: str) -> None:
    assert move_teams(_game(home=name, away=name)).home == name.split(" ")[-1].lower()


class _FakeSeasonCache(SeasonCache):
    """
    A SeasonCache that keeps everything in memory, so the season tests
    never touch the real cache directory.

    Takes the league name the way the real one does, since `get_season`
    builds its own cache rather than taking one -- so this stands in for the
    class itself.
    """

    def __init__(self, league: str = "fake", cached: Season | None = None):
        super().__init__(league)
        self._cached = cached
        self.saved: list[Season] = []

    def check_cache(self, season: int) -> Season | None:
        return self._cached

    def save_to_cache(self, season: Season) -> None:
        self.saved.append(season)


def _patch_espn_games(games: list[Game]):
    async def fake_get_games(url, parameters, **_kwargs):
        return list(games)

    return patch.object(
        games_module, "get_games", AsyncMock(side_effect=fake_get_games)
    )


@pytest.mark.parametrize("include_unplayed", [False, True])
async def test_get_season_hands_the_flag_to_every_week(include_unplayed: bool) -> None:
    """`get_games` is what actually drops the unfinished games.

    Both loops, since the postseason is a separate one -- a flag threaded
    through the regular season and forgotten on the way to the playoffs
    would leave January's fixtures out and nothing would say so.
    """
    with (
        patch.object(games_module, "SeasonCache", _FakeSeasonCache),
        _patch_espn_games([]) as mock_get_games,
    ):
        await get_season(2019, include_unplayed=include_unplayed)

    assert mock_get_games.await_count == N_REGULAR_WEEKS + 5
    assert {
        call.kwargs["include_unplayed"] for call in mock_get_games.await_args_list
    } == {include_unplayed}


async def test_get_season_keeps_a_fixture_with_no_result_yet() -> None:
    """The Bears host the Packers next Sunday, 0-0 and not final."""
    fixture = _game(home="Chicago Bears", away="Green Bay Packers")._replace(
        completed=False, home_score=0, away_score=0
    )

    with (
        patch.object(games_module, "SeasonCache", _FakeSeasonCache),
        _patch_espn_games([fixture]),
    ):
        season = await get_season(2019, include_unplayed=True)

    games = [g for w in season.weeks for g in w.games]
    assert games, "the fixture was dropped"
    assert not any(g.completed for g in games)
    assert {g.home for g in games} == {"bears"}
