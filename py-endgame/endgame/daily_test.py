from datetime import date, datetime, timedelta, timezone
from typing import List, Optional
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from . import daily as daily_module
from .daily import DailyLeague, get_daily_games, get_daily_odds, get_season
from .date import date_range
from .nhl import NHL
from .season_cache import SeasonCache
from .types import Game, Season, Week
from .wnba import WNBA

# Seasons that are long over, so nothing gets clamped to today's date
_FINISHED_NHL_YEAR = 2015  # an ordinary season, no COVID dates
_FINISHED_WNBA_YEAR = 2015


def _game(
    day: datetime,
    game_id: str,
    home: str = "Home",
    away: str = "Away",
    home_score: int = 3,
    away_score: int = 2,
) -> Game:
    return Game(
        home=home,
        home_score=home_score,
        away=away,
        away_score=away_score,
        neutral_site=False,
        completed=True,
        date=day,
        game_id=game_id,
    )


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


async def _no_games(league: DailyLeague, day: date) -> List[Game]:
    return []


def _patch_get_games(side_effect=_no_games):
    return patch.object(
        daily_module, "get_daily_games", AsyncMock(side_effect=side_effect)
    )


def _called_days(mock_get_games: AsyncMock) -> List[date]:
    # get_daily_games is called as get_daily_games(league, day), so
    # rebuilding from .args also checks nothing was passed by keyword.
    assert all(not call.kwargs for call in mock_get_games.await_args_list)
    return [call.args[1] for call in mock_get_games.await_args_list]


def _frozen_today(today: date):
    class _FrozenDate(date):
        @classmethod
        def today(cls) -> date:
            return today

    return patch.object(daily_module, "date", _FrozenDate)


@pytest.mark.parametrize(
    "today, expected",
    [
        # The 2025-26 season is underway
        pytest.param(date(2025, 12, 1), 2025, id="mid-season"),
        # Between the June final and the October opener, the last season
        # that started is still the one before this calendar year
        pytest.param(date(2026, 8, 17), 2025, id="offseason"),
        pytest.param(date(2026, 10, 20), 2026, id="new-season-started"),
    ],
)
def test_nhl_latest_year(today: date, expected: int) -> None:
    with _frozen_today(today):
        assert NHL.latest_year() == expected


@pytest.mark.parametrize(
    "today, expected",
    [
        pytest.param(date(2026, 8, 17), 2026, id="mid-season"),
        # A WNBA season is over by November, so anything before May belongs
        # to the previous year rather than a season that hasn't started
        pytest.param(date(2026, 2, 1), 2025, id="offseason"),
        pytest.param(date(2026, 11, 30), 2026, id="just-finished"),
    ],
)
def test_wnba_latest_year(today: date, expected: int) -> None:
    with _frozen_today(today):
        assert WNBA.latest_year() == expected


def test_nhl_season_runs_into_the_next_year() -> None:
    assert NHL.start_date(2018) == date(2018, 9, 15)
    assert NHL.end_date(2018) == date(2019, 7, 15)


def test_covid_seasons_get_their_real_dates() -> None:
    """2019-20 finished in the Toronto bubble on 2020-09-28.

    The usual window ends in July, so those playoff games sat outside
    2019 entirely and inside the days 2020 would otherwise start on.
    """
    assert NHL.end_date(2019) > date(2020, 9, 28)
    # 2020-21 didn't open until 2021-01-13
    assert NHL.start_date(2020) > date(2020, 10, 1)
    assert NHL.start_date(2020) <= date(2021, 1, 13)


@pytest.mark.parametrize("league", [NHL, WNBA], ids=lambda league: league.name)
def test_no_season_overlaps_the_next(league: DailyLeague) -> None:
    """A day belongs to one season, or the same game lands in two files."""
    for year in range(league.first_year, 2030):
        assert league.end_date(year) <= league.start_date(year + 1), year


