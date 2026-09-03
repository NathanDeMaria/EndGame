from datetime import date, datetime, timedelta
from typing import List, Optional
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from ..constants import DEFAULT_LOOKAHEAD_DAYS
from ..date import date_range, is_between_dates
from ..season_cache import SeasonCache
from ..types import group_games_into_weeks
from . import ncaabb as ncaabb_module
from .ncaabb import (
    POST_SEASON_START,
    POSTSEASON_GROUPS,
    REGULAR_SEASON_END,
    REGULAR_SEASON_START,
    SEASON_END,
    DayParams,
    Game,
    NcaabbGender,
    NcaabbGroup,
    Season,
    Week,
    _odds_params,
    get_ncaabb_games,
    get_ncaabb_season,
    merge_seasons,
)


@pytest.mark.parametrize(
    "test_date, start, end, expected",
    [
        (date(2025, 1, 1), (11, 1), (4, 1), True),
        (date(2025, 6, 6), (11, 1), (4, 1), False),
        (date(2025, 12, 29), (11, 1), (4, 1), True),
        (date(2025, 1, 1), (6, 1), (6, 31), False),
        (date(2025, 6, 6), (6, 1), (6, 31), True),
        (date(2025, 12, 29), (6, 1), (6, 31), False),
    ],
)
def test_is_between_dates(
    test_date: date, start: tuple[int, int], end: tuple[int, int], expected: bool
) -> None:
    assert is_between_dates(test_date, start, end) == expected


def test_merge_seasons() -> None:
    year = 1989
    # Week numbers come from the date now, so these have to be inside the
    # season they're labelled with: the 1989 season starts Nov 1989.
    g1 = Game("A", 10, "B", 5, False, True, datetime(year, 11, 1), "1")
    g2 = Game("C", 20, "D", 15, False, True, datetime(year, 11, 1), "2")

    # Same ID as g1, but different score (updated)
    updated_home_score = 12
    g1_updated = Game(
        "A", updated_home_score, "B", 5, False, True, datetime(year, 11, 1), "1"
    )

    g3 = Game("E", 30, "F", 25, False, True, datetime(year, 11, 8), "3")

    day_params = [DayParams(date(year, 11, 1), NcaabbGender.mens, NcaabbGroup.d1)]
    s1 = Season(
        weeks=[Week([g1, g2], 1)],
        year=year,
        trouble_params=day_params,
    )

    s2 = Season(
        weeks=[Week([g1_updated], 1), Week([g3], 2)], year=year, trouble_params=None
    )

    merged = merge_seasons([s1, s2])

    assert merged.year == year
    weeks = merged.calendar_weeks
    assert len(weeks) == 2

    # Check week 1
    w1 = next(w for w in weeks if w.number == 1)
    assert len(w1.games) == 2

    # Should have the updated g1
    merged_g1 = next(g for g in w1.games if g.game_id == "1")
    assert merged_g1.home_score == updated_home_score

    # Should still have g2
    merged_g2 = next(g for g in w1.games if g.game_id == "2")
    assert merged_g2.home_score == 20

    # Check week 2
    w2 = next(w for w in weeks if w.number == 2)
    assert len(w2.games) == 1
    merged_g3 = next(g for g in w2.games if g.game_id == "3")
    assert merged_g3.home_score == 30

    # Check trouble params
    assert set(merged.trouble_params or []) == set(day_params)


def _dated_game(day: datetime, game_id: str) -> Game:
    return Game("A", 10, "B", 5, False, True, day, game_id)


def test_group_games_into_weeks__numbers_from_season_start() -> None:
    year = 1989
    november = _dated_game(datetime(year, 11, 1), "nov")
    march = _dated_game(datetime(year + 1, 3, 7), "mar")

    weeks = group_games_into_weeks([november, march], year, REGULAR_SEASON_START)

    assert [w.number for w in weeks] == [1, 19]


def test_group_games_into_weeks__numbering_is_stable_for_a_partial_season() -> None:
    """The bug: an incremental pull only sees recent games and renumbers from 1.

    Grouping a tail of the season has to give those weeks the same numbers
    they'd get if the whole season were grouped at once.
    """
    year = 1989
    early = _dated_game(datetime(year, 11, 1), "early")
    late = _dated_game(datetime(year + 1, 3, 7), "late")

    full = group_games_into_weeks([early, late], year, REGULAR_SEASON_START)
    # What a daily run that only fetched March would produce
    partial = group_games_into_weeks([late], year, REGULAR_SEASON_START)

    assert [w.number for w in partial] == [w.number for w in full if w.games[0] is late]
    assert partial[0].number == 19


