from datetime import datetime, timezone

import pytest

from .types import (
    Game,
    NcaaFbGroup,
    OverlappingWeeksError,
    Season,
    SeasonType,
    Week,
    WeekParams,
    check_weeks_dont_overlap,
    iter_weeks,
    merge_weekly_seasons,
)


def _game(day: int, game_id: str = "1", month: int = 11, year: int = 2023) -> Game:
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


def test_games_in_order() -> None:
    late, early = _game(9, "late"), _game(6, "early")
    week = Week([late, early], 1)

    assert [g.game_id for g in week.games_in_order] == ["early", "late"]
    # The raw list is left alone
    assert [g.game_id for g in week.games] == ["late", "early"]


def test_week_start_and_end() -> None:
    week = Week([_game(9), _game(6), _game(7)], 1)
    assert week.start == datetime(2023, 11, 6, tzinfo=timezone.utc)
    assert week.end == datetime(2023, 11, 9, tzinfo=timezone.utc)


def test_empty_week_has_no_start_or_end() -> None:
    week = Week([], 1)
    assert week.start is None
    assert week.end is None


def test_weeks_in_order() -> None:
    first = Week([_game(6)], 1)
    second = Week([_game(13)], 2)
    season = Season([second, first], 2023)

    assert [w.number for w in season.weeks_in_order] == [1, 2]


def test_weeks_in_order_ignores_week_number() -> None:
    """Sorting is on game dates, since the numbering itself isn't trustworthy."""
    earlier_but_numbered_later = Week([_game(6)], 99)
    later_but_numbered_earlier = Week([_game(13)], 2)
    season = Season([later_but_numbered_earlier, earlier_but_numbered_later], 2023)

    assert [w.number for w in season.weeks_in_order] == [99, 2]


def test_weeks_in_order_puts_empty_weeks_last() -> None:
    empty = Week([], 1)
    dated = Week([_game(6)], 2)
    season = Season([empty, dated], 2023)

    assert [w.number for w in season.weeks_in_order] == [2, 1]


def test_check_weeks_dont_overlap__ok() -> None:
    weeks = [Week([_game(6)], 1), Week([_game(13)], 2)]
    check_weeks_dont_overlap(weeks)


def test_check_weeks_dont_overlap__empty_weeks_ignored() -> None:
    weeks = [Week([_game(6)], 1), Week([], 2), Week([_game(13)], 3)]
    check_weeks_dont_overlap(weeks)


def test_check_weeks_dont_overlap__raises() -> None:
    # The shape the ncaabb incremental merge produces: a week that got
    # games from both ends of the season merged into it.
    spans_season = Week([_game(6), _game(20, month=3, year=2024)], 1)
    normal = Week([_game(13)], 2)

    with pytest.raises(OverlappingWeeksError, match="Week 1"):
        check_weeks_dont_overlap([spans_season, normal])


def test_iter_weeks() -> None:
    first = Week([_game(6)], 1)
    second = Week([_game(13)], 2)
    season = Season([second, first], 2023)

    assert [w.number for w in iter_weeks(season)] == [1, 2]


def test_iter_weeks_validates_eagerly() -> None:
    """The check runs on call, not on first iteration."""
    season = Season(
        [Week([_game(6), _game(20, month=3, year=2024)], 1), Week([_game(13)], 2)], 2023
    )

    with pytest.raises(OverlappingWeeksError):
        iter_weeks(season)


def test_iter_weeks_can_skip_validation() -> None:
    season = Season(
        [Week([_game(6), _game(20, month=3, year=2024)], 1), Week([_game(13)], 2)], 2023
    )

    assert len(list(iter_weeks(season, validate=False))) == 2


# A football season, so the weeks that come back from the source aren't
# chronological and have to be rebuilt from the game dates.
_FB_START = (8, 20)


def _fb_game(month: int, day: int, game_id: str, year: int = 2021) -> Game:
    return _game(day, game_id, month=month, year=year)


