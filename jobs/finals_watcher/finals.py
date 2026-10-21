"""
Which football games have gone final since the last look, and when to look
again.

The decisions only, with no AWS and no network, so they can be tested where
neither exists: `handler.py` does the reading and the starting. Standard
library only, because this file is zipped into the Lambda as it stands, with
nothing installed beside it.

The watcher replaces running the football chains on the clock. Every five
minutes it reads the day's scoreboards, and a game that has gone final since
the last chain it started is a reason to start another. Nothing new means
nothing runs. Between games it doesn't read ESPN at all: it sleeps until the
next kickoff it knows of, or for an hour at most, so a Tuesday costs a couple
of dozen requests rather than three hundred.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/{path}/scoreboard"


@dataclass(frozen=True)
class League:
    # ESPN's path segment for the league.
    path: str
    # Scoreboard `groups` to read, one request each. None is the whole league.
    #
    # ncaafb reads FBS (80) and FCS (81): the games Kalshi lists, and every
    # game their teams play, since a cross-division game is in both lists.
    # Division II/III games alone are left out. Nothing bets them, and the
    # 8am chain still pulls them.
    groups: tuple[str | None, ...]


LEAGUES: dict[str, League] = {
    "ncaafb": League("college-football", ("80", "81")),
    "nfl": League("nfl", (None,)),
}

# ESPN files a game under its kickoff's Eastern calendar day: Saturday's 11pm
# ET kickoff in Honolulu is on Saturday's scoreboard, and it ends around 2:30am
# Sunday. So the day to read is "a few hours ago in Eastern", and both of the
# days that spans. A fixed offset rather than a tz database, because a Lambda
# has no tz database to read: standard time is never more than an hour off,
# and the lookback absorbs that hour.
_EASTERN = timezone(timedelta(hours=-5))
# How far back a kickoff can be and its game still be going. Long enough for a
# weather delay; the oldest finals it catches are ones a run already started.
LOOKBACK = timedelta(hours=10)

# The longest the watcher goes without reading ESPN between games. It only
# knows the kickoffs on the days it reads, so with nothing left today it can't
# know tomorrow's first: this is how stale its picture of the schedule gets.
MAX_QUIET = timedelta(hours=1)

# Finals a run was started for are remembered this long, then forgotten. Far
# longer than LOOKBACK, so a game is gone from the scoreboards being read well
# before the watcher could forget it and start a chain for it again.
REMEMBER = timedelta(days=4)

# A league's ESPN reads failing this many ticks running is an outage worth an
# email rather than a blip worth a log line. A tick is five minutes.
FAILURES_TO_REPORT = 3

# The least time between two runs for one league. It was fifteen minutes when
# a run's first step re-read the whole season -- 54 scoreboard requests for
# ncaafb -- since on a Saturday some game goes final in nearly every five
# minutes from noon to 2am, and 2026-10-17's FBS finals would have started a
# run 50-64 times. The chain's `games` step now re-reads only the weeks with
# a game from the last twelve hours (`games --recent_hours`), three requests
# for ncaafb, so a run every five minutes is fewer ESPN reads than one every
# fifteen was. What's left is a little under the tick, so a tick that fires a
# second early can't be held to the one after; a run still never starts
# while one is going (`handler._in_flight`). Fifteen minutes did cost
# something: on 2026-10-20 a Kalshi book was bought out within twenty
# minutes of the final that moved it.
MIN_GAP = timedelta(minutes=4)

# A game this close to its end is re-read within the tick rather than at the
# next one: the last three minutes of the fourth quarter, or overtime. Its
# final is then seen about a minute after ESPN has it rather than two and a
# half on average, for a few extra reads of the one scoreboard it's on.
NEAR_END_CLOCK = 180.0
RECHECK_EVERY = timedelta(seconds=75)
# How long a tick keeps re-reading. Less than the tick, so one invocation is
# done before the next is due -- the function runs one at a time, and a tick
# that arrives while one is still going is dropped.
RECHECK_BUDGET = timedelta(seconds=200)


@dataclass(frozen=True)
class Game:
    game_id: str
    kickoff: datetime
    # ESPN's coarse status: "pre", "in" or "post". A cancelled or postponed
    # game is "post" and not completed.
    state: str
    completed: bool
    # The quarter (5 and up is overtime) and the seconds left in it. Zero
    # before kickoff.
    period: int = 0
    clock: float = 0.0


def scoreboard_urls(league: str, now: datetime) -> list[str]:
    """Every scoreboard request a look at `league` makes, at `now`."""
    spec = LEAGUES[league]
    url = SCOREBOARD.format(path=spec.path)
    days = sorted({_eastern_day(now), _eastern_day(now - LOOKBACK)})
    urls = []
    for day in days:
        for group in spec.groups:
            params = f"dates={day}&limit=300"
            if group is not None:
                params += f"&groups={group}"
            urls.append(f"{url}?{params}")
    return urls


def _eastern_day(when: datetime) -> str:
    return when.astimezone(_EASTERN).strftime("%Y%m%d")


def parse_scoreboard(payload: dict[str, Any]) -> list[Game]:
    """The games on one scoreboard response."""
    games = []
    for event in payload.get("events", []):
        status = event["status"]["type"]
        games.append(
            Game(
                game_id=str(event["id"]),
                kickoff=_parse_time(event["date"]),
                state=status.get("state", ""),
                completed=bool(status.get("completed")),
                period=int(event["status"].get("period") or 0),
                clock=float(event["status"].get("clock") or 0.0),
            )
        )
    return games


def _parse_time(value: str) -> datetime:
    # ESPN writes "2026-10-17T16:00Z", without seconds, which fromisoformat
    # reads from 3.11 on.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class LeagueState:
    """What the watcher remembers about one league between ticks."""

    # Game id -> when a run was started for its final.
    started: dict[str, str] = field(default_factory=dict)
    # Don't read ESPN for this league before this time. None means look now.
    quiet_until: str | None = None
    # ESPN reads that failed on consecutive ticks.
    failures: int = 0
    # When the last run was started.
    last_started: str | None = None

    @classmethod
    def from_json(cls, value: dict[str, Any] | None) -> LeagueState:
        value = value or {}
        return cls(
            started=dict(value.get("started", {})),
            quiet_until=value.get("quiet_until"),
            failures=int(value.get("failures", 0)),
            last_started=value.get("last_started"),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "quiet_until": self.quiet_until,
            "failures": self.failures,
            "last_started": self.last_started,
        }

    def is_quiet(self, now: datetime) -> bool:
        return self.quiet_until is not None and now < _parse_time(self.quiet_until)

    def too_soon(self, now: datetime) -> bool:
        """Whether a run now would follow the last one by less than MIN_GAP."""
        return (
            self.last_started is not None
            and now - _parse_time(self.last_started) < MIN_GAP
        )


def near_end(game: Game) -> bool:
    """Whether a game being played could be final within a couple of minutes."""
    if game.state != "in":
        return False
    return game.period >= 5 or (game.period == 4 and game.clock <= NEAR_END_CLOCK)


def recheck_urls(games_by_url: dict[str, list[Game]]) -> list[str]:
    """The scoreboards holding a game near its end: the ones worth re-reading."""
    return sorted(
        url for url, games in games_by_url.items() if any(near_end(g) for g in games)
    )


def new_finals(games: list[Game], state: LeagueState) -> list[str]:
    """Finished games no run has been started for yet."""
    return sorted(
        {g.game_id for g in games if g.completed and g.game_id not in state.started}
    )


def quiet_until(
    games: list[Game], waiting: list[str], now: datetime
) -> datetime | None:
    """
    When to look at this league next, or None for the next tick.

    The next tick while any game is going, or any final is still waiting for a
    run. Otherwise not until the next kickoff on the days read, and never more
    than MAX_QUIET away. A game past its kickoff that ESPN still has as "pre"
    counts as going: that's a late start or a slow feed, and either way it's
    about to be live.
    """
    if waiting:
        return None
    if any(g.state == "in" or (g.state == "pre" and g.kickoff <= now) for g in games):
        return None
    upcoming = [g.kickoff for g in games if g.state == "pre" and g.kickoff > now]
    return min([*upcoming, now + MAX_QUIET])


def record_started(state: LeagueState, finals: list[str], now: datetime) -> None:
    """Remember that a run was started for `finals`, and forget old ones."""
    stamp = now.isoformat()
    state.last_started = stamp
    for game_id in finals:
        state.started[game_id] = stamp
    cutoff = now - REMEMBER
    state.started = {
        g: when for g, when in state.started.items() if _parse_time(when) >= cutoff
    }