def test_merge_seasons__repairs_positionally_numbered_weeks() -> None:
    """Seasons saved under the old numbering get regrouped, not carried forward.

    The old code numbered by position, so a March-only incremental pull
    produced a "week 1" that merged into November's week 1.
    """
    year = 1989
    november = _dated_game(datetime(year, 11, 1), "nov")
    march = _dated_game(datetime(year + 1, 3, 7), "mar")

    # Both mislabelled week 1, which is exactly the corrupted shape
    saved = Season(weeks=[Week([november, march], 1)], year=year, trouble_params=None)

    weeks = merge_seasons([saved]).calendar_weeks

    assert [w.number for w in weeks] == [1, 19]
    assert [g.game_id for w in weeks for g in w.games] == ["nov", "mar"]


class _FakeSeasonCache(SeasonCache):
    """
    A SeasonCache that keeps everything in memory, so the season tests
    never touch the real cache directory.
    """

    def __init__(self, cached: Optional[Season] = None):
        super().__init__("fake")
        self._cached = cached
        self.saved: List[Season] = []

    def check_cache(self, season: int) -> Optional[Season]:
        return self._cached

    def save_to_cache(self, season: Season) -> None:
        self.saved.append(season)


# A season that's long over, so nothing gets clamped to today's date
_FINISHED_YEAR = 1989


def _expected_day_params(
    gender: NcaabbGender,
    year: int,
    regular_start: date,
    postseason_start: date,
) -> set:
    """
    Every (day, gender, group) get_ncaabb_games should be asked for.
    """
    expected = {
        DayParams(day, gender, NcaabbGroup.d1)
        for day in date_range(regular_start, date(year + 1, *REGULAR_SEASON_END))
    }
    for group in POSTSEASON_GROUPS:
        expected |= {
            DayParams(day, gender, group)
            for day in date_range(postseason_start, date(year + 1, *SEASON_END))
        }
    return expected


def _called_day_params(mock_get_games: AsyncMock) -> List[DayParams]:
    # get_ncaabb_games is called as get_ncaabb_games(*day_param,
    # include_unplayed=...), so rebuilding from .args also checks the day
    # params themselves weren't passed by keyword.
    assert all(
        set(call.kwargs) <= {"include_unplayed"}
        for call in mock_get_games.await_args_list
    )
    return [DayParams(*call.args) for call in mock_get_games.await_args_list]


def _unplayed_flags(mock_get_games: AsyncMock) -> set:
    """The distinct `include_unplayed` values a season's days were asked with."""
    return {
        call.kwargs.get("include_unplayed", False)
        for call in mock_get_games.await_args_list
    }


async def _no_games(
    game_date: date, gender: NcaabbGender, group: NcaabbGroup
) -> List[Game]:
    return []


def _patch_get_games(side_effect=_no_games):
    """
    Stand in for `get_ncaabb_games`, which the doubles below take by day.

    `include_unplayed` is swallowed here rather than added to every double:
    which days a season walks is what these tests are about, and what the
    flag does to a single day belongs to `get_ncaabb_games`'s own tests. The
    mock still records it, so `_unplayed_flags` can check it was passed on.
    """

    async def _call(
        game_date: date, gender: NcaabbGender, group: NcaabbGroup, **_kwargs
    ) -> List[Game]:
        return await side_effect(game_date, gender, group)

    return patch.object(ncaabb_module, "get_ncaabb_games", AsyncMock(side_effect=_call))


async def test_get_ncaabb_season__cache_hit_skips_fetching() -> None:
    cached = Season([Week([_dated_game(datetime(1990, 3, 7), "cached")], 19)], 1989)
    cache = _FakeSeasonCache(cached)

    with _patch_get_games() as mock_get_games:
        season = await get_ncaabb_season(
            _FINISHED_YEAR, NcaabbGender.mens, season_cache=cache
        )

    # Tagged with the season start on the way out, but otherwise untouched
    assert season == cached.with_season_start(REGULAR_SEASON_START)
    assert season.weeks == cached.weeks
    mock_get_games.assert_not_awaited()
    assert cache.saved == []


