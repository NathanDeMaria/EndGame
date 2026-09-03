from datetime import date, datetime, timedelta, timezone
from typing import List, Optional
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from . import daily as daily_module
from .daily import (
    DailyLeague,
    get_daily_games,
    get_league_odds,
    get_season,
    league_play_filter,
)
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
    """
    Stand in for `get_daily_games`, which the doubles below take by day.

    `include_unplayed` is swallowed here rather than added to every double:
    which days a season walks is what these tests are about, and what the
    flag does to a single day belongs to `get_daily_games`'s own tests. The
    mock still records it, so `_unplayed_flags` can check it was passed on.
    """

    async def _call(league: DailyLeague, day: date, **_kwargs) -> List[Game]:
        return await side_effect(league, day)

    return patch.object(daily_module, "get_daily_games", AsyncMock(side_effect=_call))


def _called_days(mock_get_games: AsyncMock) -> List[date]:
    # get_daily_games is called as get_daily_games(league, day,
    # include_unplayed=...), so rebuilding from .args also checks the day
    # itself wasn't passed by keyword.
    assert all(
        set(call.kwargs) <= {"include_unplayed"}
        for call in mock_get_games.await_args_list
    )
    return [call.args[1] for call in mock_get_games.await_args_list]


def _unplayed_flags(mock_get_games: AsyncMock) -> set:
    """The distinct `include_unplayed` values a season's days were asked with."""
    return {
        call.kwargs.get("include_unplayed", False)
        for call in mock_get_games.await_args_list
    }


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


class TestCarryingFixtures:
    """A season pulled with `include_unplayed`, so it holds what's coming.

    A league walked by day only sees a fixture by asking for the day it
    falls on, so the flag on its own would find nothing but the unfinished
    games on days already gone. The walk has to run past today too, which is
    the half these cover.
    """

    # Deep enough into 2015-16 that there's a season either side of it
    _MID_SEASON = date(_FINISHED_NHL_YEAR + 1, 1, 15)

    async def _walk(self, today: date, **kwargs) -> AsyncMock:
        with _frozen_today(today), _patch_get_games() as mock_get_games:
            await get_season(
                NHL, _FINISHED_NHL_YEAR, season_cache=_FakeSeasonCache(), **kwargs
            )
        return mock_get_games

    async def test_a_results_only_pull_still_stops_at_today(self) -> None:
        mock_get_games = await self._walk(self._MID_SEASON)

        assert max(_called_days(mock_get_games)) == self._MID_SEASON - timedelta(days=1)
        assert _unplayed_flags(mock_get_games) == {False}

    async def test_the_walk_runs_a_lookahead_past_today(self) -> None:
        mock_get_games = await self._walk(self._MID_SEASON, include_unplayed=True)

        # date_range's end is exclusive, so the last day asked for is the
        # day before the horizon
        assert max(_called_days(mock_get_games)) == self._MID_SEASON + timedelta(
            days=NHL.lookahead_days - 1
        )
        assert _unplayed_flags(mock_get_games) == {True}

    async def test_the_lookahead_stops_at_the_end_of_the_season(self) -> None:
        """A week past the June final isn't next season's fixtures."""
        season_end = NHL.end_date(_FINISHED_NHL_YEAR)

        mock_get_games = await self._walk(
            season_end - timedelta(days=2), include_unplayed=True
        )

        assert max(_called_days(mock_get_games)) == season_end - timedelta(days=1)

    async def test_it_resumes_from_the_last_result_not_the_last_fixture(self) -> None:
        """Resuming from a fixture starts the walk after the end of it.

        The last game in a season that carries its schedule is one that
        hasn't been played, often weeks out. Resuming from that day would
        leave every run fetching an empty range and writing back exactly
        what it already had -- a pull that never picks up another result and
        never says so.
        """
        played = _game(datetime(_FINISHED_NHL_YEAR + 1, 1, 14, 19), "played")
        scheduled = _game(
            datetime(_FINISHED_NHL_YEAR + 1, 1, 20, 19), "scheduled"
        )._replace(completed=False)
        season_so_far = Season([Week([played, scheduled], 1)], _FINISHED_NHL_YEAR)

        mock_get_games = await self._walk(
            self._MID_SEASON, season_so_far=season_so_far, include_unplayed=True
        )

        called = _called_days(mock_get_games)
        assert called, "the walk asked for nothing at all"
        assert min(called) == played.date.date()

    async def test_a_season_of_nothing_but_fixtures_starts_from_the_top(self) -> None:
        """No result to resume from, so there's no shortcut to take."""
        scheduled = _game(
            datetime(_FINISHED_NHL_YEAR + 1, 1, 20, 19), "scheduled"
        )._replace(completed=False)
        season_so_far = Season([Week([scheduled], 1)], _FINISHED_NHL_YEAR)

        mock_get_games = await self._walk(
            self._MID_SEASON, season_so_far=season_so_far, include_unplayed=True
        )

        assert min(_called_days(mock_get_games)) == NHL.start_date(_FINISHED_NHL_YEAR)


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
    async def fake_get_games(url, parameters, event_filter=None, **_kwargs):
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


