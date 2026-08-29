from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from . import ncaafb
from .ncaafb import FIRST_WEEK_ZERO_SEASON, SEASON_START, _week_params
from .season_cache import SeasonCache
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


class _FakeSeasonCache(SeasonCache):
    """A SeasonCache held in memory, so tests never touch the real one."""

    def __init__(self, cached: Season | None = None) -> None:
        super().__init__("fake")
        self._cached = cached
        self.saved: list[Season] = []

    def check_cache(self, season: int) -> Season | None:
        return self._cached

    def save_to_cache(self, season: Season) -> None:
        self.saved.append(season)


def _cached_season(year: int = 2016) -> Season:
    """A season as it was written before week 0 was ever asked for."""
    return Season([Week([_game(9, 10, "week-one", year)], 1)], year, [], SEASON_START)


class TestIgnoringTheCache:
    """`backfill_week_zero` re-pulls seasons *because* what's saved is stale.

    The season cache is written for every season that has ended, so any
    machine that has pulled these years has one -- and a cache hit returns
    the pre-week-0 season without a single request. The backfill's first
    real run reported that every season gained nothing, which is exactly
    what that looks like from outside.
    """

    async def test_a_cache_hit_short_circuits_the_fetch(self) -> None:
        """The behaviour being opted out of, pinned so it stays deliberate."""
        cache = _FakeSeasonCache(_cached_season())

        with patch.object(ncaafb, "_get_week", AsyncMock()) as get_week:
            season = await ncaafb.get_season(2016, season_cache=cache)

        get_week.assert_not_awaited()
        assert [g.game_id for w in season.weeks for g in w.games] == ["week-one"]

    async def test_use_cache_false_fetches_anyway(self) -> None:
        cache = _FakeSeasonCache(_cached_season())

        with patch.object(
            ncaafb, "_get_week", AsyncMock(return_value=Week([], 0))
        ) as get_week:
            await ncaafb.get_season(2016, use_cache=False, season_cache=cache)

        # And it asks for week 0, which is the whole point of re-pulling.
        assert get_week.await_count == len(_week_params(2016))
        assert 0 in {call.args[1] for call in get_week.await_args_list}

    async def test_it_leaves_the_cache_alone(self) -> None:
        """A dry run has to stay a dry run.

        `save_to_cache` also refuses to overwrite, so writing here would
        raise on every season that already has one.
        """
        cache = _FakeSeasonCache(_cached_season())

        with patch.object(ncaafb, "_get_week", AsyncMock(return_value=Week([], 0))):
            await ncaafb.get_season(2016, use_cache=False, season_cache=cache)

        assert cache.saved == []