async def test_get_ncaabb_season__fetches_whole_season_when_cache_is_empty() -> None:
    regular = _dated_game(datetime(_FINISHED_YEAR, 11, 3, 19), "regular")
    tournament = _dated_game(datetime(_FINISHED_YEAR + 1, 3, 20, 19), "tournament")

    async def fake_get_games(
        game_date: date, gender: NcaabbGender, group: NcaabbGroup
    ) -> List[Game]:
        if (game_date, group) == (regular.date.date(), NcaabbGroup.d1):
            return [regular]
        if (game_date, group) == (tournament.date.date(), NcaabbGroup.ncaa):
            return [tournament]
        return []

    cache = _FakeSeasonCache()
    with _patch_get_games(fake_get_games) as mock_get_games:
        season = await get_ncaabb_season(
            _FINISHED_YEAR, NcaabbGender.mens, season_cache=cache
        )

    called = _called_day_params(mock_get_games)
    expected = _expected_day_params(
        NcaabbGender.mens,
        _FINISHED_YEAR,
        regular_start=date(_FINISHED_YEAR, *REGULAR_SEASON_START),
        postseason_start=date(_FINISHED_YEAR + 1, *POST_SEASON_START),
    )
    # Nothing asked for twice, and exactly the days we expect
    assert len(called) == len(expected)
    assert set(called) == expected

    assert season.year == _FINISHED_YEAR
    assert {g.game_id for w in season.weeks for g in w.games} == {
        "regular",
        "tournament",
    }
    assert season.trouble_params == []


async def test_get_ncaabb_season__resumes_after_the_last_day_of_season_so_far() -> None:
    already_have = _dated_game(datetime(_FINISHED_YEAR + 1, 3, 7, 19), "already-have")
    season_so_far = Season([Week([already_have], 19)], _FINISHED_YEAR)
    fresh = _dated_game(datetime(_FINISHED_YEAR + 1, 3, 10, 19), "fresh")

    async def fake_get_games(
        game_date: date, gender: NcaabbGender, group: NcaabbGroup
    ) -> List[Game]:
        if (game_date, group) == (fresh.date.date(), NcaabbGroup.d1):
            return [fresh]
        return []

    cache = _FakeSeasonCache()
    with _patch_get_games(fake_get_games) as mock_get_games:
        season = await get_ncaabb_season(
            _FINISHED_YEAR,
            NcaabbGender.mens,
            season_so_far=season_so_far,
            season_cache=cache,
        )

    called = _called_day_params(mock_get_games)
    # Both the regular season and postseason restart from the last day we have
    last_day = already_have.date.date()
    expected = _expected_day_params(
        NcaabbGender.mens,
        _FINISHED_YEAR,
        regular_start=last_day,
        postseason_start=last_day,
    )
    assert set(called) == expected
    assert min(p.date for p in called) == last_day

    # The season we already had is merged in, not thrown away
    assert {g.game_id for w in season.weeks for g in w.games} == {
        "already-have",
        "fresh",
    }


@pytest.mark.parametrize(
    "weeks",
    [
        pytest.param([], id="no-weeks"),
        pytest.param([Week([], 1)], id="weeks-but-no-games"),
    ],
)
async def test_get_ncaabb_season__empty_season_so_far_starts_from_the_top(
    weeks: List[Week],
) -> None:
    """A season_so_far with no games has no last day to resume from.

    It has to fall back to the full season's date range rather than, say,
    resuming from nothing and fetching nothing.
    """
    regular = _dated_game(datetime(_FINISHED_YEAR, 11, 3, 19), "regular")

    async def fake_get_games(
        game_date: date, gender: NcaabbGender, group: NcaabbGroup
    ) -> List[Game]:
        if (game_date, group) == (regular.date.date(), NcaabbGroup.d1):
            return [regular]
        return []

    cache = _FakeSeasonCache()
    with _patch_get_games(fake_get_games) as mock_get_games:
        season = await get_ncaabb_season(
            _FINISHED_YEAR,
            NcaabbGender.mens,
            season_so_far=Season(weeks, _FINISHED_YEAR),
            season_cache=cache,
        )

    assert set(_called_day_params(mock_get_games)) == _expected_day_params(
        NcaabbGender.mens,
        _FINISHED_YEAR,
        regular_start=date(_FINISHED_YEAR, *REGULAR_SEASON_START),
        postseason_start=date(_FINISHED_YEAR + 1, *POST_SEASON_START),
    )

    # Merging with an empty season doesn't drop what we just fetched,
    # and doesn't leave the gameless week behind either
    assert {g.game_id for w in season.weeks for g in w.games} == {"regular"}
    assert all(w.games for w in season.weeks)