async def test_wnba_keeps_a_scoreless_game_it_hasnt_played_yet() -> None:
    """Every fixture is 0-0, so the filter has to read `completed` too.

    ESPN sends 0s for a game that hasn't tipped off, and `parse_game` writes
    them for a fixture with no score at all. Dropping on the scoreline alone
    throws away the whole schedule this pull exists to fetch.
    """
    fixture = _game(
        datetime(2015, 6, 5), "fixture", home_score=0, away_score=0
    )._replace(completed=False)
    bogus = _game(datetime(2015, 6, 5), "bogus", home_score=0, away_score=0)
    real = _game(datetime(2015, 6, 5), "real", home_score=80, away_score=75)

    with _patch_espn_games([fixture, bogus, real]):
        games = await get_daily_games(WNBA, date(2015, 6, 5), include_unplayed=True)

    assert [g.game_id for g in games] == ["fixture", "real"]


@pytest.mark.parametrize("include_unplayed", [False, True])
async def test_get_daily_games_hands_the_flag_to_espn(include_unplayed: bool) -> None:
    """`get_games` is what actually drops the unfinished games."""
    with _patch_espn_games([]) as mock_get_games:
        await get_daily_games(NHL, date(2015, 11, 5), include_unplayed=include_unplayed)

    assert mock_get_games.await_args.kwargs["include_unplayed"] is include_unplayed


