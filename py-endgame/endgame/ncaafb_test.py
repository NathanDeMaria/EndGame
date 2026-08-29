from datetime import datetime, timezone

from .ncaafb import FIRST_WEEK_ZERO_SEASON, SEASON_START, _week_params
from .types import (
    Game,
    NcaaFbGroup,
    Season,
    SeasonType,
    Week,
    group_games_into_weeks,
    iter_weeks,
)


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


def test_week_zero_is_asked_for_from_the_season_espn_has_one() -> None:
    """The games that open the season sit in week 0, and were never fetched.

    UNC and TCU kicking off on the Saturday before Labour Day weekend is
    the case: the season file had weeks 1-16 and none of the games anyone
    actually wanted to look at that day.
    """
    params = _week_params(FIRST_WEEK_ZERO_SEASON)

    regular = [p for p in params if p.season_type is SeasonType.regular]
    assert min(p.week for p in regular) == 0
    # Every division, not just FBS -- week 0 has FCS games in it too.
    assert {p.group for p in regular if p.week == 0} == set(NcaaFbGroup)


def test_week_zero_is_not_asked_for_before_that() -> None:
    """A year with no week 0 must not spend three requests finding that out.

    They'd come back empty or 404, and a 404 lands in `trouble_params` --
    which is the signal for "this season is missing something", so filling
    it with a permanent, expected absence is how that signal stops meaning
    anything.
    """
    params = _week_params(FIRST_WEEK_ZERO_SEASON - 1)

    assert min(p.week for p in params if p.season_type is SeasonType.regular) == 1


def test_the_rest_of_the_season_is_unchanged_either_side_of_the_gate() -> None:
    with_zero = _week_params(FIRST_WEEK_ZERO_SEASON)
    without = _week_params(FIRST_WEEK_ZERO_SEASON - 1)

    def without_year(params):
        return {(p.week, p.season_type, p.group) for p in params}

    # The gate adds week 0 and touches nothing else.
    assert without_year(with_zero) - without_year(without) == {
        (0, SeasonType.regular, group) for group in NcaaFbGroup
    }
    assert without_year(without) - without_year(with_zero) == set()