def _response_error(status: int) -> aiohttp.ClientResponseError:
    request_info = aiohttp.RequestInfo(
        URL("https://example.com"), "GET", CIMultiDictProxy(CIMultiDict())
    )
    return aiohttp.ClientResponseError(request_info, (), status=status)


async def test_get_ncaabb_season__marks_failed_days_as_trouble() -> None:
    good = _dated_game(datetime(_FINISHED_YEAR, 11, 3, 19), "good")
    bad_day = date(_FINISHED_YEAR, 11, 4)

    async def fake_get_games(
        game_date: date, gender: NcaabbGender, group: NcaabbGroup
    ) -> List[Game]:
        if game_date == bad_day and group == NcaabbGroup.d1:
            raise _response_error(500)
        if (game_date, group) == (good.date.date(), NcaabbGroup.d1):
            return [good]
        return []

    cache = _FakeSeasonCache()
    with _patch_get_games(fake_get_games):
        season = await get_ncaabb_season(
            _FINISHED_YEAR, NcaabbGender.mens, season_cache=cache
        )

    assert season.trouble_params == [
        DayParams(bad_day, NcaabbGender.mens, NcaabbGroup.d1)
    ]
    # One bad day doesn't lose the rest of the season
    assert {g.game_id for w in season.weeks for g in w.games} == {"good"}


async def test_get_ncaabb_season__caches_a_finished_season() -> None:
    cache = _FakeSeasonCache()

    with _patch_get_games():
        season = await get_ncaabb_season(
            _FINISHED_YEAR, NcaabbGender.mens, season_cache=cache
        )

    assert cache.saved == [season]


async def test_get_ncaabb_season__doesnt_cache_an_unfinished_season() -> None:
    # Far enough out that the season can't have ended, whenever this runs
    future_year = date.today().year + 5
    cache = _FakeSeasonCache()

    with _patch_get_games() as mock_get_games:
        await get_ncaabb_season(future_year, NcaabbGender.mens, season_cache=cache)

    assert cache.saved == []
    # Days in the future never get requested
    assert _called_day_params(mock_get_games) == []


def _frozen_today(today: date):
    class _FrozenDate(date):
        @classmethod
        def today(cls) -> date:
            return today

    return patch.object(ncaabb_module, "date", _FrozenDate)


def _unplayed(game: Game) -> Game:
    """The same game as ESPN reports it before it's been played."""
    return game._replace(completed=False, home_score=0, away_score=0)


