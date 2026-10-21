"""
`finals.py`'s decisions. unittest rather than pytest, because these run in the
terraform workflow's lint job (`make lint`), which installs no Python
packages: the watcher's code changes ship with `jobs/`, and that's the
workflow that sees them.
"""

import unittest
from datetime import datetime, timedelta, timezone

import finals
from finals import Game, LeagueState

SATURDAY_NIGHT = datetime(2026, 10, 18, 3, 30, tzinfo=timezone.utc)  # 10:30pm CT


def _game(game_id, kickoff, state, completed=False):
    return Game(game_id, kickoff, state, completed)


class ScoreboardUrls(unittest.TestCase):
    def test_late_saturday_reads_saturday_and_the_fbs_and_fcs_lists(self):
        urls = finals.scoreboard_urls("ncaafb", SATURDAY_NIGHT)

        self.assertEqual(
            urls,
            [
                f"{finals.SCOREBOARD.format(path='college-football')}"
                f"?dates=20261017&limit=300&groups={group}"
                for group in ("80", "81")
            ],
        )

    def test_after_midnight_eastern_still_reads_yesterday(self):
        # 2:30am ET Sunday: Saturday's Hawaii game is still being played, and
        # it's on Saturday's scoreboard.
        now = datetime(2026, 10, 18, 7, 30, tzinfo=timezone.utc)

        days = {u.split("dates=")[1][:8] for u in finals.scoreboard_urls("nfl", now)}

        self.assertEqual(days, {"20261017", "20261018"})

    def test_nfl_has_no_groups(self):
        (url,) = finals.scoreboard_urls("nfl", SATURDAY_NIGHT)

        self.assertNotIn("groups", url)


class ParseScoreboard(unittest.TestCase):
    def test_reads_id_kickoff_and_status(self):
        payload = {
            "events": [
                {
                    "id": "401872999",
                    "date": "2026-10-18T17:00Z",
                    "status": {"type": {"state": "post", "completed": True}},
                },
                {
                    "id": "401873007",
                    "date": "2026-10-19T00:20Z",
                    "status": {"type": {"state": "pre", "completed": False}},
                },
            ]
        }

        games = finals.parse_scoreboard(payload)

        self.assertEqual(
            games,
            [
                _game(
                    "401872999",
                    datetime(2026, 10, 18, 17, tzinfo=timezone.utc),
                    "post",
                    True,
                ),
                _game(
                    "401873007",
                    datetime(2026, 10, 19, 0, 20, tzinfo=timezone.utc),
                    "pre",
                ),
            ],
        )

    def test_a_day_with_no_games(self):
        self.assertEqual(finals.parse_scoreboard({"events": []}), [])


class NewFinals(unittest.TestCase):
    def test_only_finals_no_run_was_started_for(self):
        kickoff = SATURDAY_NIGHT - timedelta(hours=4)
        games = [
            _game("1", kickoff, "post", True),
            _game("2", kickoff, "post", True),
            _game("3", kickoff, "in"),
            # Cancelled: over, never played, not a final.
            _game("4", kickoff, "post", False),
        ]
        state = LeagueState(started={"1": SATURDAY_NIGHT.isoformat()})

        self.assertEqual(finals.new_finals(games, state), ["2"])


class QuietUntil(unittest.TestCase):
    def test_looks_again_next_tick_while_a_game_is_on(self):
        games = [_game("1", SATURDAY_NIGHT - timedelta(hours=1), "in")]

        self.assertIsNone(finals.quiet_until(games, [], SATURDAY_NIGHT))

    def test_looks_again_next_tick_while_a_final_waits_for_a_run(self):
        games = [_game("1", SATURDAY_NIGHT - timedelta(hours=4), "post", True)]

        self.assertIsNone(finals.quiet_until(games, ["1"], SATURDAY_NIGHT))

    def test_a_game_past_kickoff_still_marked_pre_counts_as_on(self):
        games = [_game("1", SATURDAY_NIGHT - timedelta(minutes=10), "pre")]

        self.assertIsNone(finals.quiet_until(games, [], SATURDAY_NIGHT))

    def test_sleeps_until_the_next_kickoff(self):
        kickoff = SATURDAY_NIGHT + timedelta(minutes=25)
        games = [
            _game("1", SATURDAY_NIGHT - timedelta(hours=4), "post", True),
            _game("2", kickoff, "pre"),
        ]

        self.assertEqual(finals.quiet_until(games, [], SATURDAY_NIGHT), kickoff)

    def test_never_sleeps_longer_than_max_quiet(self):
        games = [_game("1", SATURDAY_NIGHT + timedelta(hours=14), "pre")]

        self.assertEqual(
            finals.quiet_until(games, [], SATURDAY_NIGHT),
            SATURDAY_NIGHT + finals.MAX_QUIET,
        )

    def test_a_day_with_nothing_on_sleeps_max_quiet(self):
        self.assertEqual(
            finals.quiet_until([], [], SATURDAY_NIGHT),
            SATURDAY_NIGHT + finals.MAX_QUIET,
        )