@pytest.mark.parametrize(
    "league, old_name, current_name",
    [
        # ESPN's own spelling. The name this used to carry, "Mighty Ducks
        # of Anaheim", is the one nothing ever sends -- so this passed while
        # the rename it was checking never fired on a real game.
        pytest.param(NHL, "Anaheim Mighty Ducks", "Anaheim Ducks", id="nhl-rename"),
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


def _patch_get_odds(odds: List[dict], asked: Optional[List[tuple]] = None):
    async def fake_get_odds_range(url, parameters, *, start, end, chunk_days):
        if asked is not None:
            asked.append((start, end, chunk_days))
        for odd in odds:
            yield odd

    return patch.object(daily_module, "get_odds_range", fake_get_odds_range)


async def test_get_league_odds__skips_a_range_out_of_season() -> None:
    """No point asking ESPN for WNBA odds in February."""
    with _patch_get_odds([{"competition_id": "1", "odds": {}}]):
        odds = [
            o async for o in get_league_odds(WNBA, date(2026, 2, 1), date(2026, 2, 14))
        ]

    assert odds == []


async def test_get_league_odds__returns_odds_in_season() -> None:
    expected = [{"competition_id": "1", "odds": {}}]

    with _patch_get_odds(expected):
        odds = [
            o async for o in get_league_odds(WNBA, date(2026, 7, 1), date(2026, 7, 14))
        ]

    assert odds == expected


async def test_get_league_odds__asks_when_only_part_of_the_range_is_in_season() -> None:
    """
    The range is the request, so one day of it being in season is enough to
    make the whole thing worth asking for. ESPN just returns nothing for the
    days either side, where narrowing to the in-season part would cost an
    extra request for no more data.

    The WNBA starts on May 1st, so this straddles the opening.
    """
    expected = [{"competition_id": "1", "odds": {}}]
    asked: List[tuple] = []

    with _patch_get_odds(expected, asked):
        odds = [
            o async for o in get_league_odds(WNBA, date(2026, 4, 25), date(2026, 5, 5))
        ]

    assert odds == expected
    assert asked == [(date(2026, 4, 25), date(2026, 5, 5), WNBA.odds_chunk_days)]


async def test_get_league_odds__passes_the_league_its_own_chunk_size() -> None:
    """
    Both leagues here play few enough games a day to ask for a long stretch
    at once, rather than the default sized for NCAABB.
    """
    asked: List[tuple] = []

    with _patch_get_odds([], asked):
        [o async for o in get_league_odds(NHL, date(2026, 11, 1), date(2027, 1, 1))]

    assert asked == [(date(2026, 11, 1), date(2027, 1, 1), NHL.odds_chunk_days)]
    assert NHL.odds_chunk_days > 14


def test_season_start_is_before_the_earliest_game_of_a_season() -> None:
    """Week 1 has to contain the season's opener, not come after it."""
    assert NHL.start_date(2019) < date(2019, 10, 2)
    assert WNBA.start_date(2019) < date(2019, 5, 24)


def test_finished_seasons_are_finished() -> None:
    assert NHL.is_finished(2015)
    assert WNBA.is_finished(2015)
    assert not NHL.is_finished(datetime.now(timezone.utc).year + 5)
    assert not WNBA.is_finished(datetime.now(timezone.utc).year + 5)


def _espn_event(
    season_type: Optional[int],
    competition_type: Optional[str],
    name: str = "Away at Home",
) -> dict:
    """
    An ESPN event carrying only the fields the filter reads.
    """
    season = {} if season_type is None else {"season": {"type": season_type}}
    competition: dict = {}
    if competition_type is not None:
        competition["type"] = {"abbreviation": competition_type}
    return dict(id="1", name=name, competitions=[competition], **season)


# Every case is a real event, with the season and competition types ESPN
# actually served for it.
@pytest.mark.parametrize(
    "league,season_type,competition_type,name",
    [
        # An ordinary game -- every regular-season game either league has
        # played back to 2002, the outdoor ones included.
        (NHL, 2, "STD", "New York Rangers at Carolina Hurricanes"),
        (WNBA, 2, "STD", "Chicago Sky at Connecticut Sun"),
        (NHL, 2, "STD", "Washington Capitals at Pittsburgh Penguins (Winter Classic)"),
        # The postseason is kept whatever its rounds are called.
        (NHL, 3, "RD16", "Washington Capitals at New York Rangers"),
        (NHL, 3, "QTR", "Tampa Bay Lightning at Washington Capitals"),
        (NHL, 3, "SEMI", "Dallas Stars at Edmonton Oilers"),
        (NHL, 3, "FINAL", "Vancouver Canucks at Boston Bruins"),
        (WNBA, 3, "FINAL", "Las Vegas Aces at New York Liberty"),
        # The WNBA's Commissioner's Cup final, which only it declares.
        (WNBA, 2, "CC", "Indiana Fever at Minnesota Lynx"),
    ],
)
def test_league_play_is_kept(
    league: DailyLeague, season_type: int, competition_type: str, name: str
) -> None:
    assert league_play_filter(league)(_espn_event(season_type, competition_type, name))


@pytest.mark.parametrize(
    "league,season_type,competition_type,name",
    [
        # Preseason, which is where the games against sides that aren't in
        # the league live.
        (NHL, 1, "STD", "Florida Panthers at Carolina Hurricanes"),
        (NHL, 1, "STD", "Adler Mannheim at Chicago Blackhawks"),
        (WNBA, 1, "STD", "NIGERIA at Indiana Fever"),
        (WNBA, 1, "EXH", "China at Los Angeles Sparks"),
        # The All-Star game is filed under the *regular* season, which is
        # why the season type alone can't be the check.
        (NHL, 2, "ALLSTAR", "Team Staal at Team Lidstrom"),
        (WNBA, 2, "ALLSTAR", "TEAM COLLIER at TEAM CLARK"),
        # The 2023 NHL All-Star replaced the single game with a bracket
        # between the divisions. Its games are "SEMI" -- the same name a
        # conference final has, so only the season type separates them.
        (NHL, 2, "SEMI", "Pacific at Central"),
        # The 4 Nations Face-Off, played in the All-Star's slot in 2025.
        (NHL, 2, "QRR", "USA at Canada"),
        # The Commissioner's Cup is the WNBA's, so it isn't NHL league play.
        (NHL, 2, "CC", "Some Team at Some Other Team"),
    ],
)
def test_exhibitions_are_dropped(
    league: DailyLeague, season_type: int, competition_type: str, name: str
) -> None:
    assert not league_play_filter(league)(
        _espn_event(season_type, competition_type, name)
    )


def test_semi_final_depends_on_the_season_type() -> None:
    """
    The case that makes this take both fields: one name, a conference final
    and an All-Star bracket game.
    """
    is_league_play = league_play_filter(NHL)
    assert is_league_play(_espn_event(3, "SEMI", "Dallas Stars at Edmonton Oilers"))
    assert not is_league_play(_espn_event(2, "SEMI", "Pacific at Central"))


def test_unknown_regular_season_competition_is_dropped() -> None:
    """
    An unrecognized competition costs real games rather than admitting a
    team that doesn't exist -- see `league_play_filter`.
    """
    assert not league_play_filter(NHL)(_espn_event(2, "WHATEVER"))


def test_missing_fields_do_not_admit_an_event() -> None:
    """
    A response missing the blocks this reads mustn't skip the check by
    leaving them out.
    """
    is_league_play = league_play_filter(NHL)
    assert not is_league_play(_espn_event(None, None))
    assert not is_league_play(_espn_event(None, "ALLSTAR"))
    assert not is_league_play({"id": "1", "competitions": [{}]})
    # ... but a normal competition with no season block still reads as the
    # regular season, which is the one league game shape that lacks one.
    assert is_league_play(_espn_event(None, "STD"))


def test_untagged_exhibitions_are_dropped_by_id() -> None:
    """
    The 2002 WNBA All-Star game, which ESPN filed as an ordinary game --
    nothing in the response tells it from league play, so it's named.
    """
    all_star = _espn_event(2, "STD", "Western Conf West at Eastern Conf East")
    all_star["id"] = "220715098"
    assert not league_play_filter(WNBA)(all_star)

    # The same event for a league that hasn't named it is left alone, so
    # one league's exception can't reach another's games.
    assert league_play_filter(NHL)(all_star)


def test_an_ordinary_game_keeps_its_id() -> None:
    ordinary = _espn_event(2, "STD", "Chicago Sky at Connecticut Sun")
    ordinary["id"] = "401244567"
    assert league_play_filter(WNBA)(ordinary)


@pytest.mark.parametrize(
    "name,day,expected",
    [
        # The first Winnipeg franchise: Jets until 1996, then Phoenix, and
        # Utah now.
        ("Winnipeg Jets", date(1994, 11, 20), "Utah Mammoth"),
        ("Winnipeg Jets", date(1996, 3, 1), "Utah Mammoth"),
        ("Phoenix Coyotes", date(1997, 11, 20), "Utah Mammoth"),
        ("Arizona Coyotes", date(2015, 11, 20), "Utah Mammoth"),
        # The second one, which is the old Atlanta Thrashers and stays put.
        ("Winnipeg Jets", date(2013, 11, 20), "Winnipeg Jets"),
        ("Atlanta Thrashers", date(2005, 11, 20), "Winnipeg Jets"),
        # Franchises that moved inside the range now reachable.
        ("Quebec Nordiques", date(1994, 11, 20), "Colorado Avalanche"),
        ("Hartford Whalers", date(1995, 11, 20), "Carolina Hurricanes"),
        # ESPN's spelling of the Ducks before they dropped the "Mighty".
        ("Anaheim Mighty Ducks", date(2003, 11, 20), "Anaheim Ducks"),
        ("Anaheim Ducks", date(2011, 11, 20), "Anaheim Ducks"),
        # Something that never moved.
        ("Boston Bruins", date(1994, 11, 20), "Boston Bruins"),
    ],
)
def test_nhl_renames(name: str, day: date, expected: str) -> None:
    assert NHL.rename_team(name, day) == expected


def test_the_two_winnipeg_franchises_stay_apart() -> None:
    """
    The reason renaming takes a date: one name, two franchises that never
    overlapped, and merging them hands one's history to the other.
    """
    old = NHL.rename_team("Winnipeg Jets", date(1994, 11, 20))
    new = NHL.rename_team("Winnipeg Jets", date(2013, 11, 20))
    assert old != new


def test_wnba_renames_ignore_the_day() -> None:
    for day in (date(2003, 6, 1), date(2025, 6, 1)):
        assert WNBA.rename_team("Detroit Shock", day) == "Dallas Wings"
        assert WNBA.rename_team("Minnesota Lynx", day) == "Minnesota Lynx"
