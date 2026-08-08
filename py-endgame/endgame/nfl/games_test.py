from datetime import datetime, timezone

import pytest

from ..types import Game
from .games import move_teams


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