def test_covid_seasons_are_finished() -> None:
    """`is_finished` has to follow the real end date, not the window.

    Caching 2019 in, say, August 2020 would have frozen a season whose
    playoffs hadn't been played yet.
    """
    assert NHL.is_finished(2019)
    assert NHL.is_finished(2020)


@pytest.mark.parametrize(
    "day, expected",
    [
        # The bubble playoffs, which the September-to-July window misses
        pytest.param(date(2020, 8, 20), True, id="bubble-playoffs"),
        pytest.param(date(2020, 9, 28), True, id="bubble-final"),
        # A normal August, with no season listed as running through it
        pytest.param(date(2018, 8, 20), False, id="ordinary-august"),
    ],
)
def test_is_in_season_covers_the_covid_playoffs(day: date, expected: bool) -> None:
    assert NHL.is_in_season(day) == expected


def test_wnba_season_stays_inside_one_year() -> None:
    """The WNBA is the one league here that doesn't span two years."""
    assert WNBA.start_date(2019) == date(2019, 5, 1)
    assert WNBA.end_date(2019) == date(2019, 10, 31)


@pytest.mark.parametrize(
    "league, day, expected",
    [
        pytest.param(NHL, date(2025, 12, 1), True, id="nhl-in-season"),
        pytest.param(NHL, date(2025, 8, 1), False, id="nhl-offseason"),
        # The NHL's range wraps the new year, so January is in season
        pytest.param(NHL, date(2026, 1, 15), True, id="nhl-january"),
        pytest.param(WNBA, date(2025, 7, 1), True, id="wnba-in-season"),
        pytest.param(WNBA, date(2025, 1, 15), False, id="wnba-offseason"),
    ],
)
def test_is_in_season(league: DailyLeague, day: date, expected: bool) -> None:
    assert league.is_in_season(day) == expected


async def test_get_season__cache_hit_skips_fetching() -> None:
    cached = Season(
        [Week([_game(datetime(2016, 3, 7), "cached")], 1)], _FINISHED_NHL_YEAR
    )
    cache = _FakeSeasonCache(cached)

    with _patch_get_games() as mock_get_games:
        season = await get_season(NHL, _FINISHED_NHL_YEAR, season_cache=cache)

    # Tagged with the season start on the way out, but otherwise untouched
    assert season == cached.with_season_start(NHL.season_start)
    assert season.weeks == cached.weeks
    mock_get_games.assert_not_awaited()
    assert cache.saved == []


async def test_get_season__fetches_every_day_when_the_cache_is_empty() -> None:
    opener = _game(datetime(_FINISHED_NHL_YEAR, 10, 7, 19), "opener")
    final = _game(datetime(_FINISHED_NHL_YEAR + 1, 6, 12, 19), "final")

    async def fake_get_games(league: DailyLeague, day: date) -> List[Game]:
        return [g for g in (opener, final) if g.date.date() == day]

    cache = _FakeSeasonCache()
    with _patch_get_games(fake_get_games) as mock_get_games:
        season = await get_season(NHL, _FINISHED_NHL_YEAR, season_cache=cache)

    called = _called_days(mock_get_games)
    expected = date_range(
        NHL.start_date(_FINISHED_NHL_YEAR), NHL.end_date(_FINISHED_NHL_YEAR)
    )
    # Nothing asked for twice, and exactly the days we expect
    assert called == expected

    assert season.year == _FINISHED_NHL_YEAR
    assert {g.game_id for w in season.weeks for g in w.games} == {"opener", "final"}
    assert season.trouble_params == []