def test_calendar_weeks_numbers_from_the_season_start() -> None:
    # Week 1 is the week containing Aug 20, so the first Saturday of the
    # season is week 1 no matter which week number the source gave it.
    opener = Week([_fb_game(8, 21, "opener")], 1)
    season = Season([opener], 2021, None, _FB_START)

    assert [w.number for w in season.calendar_weeks] == [1]


def test_calendar_weeks_splits_out_a_mislabelled_straggler() -> None:
    """The 2021 shape: ESPN put one Oct 30 game in week 8 with the Oct 20-24 games."""
    week_8 = Week(
        [
            _fb_game(10, 23, "saturday"),
            _fb_game(10, 20, "midweek"),
            _fb_game(10, 30, "straggler"),
        ],
        8,
    )
    season = Season([week_8], 2021, None, _FB_START)

    weeks = season.calendar_weeks

    assert [[g.game_id for g in w.games] for w in weeks] == [
        ["midweek", "saturday"],
        ["straggler"],
    ]
    assert [w.number for w in weeks] == [10, 11]
    # The week as ESPN labelled it is left alone, so a game can still be
    # traced back to the request that fetched it.
    assert [w.number for w in season.weeks] == [8]


def test_calendar_weeks_splits_a_month_long_postseason() -> None:
    """The whole postseason comes back as a single week that runs into January."""
    postseason = Week(
        [
            _fb_game(12, 4, "conference_championship"),
            _fb_game(12, 11, "semifinal"),
            _fb_game(12, 18, "early_bowl"),
            _fb_game(1, 10, "title", year=2022),
        ],
        17,
    )
    season = Season([postseason], 2021, None, _FB_START)

    weeks = season.calendar_weeks

    assert [[g.game_id for g in w.games] for w in weeks] == [
        ["conference_championship"],
        ["semifinal"],
        ["early_bowl"],
        ["title"],
    ]
    assert [w.number for w in weeks] == [16, 17, 18, 22]


def test_calendar_weeks_pools_games_from_every_source_week() -> None:
    # A cross-division game comes back under more than one request, and the
    # copies aren't guaranteed to be identical -- the last one wins.
    fbs = Week([_fb_game(9, 4, "shared")], 2)
    fcs = Week([_fb_game(9, 4, "shared")._replace(home_score=99)], 2)
    season = Season([fbs, fcs], 2021, None, _FB_START)

    (week,) = season.calendar_weeks

    assert [g.game_id for g in week.games] == ["shared"]
    assert week.games[0].home_score == 99


def test_calendar_weeks_needs_a_season_start() -> None:
    season = Season([Week([_game(6)], 1)], 2023)

    with pytest.raises(ValueError, match="season_start"):
        _ = season.calendar_weeks


def test_iter_weeks_regroups_overlapping_source_weeks() -> None:
    """The 2021 overlap: the postseason bucket straddles regular season week 15.

    Sorting can't fix this -- week 17 starts before week 15 and ends a month
    after it -- so the weeks have to be rebuilt from the game dates.
    """
    postseason = Week([_fb_game(12, 4, "bowl"), _fb_game(1, 10, "title", 2022)], 17)
    fcs_playoffs = Week([_fb_game(12, 11, "fcs_semifinal")], 15)
    season = Season([postseason, fcs_playoffs], 2021, None, _FB_START)

    weeks = list(iter_weeks(season))

    assert [[g.game_id for g in w.games] for w in weeks] == [
        ["bowl"],
        ["fcs_semifinal"],
        ["title"],
    ]
    # ...and the season as fetched is still the overlapping mess ESPN sent
    with pytest.raises(OverlappingWeeksError):
        check_weeks_dont_overlap(season.weeks_in_order)