class TestCarryingFixtures:
    """A season pulled with `include_unplayed`, so it holds what's coming.

    NCAABB is walked by day, so a fixture is only visible by asking for the
    day it falls on: the flag on its own would find nothing but the
    unfinished games on days already gone. The walk has to run past today
    too, which is the half these cover.
    """

    # Deep enough into the season that both stretches are underway
    _MID_SEASON = date(_FINISHED_YEAR + 1, 3, 10)

    async def _walk(self, today: date, **kwargs) -> AsyncMock:
        with _frozen_today(today), _patch_get_games() as mock_get_games:
            await get_ncaabb_season(
                _FINISHED_YEAR,
                NcaabbGender.mens,
                season_cache=_FakeSeasonCache(),
                **kwargs,
            )
        return mock_get_games

    async def test_a_results_only_pull_still_stops_at_today(self) -> None:
        mock_get_games = await self._walk(self._MID_SEASON)

        called = _called_day_params(mock_get_games)
        assert max(p.date for p in called) == self._MID_SEASON - timedelta(days=1)
        assert _unplayed_flags(mock_get_games) == {False}

    async def test_the_walk_runs_a_lookahead_past_today(self) -> None:
        mock_get_games = await self._walk(self._MID_SEASON, include_unplayed=True)

        called = _called_day_params(mock_get_games)
        # date_range's end is exclusive, so the last day asked for is the
        # day before the horizon
        horizon = self._MID_SEASON + timedelta(days=DEFAULT_LOOKAHEAD_DAYS - 1)
        assert max(p.date for p in called) == horizon
        assert _unplayed_flags(mock_get_games) == {True}

    async def test_the_lookahead_reaches_the_postseason_groups(self) -> None:
        """Both stretches of the season get the window, not just d1."""
        mock_get_games = await self._walk(self._MID_SEASON, include_unplayed=True)

        called = _called_day_params(mock_get_games)
        horizon = self._MID_SEASON + timedelta(days=DEFAULT_LOOKAHEAD_DAYS - 1)
        for group in POSTSEASON_GROUPS:
            assert max(p.date for p in called if p.group == group) == horizon, (
                f"{group.name} stopped short of the horizon"
            )

    async def test_the_lookahead_stops_at_the_end_of_each_stretch(self) -> None:
        """A week past April 1st isn't more regular season."""
        regular_end = date(_FINISHED_YEAR + 1, *REGULAR_SEASON_END)

        mock_get_games = await self._walk(
            regular_end - timedelta(days=2), include_unplayed=True
        )

        called = _called_day_params(mock_get_games)
        d1_days = [p.date for p in called if p.group == NcaabbGroup.d1]
        assert max(d1_days) == regular_end - timedelta(days=1)

    async def test_it_resumes_from_the_last_result_not_the_last_fixture(self) -> None:
        """Resuming from a fixture starts the walk after the end of it.

        The last game in a season that carries its schedule is one that
        hasn't been played, often weeks out. Resuming from that day would
        leave every run fetching an empty range and writing back exactly
        what it already had -- a pull that never picks up another result and
        never says so.
        """
        played = _dated_game(datetime(_FINISHED_YEAR + 1, 3, 9, 19), "played")
        scheduled = _unplayed(
            _dated_game(datetime(_FINISHED_YEAR + 1, 3, 20, 19), "scheduled")
        )
        season_so_far = Season([Week([played, scheduled], 19)], _FINISHED_YEAR)

        mock_get_games = await self._walk(
            self._MID_SEASON, season_so_far=season_so_far, include_unplayed=True
        )

        called = _called_day_params(mock_get_games)
        assert called, "the walk asked for nothing at all"
        assert min(p.date for p in called) == played.date.date()


def _patch_espn_games(games: List[Game]):
    async def fake_get_games(url, parameters, event_filter=None, **_kwargs):
        return list(games)

    return patch.object(
        ncaabb_module, "get_games", AsyncMock(side_effect=fake_get_games)
    )


async def test_get_ncaabb_games_drops_a_finished_scoreless_game() -> None:
    """Montana State at Northern Arizona, 2003-02-28: basketball isn't 0-0."""
    bogus = _dated_game(datetime(2003, 2, 28, 19), "bogus")._replace(
        home_score=0, away_score=0
    )
    real = _dated_game(datetime(2003, 2, 28, 19), "real")

    with _patch_espn_games([bogus, real]):
        games = await get_ncaabb_games(
            date(2003, 2, 28), NcaabbGender.mens, NcaabbGroup.d1
        )

    assert [g.game_id for g in games] == ["real"]


async def test_get_ncaabb_games_keeps_a_scoreless_game_it_hasnt_played_yet() -> None:
    """Every fixture is 0-0, so the filter has to read `completed` too.

    ESPN sends 0s for a game that hasn't tipped off, and `parse_game` writes
    them for a fixture with no score at all. Dropping on the scoreline alone
    throws away the whole schedule this pull exists to fetch.
    """
    fixture = _unplayed(_dated_game(datetime(2003, 2, 28, 19), "fixture"))
    bogus = _dated_game(datetime(2003, 2, 28, 19), "bogus")._replace(
        home_score=0, away_score=0
    )
    real = _dated_game(datetime(2003, 2, 28, 19), "real")

    with _patch_espn_games([fixture, bogus, real]):
        games = await get_ncaabb_games(
            date(2003, 2, 28),
            NcaabbGender.mens,
            NcaabbGroup.d1,
            include_unplayed=True,
        )

    assert [g.game_id for g in games] == ["fixture", "real"]


