from datetime import datetime, timezone

from .ncaafb import SEASON_START
from .types import Game, Season, Week, group_games_into_weeks, iter_weeks


def _game(month: int, day: int, game_id: str, year: int = 2021) -> Game:
    return Game(
        home="Home",
        home_score=1,
        away="Away",
        away_score=0,
        neutral_site=False,
        completed=True,
        date=datetime(year, month, day, tzinfo=timezone.utc),
        game_id=game_id,
    )


def test_season_start_is_before_the_earliest_game_we_have() -> None:
    """The earliest game in any season we've pulled is 2002-08-22."""
    weeks = group_games_into_weeks([_game(8, 22, "earliest", 2002)], 2002, SEASON_START)

    assert weeks[0].number == 1


def test_season_walks_chronologically() -> None:
    """2021 as ESPN grouped it.

    The postseason came back as one week running from the December bowls to
    the January title game, overlapping the FCS playoff games sitting in
    regular season week 15.
    """
    season = Season(
        [
            Week([_game(12, 4, "bowl"), _game(1, 10, "title", 2022)], 17),
            Week([_game(12, 11, "fcs_semifinal")], 15),
        ],
        2021,
        [],
        SEASON_START,
    )

    weeks = list(iter_weeks(season))

    assert [[g.game_id for g in w.games] for w in weeks] == [
        ["bowl"],
        ["fcs_semifinal"],
        ["title"],
    ]