def test_iter_weeks_walks_source_weeks_without_a_season_start() -> None:
    """A league whose own week numbers are chronological is left alone."""
    season = Season([Week([_game(13)], 2), Week([_game(6)], 1)], 2023)

    assert [w.number for w in iter_weeks(season)] == [1, 2]


def _scored(game: Game, home_score: int, away_score: int) -> Game:
    return game._replace(home_score=home_score, away_score=away_score)


class TestMergeWeeklySeasons:
    """Folding a fresh pull over what's already saved.

    The property that matters is that a merge can only add and correct.
    Before it, `games` wrote whatever the fetch returned straight over the
    season in the bucket -- so a pull that lost a week to an ESPN 5xx
    replaced a complete season with a smaller one, silently.
    """

    def test_a_game_only_the_old_season_has_survives(self) -> None:
        old = Season([Week([_game(4, "kept")], 1)], 2023)
        # What a pull that dropped a week looks like: fewer games, no error.
        partial = Season([Week([_game(11, "fresh")], 2)], 2023)

        merged = merge_weekly_seasons([old, partial])

        assert {g.game_id for w in merged.weeks for g in w.games} == {
            "kept",
            "fresh",
        }

    def test_an_empty_pull_cannot_empty_the_season(self) -> None:
        old = Season([Week([_game(4, "kept")], 1)], 2023)

        merged = merge_weekly_seasons([old, Season([], 2023)])

        assert [g.game_id for w in merged.weeks for g in w.games] == ["kept"]

    def test_the_later_copy_wins(self) -> None:
        """A score gets corrected upstream, so the fresh copy has to win."""
        stale = _game(4, "game")
        old = Season([Week([stale], 1)], 2023)
        fresh = Season([Week([_scored(stale, 42, 17)], 1)], 2023)

        merged = merge_weekly_seasons([old, fresh])

        [game] = [g for w in merged.weeks for g in w.games]
        assert (game.home_score, game.away_score) == (42, 17)

    def test_each_game_keeps_the_week_it_was_fetched_under(self) -> None:
        """ESPN's week numbers are how a game is traced back to its request.

        Merging through a date-based regroup -- which is what the daily
        leagues' merge does -- would replace them with calendar weeks and
        lose that.
        """
        old = Season([Week([_game(4, "early")], 3)], 2023)
        fresh = Season([Week([_game(11, "late")], 9)], 2023)

        merged = merge_weekly_seasons([old, fresh])

        assert {w.number: [g.game_id for g in w.games] for w in merged.weeks} == {
            3: ["early"],
            9: ["late"],
        }

    def test_trouble_params_are_unioned_without_being_sorted(self) -> None:
        """They hold enums, and enums don't order.

        Deduping through a sorted set raises TypeError here, which is the
        kind of thing that only shows up on the one season that had a bad
        week.
        """
        first = WeekParams(2023, 3, SeasonType.regular, NcaaFbGroup.fbs)
        second = WeekParams(2023, 9, SeasonType.post, NcaaFbGroup.fcs)
        old = Season([], 2023, [first, second])
        fresh = Season([], 2023, [second])

        merged = merge_weekly_seasons([old, fresh])

        assert merged.trouble_params is not None
        assert set(merged.trouble_params) == {first, second}
        assert len(merged.trouble_params) == 2

    def test_an_untagged_old_season_does_not_untag_the_new_one(self) -> None:
        """Seasons pickled before `season_start` existed come back untagged.

        Taking the first one's tag would drop the new season's, and the
        chronological walk silently goes back to the source's week order.
        """
        untagged = Season([Week([_game(4, "old")], 1)], 2023)
        tagged = Season([Week([_game(11, "new")], 2)], 2023, [], (8, 20))

        assert merge_weekly_seasons([untagged, tagged]).season_start == (8, 20)

    def test_it_refuses_seasons_from_different_years(self) -> None:
        with pytest.raises(AssertionError):
            merge_weekly_seasons([Season([], 2023), Season([], 2024)])