async def test_get_season__resumes_after_the_last_day_of_season_so_far() -> None:
    already_have = _game(datetime(_FINISHED_NHL_YEAR + 1, 3, 7, 19), "already-have")
    season_so_far = Season([Week([already_have], 1)], _FINISHED_NHL_YEAR)
    fresh = _game(datetime(_FINISHED_NHL_YEAR + 1, 3, 10, 19), "fresh")

    async def fake_get_games(league: DailyLeague, day: date) -> List[Game]:
        return [fresh] if fresh.date.date() == day else []

    cache = _FakeSeasonCache()
    with _patch_get_games(fake_get_games) as mock_get_games:
        season = await get_season(
            NHL,
            _FINISHED_NHL_YEAR,
            season_so_far=season_so_far,
            season_cache=cache,
        )

    called = _called_days(mock_get_games)
    # The last day we already have gets asked for again, since we don't know
    # its games were all final last time
    assert min(called) == already_have.date.date()
    assert max(called) == NHL.end_date(_FINISHED_NHL_YEAR) - timedelta(days=1)

    # The season we already had is merged in, not thrown away
    assert {g.game_id for w in season.weeks for g in w.games} == {
        "already-have",
        "fresh",
    }


async def test_get_season__empty_season_so_far_starts_from_the_top() -> None:
    """A season_so_far with no games has no last day to resume from."""
    opener = _game(datetime(_FINISHED_NHL_YEAR, 10, 7, 19), "opener")

    async def fake_get_games(league: DailyLeague, day: date) -> List[Game]:
        return [opener] if opener.date.date() == day else []

    cache = _FakeSeasonCache()
    with _patch_get_games(fake_get_games) as mock_get_games:
        season = await get_season(
            NHL,
            _FINISHED_NHL_YEAR,
            season_so_far=Season([Week([], 1)], _FINISHED_NHL_YEAR),
            season_cache=cache,
        )

    assert min(_called_days(mock_get_games)) == NHL.start_date(_FINISHED_NHL_YEAR)
    assert {g.game_id for w in season.weeks for g in w.games} == {"opener"}


def _response_error(status: int) -> aiohttp.ClientResponseError:
    request_info = aiohttp.RequestInfo(
        URL("https://example.com"), "GET", CIMultiDictProxy(CIMultiDict())
    )
    return aiohttp.ClientResponseError(request_info, (), status=status)


async def test_get_season__marks_failed_days_as_trouble() -> None:
    good = _game(datetime(_FINISHED_WNBA_YEAR, 6, 3, 19), "good")
    bad_day = date(_FINISHED_WNBA_YEAR, 6, 4)

    async def fake_get_games(league: DailyLeague, day: date) -> List[Game]:
        if day == bad_day:
            raise _response_error(500)
        return [good] if good.date.date() == day else []

    cache = _FakeSeasonCache()
    with _patch_get_games(fake_get_games):
        season = await get_season(WNBA, _FINISHED_WNBA_YEAR, season_cache=cache)

    assert season.trouble_params == [bad_day]
    # One bad day doesn't lose the rest of the season
    assert {g.game_id for w in season.weeks for g in w.games} == {"good"}


async def test_get_season__caches_a_finished_season() -> None:
    cache = _FakeSeasonCache()

    with _patch_get_games():
        season = await get_season(WNBA, _FINISHED_WNBA_YEAR, season_cache=cache)

    assert cache.saved == [season]


@pytest.mark.parametrize("league", [NHL, WNBA], ids=lambda league: league.name)
async def test_get_season__doesnt_cache_an_unfinished_season(
    league: DailyLeague,
) -> None:
    # Far enough out that the season can't have ended, whenever this runs
    future_year = date.today().year + 5
    cache = _FakeSeasonCache()

    with _patch_get_games() as mock_get_games:
        await get_season(league, future_year, season_cache=cache)

    assert cache.saved == []
    # Days in the future never get requested
    assert _called_days(mock_get_games) == []


async def test_get_season__numbers_weeks_across_the_new_year() -> None:
    """An NHL season's weeks keep counting into January, not restart."""
    october = _game(datetime(_FINISHED_NHL_YEAR, 10, 7, 19), "october")
    january = _game(datetime(_FINISHED_NHL_YEAR + 1, 1, 6, 19), "january")

    async def fake_get_games(league: DailyLeague, day: date) -> List[Game]:
        return [g for g in (october, january) if g.date.date() == day]

    with _patch_get_games(fake_get_games):
        season = await get_season(
            NHL, _FINISHED_NHL_YEAR, season_cache=_FakeSeasonCache()
        )

    weeks = season.calendar_weeks
    assert [w.number for w in weeks] == [4, 17]
    assert [g.game_id for w in weeks for g in w.games] == ["october", "january"]


