from datetime import date, datetime
from typing import List, Optional
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

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
    _date_range,
    get_ncaabb_season,
    is_between_dates,
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
        for day in _date_range(regular_start, date(year + 1, *REGULAR_SEASON_END))
    }
    for group in POSTSEASON_GROUPS:
        expected |= {
            DayParams(day, gender, group)
            for day in _date_range(postseason_start, date(year + 1, *SEASON_END))
        }
    return expected


def _called_day_params(mock_get_games: AsyncMock) -> List[DayParams]:
    # get_ncaabb_games is called as get_ncaabb_games(*day_param), so
    # rebuilding from .args also checks nothing was passed by keyword.
    assert all(not call.kwargs for call in mock_get_games.await_args_list)
    return [DayParams(*call.args) for call in mock_get_games.await_args_list]


async def _no_games(
    game_date: date, gender: NcaabbGender, group: NcaabbGroup
) -> List[Game]:
    return []


def _patch_get_games(side_effect=_no_games):
    return patch.object(
        ncaabb_module, "get_ncaabb_games", AsyncMock(side_effect=side_effect)
    )


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