@pytest.mark.parametrize("include_unplayed", [False, True])
async def test_get_ncaabb_games_hands_the_flag_to_espn(include_unplayed: bool) -> None:
    """`get_games` is what actually drops the unfinished games."""
    with _patch_espn_games([]) as mock_get_games:
        await get_ncaabb_games(
            date(2003, 2, 28),
            NcaabbGender.mens,
            NcaabbGroup.d1,
            include_unplayed=include_unplayed,
        )

    assert mock_get_games.await_args.kwargs["include_unplayed"] is include_unplayed


class TestMergeSeasonsKeepsResults:
    """Which copy of a game a merge keeps, once one of them can be unplayed.

    `merge_seasons` folds a fresh pull over what's already in the bucket,
    and the fresh one is second. Plain "latest wins" therefore replaces
    yesterday's final with today's in-progress copy of it.
    """

    def test_a_finished_game_is_not_walked_back_to_a_live_one(self) -> None:
        final = _dated_game(datetime(_FINISHED_YEAR, 11, 1, 19), "game")
        saved = Season([Week([final], 1)], _FINISHED_YEAR)
        mid_game = Season(
            [Week([final._replace(completed=False, home_score=41, away_score=38)], 1)],
            _FINISHED_YEAR,
        )

        merged = merge_seasons([saved, mid_game])

        [game] = [g for w in merged.weeks for g in w.games]
        assert game == final

    def test_a_game_that_finished_since_the_last_pull_is_taken(self) -> None:
        stale = _unplayed(_dated_game(datetime(_FINISHED_YEAR, 11, 1, 19), "game"))
        saved = Season([Week([stale], 1)], _FINISHED_YEAR)
        final = _dated_game(datetime(_FINISHED_YEAR, 11, 1, 19), "game")
        fresh = Season([Week([final], 1)], _FINISHED_YEAR)

        merged = merge_seasons([saved, fresh])

        [game] = [g for w in merged.weeks for g in w.games]
        assert game == final

    def test_a_fixture_is_still_corrected_by_a_later_fixture(self) -> None:
        """Neither is final, so the fresher copy wins -- a tip-off moves."""
        early = _unplayed(_dated_game(datetime(_FINISHED_YEAR, 11, 1, 19), "game"))
        moved = early._replace(date=datetime(_FINISHED_YEAR, 11, 1, 21))

        merged = merge_seasons(
            [
                Season([Week([early], 1)], _FINISHED_YEAR),
                Season([Week([moved], 1)], _FINISHED_YEAR),
            ]
        )

        [game] = [g for w in merged.weeks for g in w.games]
        assert game == moved


def _groups_asked(start: date, end: date) -> set:
    return {params.group for params in _odds_params(start, end)}


def test_odds_params__december_asks_d1_only() -> None:
    """
    The tournaments don't run until March, so a December range has no
    business asking them.
    """
    assert _groups_asked(date(2026, 12, 1), date(2026, 12, 15)) == {NcaabbGroup.d1}


def test_odds_params__march_asks_the_tournaments_too() -> None:
    assert _groups_asked(date(2027, 3, 10), date(2027, 3, 24)) == {
        NcaabbGroup.d1
    } | set(POSTSEASON_GROUPS)


def test_odds_params__the_off_season_asks_nothing() -> None:
    assert _odds_params(date(2026, 7, 1), date(2026, 8, 1)) == []


def test_odds_params__narrows_each_competition_to_its_own_window() -> None:
    """
    A rest-of-season range covers days no tournament could be played on.
    Handing the whole range to every group would spend four requests a
    gender per chunk on months that can't have a game in them.
    """
    params = {
        (p.gender, p.group): (p.start, p.end)
        for p in _odds_params(date(2026, 12, 1), date(2027, 4, 30))
    }

    for gender in NcaabbGender:
        # D1 runs to the end of the regular season, not to the end of April
        assert params[(gender, NcaabbGroup.d1)] == (date(2026, 12, 1), date(2027, 4, 1))
        for group in POSTSEASON_GROUPS:
            assert params[(gender, group)] == (date(2027, 3, 1), date(2027, 4, 30))


def test_odds_params__covers_both_genders() -> None:
    genders = {
        params.gender for params in _odds_params(date(2027, 1, 5), date(2027, 1, 19))
    }
    assert genders == set(NcaabbGender)