def _patch_espn_games(games: List[Game]):
    async def fake_get_games(url, parameters):
        return list(games)

    return patch.object(
        daily_module, "get_games", AsyncMock(side_effect=fake_get_games)
    )


async def test_nhl_keeps_scoreless_games() -> None:
    """The NHL played to ties until 2005-06, so 0-0 is a real result."""
    tie = _game(datetime(2003, 11, 5), "tie", home_score=0, away_score=0)

    with _patch_espn_games([tie]):
        games = await get_daily_games(NHL, date(2003, 11, 5))

    assert [g.game_id for g in games] == ["tie"]


async def test_wnba_drops_scoreless_games() -> None:
    """Basketball can't finish 0-0, so a 0-0 game is bad data."""
    bogus = _game(datetime(2015, 6, 5), "bogus", home_score=0, away_score=0)
    real = _game(datetime(2015, 6, 5), "real", home_score=80, away_score=75)

    with _patch_espn_games([bogus, real]):
        games = await get_daily_games(WNBA, date(2015, 6, 5))

    assert [g.game_id for g in games] == ["real"]


@pytest.mark.parametrize(
    "league, old_name, current_name",
    [
        pytest.param(NHL, "Mighty Ducks of Anaheim", "Anaheim Ducks", id="nhl-rename"),
        pytest.param(NHL, "Atlanta Thrashers", "Winnipeg Jets", id="nhl-move"),
        pytest.param(NHL, "Arizona Coyotes", "Utah Mammoth", id="nhl-move-again"),
        pytest.param(WNBA, "Tulsa Shock", "Dallas Wings", id="wnba-move"),
        pytest.param(
            WNBA,
            "San Antonio Silver Stars",
            "Las Vegas Aces",
            id="wnba-rename-and-move",
        ),
    ],
)
async def test_franchises_come_back_under_their_current_name(
    league: DailyLeague, old_name: str, current_name: str
) -> None:
    old = _game(datetime(2005, 11, 5), "old", home=old_name, away="Someone Else")

    with _patch_espn_games([old]):
        games = await get_daily_games(league, date(2005, 11, 5))

    assert games[0].home == current_name
    # Teams that never changed names are left alone
    assert games[0].away == "Someone Else"


def _patch_get_odds(odds: List[dict]):
    async def fake_get_odds(url, parameters):
        for odd in odds:
            yield odd

    return patch.object(daily_module, "get_odds", fake_get_odds)


async def test_get_daily_odds__skips_days_out_of_season() -> None:
    """No point asking ESPN for WNBA odds in February."""
    with _patch_get_odds([{"competition_id": "1", "odds": {}}]):
        odds = [o async for o in get_daily_odds(WNBA, date(2026, 2, 1))]

    assert odds == []


async def test_get_daily_odds__returns_odds_in_season() -> None:
    expected = [{"competition_id": "1", "odds": {}}]

    with _patch_get_odds(expected):
        odds = [o async for o in get_daily_odds(WNBA, date(2026, 7, 1))]

    assert odds == expected


def test_season_start_is_before_the_earliest_game_of_a_season() -> None:
    """Week 1 has to contain the season's opener, not come after it."""
    assert NHL.start_date(2019) < date(2019, 10, 2)
    assert WNBA.start_date(2019) < date(2019, 5, 24)


def test_finished_seasons_are_finished() -> None:
    assert NHL.is_finished(2015)
    assert WNBA.is_finished(2015)
    assert not NHL.is_finished(datetime.now(timezone.utc).year + 5)
    assert not WNBA.is_finished(datetime.now(timezone.utc).year + 5)