class LeagueStateRoundTrip(unittest.TestCase):
    def test_json_round_trip(self):
        state = LeagueState(
            started={"1": SATURDAY_NIGHT.isoformat()},
            quiet_until=SATURDAY_NIGHT.isoformat(),
            failures=2,
            last_started=SATURDAY_NIGHT.isoformat(),
        )

        self.assertEqual(LeagueState.from_json(state.to_json()), state)

    def test_no_state_is_a_first_run(self):
        self.assertEqual(LeagueState.from_json(None), LeagueState())

    def test_quiet_until_a_time_still_ahead(self):
        state = LeagueState(
            quiet_until=(SATURDAY_NIGHT + timedelta(minutes=1)).isoformat()
        )

        self.assertTrue(state.is_quiet(SATURDAY_NIGHT))
        self.assertFalse(state.is_quiet(SATURDAY_NIGHT + timedelta(minutes=2)))
        self.assertFalse(LeagueState().is_quiet(SATURDAY_NIGHT))


class RecordStarted(unittest.TestCase):
    def test_remembers_new_finals_and_forgets_old_ones(self):
        old = (SATURDAY_NIGHT - finals.REMEMBER - timedelta(minutes=1)).isoformat()
        recent = (SATURDAY_NIGHT - timedelta(days=1)).isoformat()
        state = LeagueState(started={"old": old, "recent": recent})

        finals.record_started(state, ["new"], SATURDAY_NIGHT)

        self.assertEqual(
            state.started,
            {"recent": recent, "new": SATURDAY_NIGHT.isoformat()},
        )
        self.assertEqual(state.last_started, SATURDAY_NIGHT.isoformat())


class TooSoon(unittest.TestCase):
    def test_holds_a_run_until_the_gap_has_passed(self):
        state = LeagueState()
        self.assertFalse(state.too_soon(SATURDAY_NIGHT))

        finals.record_started(state, ["1"], SATURDAY_NIGHT)

        self.assertTrue(state.too_soon(SATURDAY_NIGHT + timedelta(minutes=3)))
        # The next tick, even one firing a few seconds early, is past it.
        tick = SATURDAY_NIGHT + timedelta(minutes=5) - timedelta(seconds=5)
        self.assertFalse(state.too_soon(tick))
        self.assertFalse(state.too_soon(SATURDAY_NIGHT + finals.MIN_GAP))


if __name__ == "__main__":
    unittest.main()


class NearEnd(unittest.TestCase):
    def _in(self, period, clock):
        return Game("1", SATURDAY_NIGHT, "in", False, period, clock)

    def test_the_last_three_minutes_and_overtime_are_near_the_end(self):
        self.assertTrue(finals.near_end(self._in(4, 179.0)))
        self.assertTrue(finals.near_end(self._in(5, 600.0)))
        self.assertFalse(finals.near_end(self._in(4, 400.0)))
        self.assertFalse(finals.near_end(self._in(3, 30.0)))
        self.assertFalse(finals.near_end(_game("2", SATURDAY_NIGHT, "post", True)))

    def test_only_the_boards_with_a_close_game_are_reread(self):
        boards = {
            "fbs": [self._in(4, 60.0), self._in(2, 300.0)],
            "fcs": [self._in(3, 60.0)],
        }

        self.assertEqual(finals.recheck_urls(boards), ["fbs"])

    def test_the_clock_is_parsed_off_the_scoreboard(self):
        payload = {
            "events": [
                {
                    "id": "1",
                    "date": "2026-10-18T00:00Z",
                    "status": {
                        "period": 4,
                        "clock": 92.0,
                        "type": {"state": "in", "completed": False},
                    },
                }
            ]
        }

        (game,) = finals.parse_scoreboard(payload)

        self.assertEqual((game.period, game.clock), (4, 92.0))
        self.assertTrue(finals.near_end(game))
