"""
The finals watcher's Lambda: read the scoreboards, and start a league's
football chain when it has games that went final since the last run.

The decisions are in `finals.py`. This is the reading and starting, and the
one rule that is about the chain rather than the games: don't start a run
while one is still going. And the re-check: a game in its last minutes has
its scoreboard read again inside the tick, so its final doesn't wait for the
next one. `football_plays` reads a week's plays file, adds to
it and writes it back, so two runs at once can each drop what the other
added. A final that arrives mid-run waits a tick and goes in the next one.

Configured by environment, which terraform sets:

    STATE_BUCKET, STATE_KEY  where the watcher keeps what it remembers
    JOB_QUEUE                the queue the chains' jobs run on
    LEAGUES                  JSON: {league: {"state_machine_arn": ...,
                                             "job_names": [...]}}
                             job_names are every job that writes the league's
                             plays -- the 8am chain's as well as this one's.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

import finals

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Every status a Batch job has before it's done.
_ACTIVE = ("SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING")

_s3 = boto3.client("s3")
_batch = boto3.client("batch")
_sfn = boto3.client("stepfunctions")


class EspnUnreachable(Exception):
    """A league's scoreboards have failed to read for several ticks running."""


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    leagues = json.loads(os.environ["LEAGUES"])
    state = _load_state()
    report: dict[str, Any] = {}
    outages = []
    states: dict[str, finals.LeagueState] = {}
    boards: dict[str, dict[str, list[finals.Game]]] = {}

    for league, config in leagues.items():
        league_state = states[league] = finals.LeagueState.from_json(
            state.get(league)
        )
        try:
            report[league] = _watch(league, config, league_state, now, boards)
        except EspnUnreachable as error:
            outages.append(str(error))
            report[league] = {"error": str(error)}

    _recheck(leagues, states, boards, report)
    for league, league_state in states.items():
        state[league] = league_state.to_json()
    _save_state(state)
    logger.info("tick: %s", json.dumps(report))
    # Raised after the state is saved, so the failure count it carries
    # survives the error. Failing the invocation is what sends the email: the
    # function's on-failure destination is the shared failure topic.
    if outages:
        raise EspnUnreachable("; ".join(outages))
    return report


def _watch(
    league: str,
    config: dict[str, Any],
    state: finals.LeagueState,
    now: datetime,
    boards: dict[str, dict[str, list[finals.Game]]],
) -> dict[str, Any]:
    if state.is_quiet(now):
        return {"quiet_until": state.quiet_until}

    try:
        by_url = _read_boards(finals.scoreboard_urls(league, now))
    except (OSError, ValueError) as error:
        # urllib's errors are OSErrors, and a body that isn't JSON is a
        # ValueError. One bad tick is ESPN having a moment, and the next tick
        # tries again; only a run of them is worth telling anyone about.
        state.failures += 1
        logger.warning(
            "%s: scoreboard read failed (%d): %s", league, state.failures, error
        )
        if state.failures == finals.FAILURES_TO_REPORT:
            raise EspnUnreachable(
                f"{league}: {state.failures} scoreboard reads failed running, "
                f"last {error!r}"
            ) from error
        return {"read_failed": state.failures}
    state.failures = 0
    games = _games(by_url)

    new = finals.new_finals(games, state)
    started = None
    if new and state.too_soon(now):
        logger.info("%s: %d new finals wait out the gap", league, len(new))
    elif new:
        busy = _in_flight(config["job_names"])
        if busy:
            logger.info("%s: %d new finals wait on %s", league, len(new), busy)
        else:
            started = _start_chain(config["state_machine_arn"], new, now)
            finals.record_started(state, new, now)
    waiting = new if started is None else []
    # Re-checked only with nothing started and nothing waiting: either way a
    # final seen sooner couldn't start a run before the next tick.
    if not new:
        boards[league] = by_url

    next_look = finals.quiet_until(games, waiting, now)
    state.quiet_until = next_look.isoformat() if next_look else None
    return {
        "games": len(games),
        "new_finals": new,
        "started": started,
        "quiet_until": state.quiet_until,
    }


def _recheck(
    leagues: dict[str, Any],
    states: dict[str, finals.LeagueState],
    boards: dict[str, dict[str, list[finals.Game]]],
    report: dict[str, Any],
) -> None:
    """Re-read the scoreboards with a game in its last minutes, until it goes
    final or the tick's time is up, and start the chain the moment it does.

    One scoreboard per re-read rather than the league's every one: a game
    near its end is on the one it was found on. A league is dropped from
    the loop once it starts a run, or a read fails, or nothing on its boards
    is close any more -- the next tick takes it from there.
    """
    deadline = time.monotonic() + finals.RECHECK_BUDGET.total_seconds()
    every = finals.RECHECK_EVERY.total_seconds()
    while True:
        watching = {
            league: urls
            for league, by_url in boards.items()
            if (urls := finals.recheck_urls(by_url))
        }
        if not watching or time.monotonic() + every > deadline:
            return
        time.sleep(every)
        now = datetime.now(timezone.utc)
        for league, urls in watching.items():
            state, by_url = states[league], boards[league]
            try:
                by_url.update(_read_boards(urls))
            except (OSError, ValueError) as error:
                logger.warning("%s: re-check read failed: %s", league, error)
                del boards[league]
                continue
            new = finals.new_finals(_games(by_url), state)
            if not new:
                continue
            del boards[league]
            config = leagues[league]
            if state.too_soon(now) or _in_flight(config["job_names"]):
                continue
            started = _start_chain(config["state_machine_arn"], new, now)
            finals.record_started(state, new, now)
            report[league] = {
                **report.get(league, {}),
                "rechecked_finals": new,
                "started": started,
            }


def _read_boards(urls: list[str]) -> dict[str, list[finals.Game]]:
    return {url: finals.parse_scoreboard(_get_json(url)) for url in urls}


def _games(by_url: dict[str, list[finals.Game]]) -> list[finals.Game]:
    by_id: dict[str, finals.Game] = {}
    for games in by_url.values():
        for game in games:
            by_id[game.game_id] = game
    return list(by_id.values())


def _get_json(url: str) -> dict[str, Any]:
    # A parameter ESPN ignores, so the CDN never hands back an older read's
    # copy: it serves an expired copy once more while revalidating, which is
    # what had the hourly chain seeing every game an hour late. Same fix as
    # endgame's `web.get`; repeated here because this file runs without
    # endgame installed.
    busted = f"{url}&_={time.time_ns()}"
    request = urllib.request.Request(busted, headers={"User-Agent": "endgame-finals"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def _in_flight(job_names: list[str]) -> list[str]:
    """The jobs among `job_names` that are queued or running, if any."""
    wanted = set(job_names)
    busy = []
    for status in _ACTIVE:
        pages = _batch.get_paginator("list_jobs").paginate(
            jobQueue=os.environ["JOB_QUEUE"], jobStatus=status
        )
        for page in pages:
            busy += [
                f"{job['jobName']} ({status})"
                for job in page["jobSummaryList"]
                if job["jobName"] in wanted
            ]
    return busy


def _start_chain(state_machine_arn: str, new: list[str], now: datetime) -> str:
    # The chain's steps take no input: `games` re-reads the season and
    # `football_plays` pulls whatever finished game it doesn't have. The finals
    # go in anyway, so an execution in the console says why it ran.
    execution = _sfn.start_execution(
        stateMachineArn=state_machine_arn,
        name=f"finals-{now:%Y%m%dT%H%M%S}",
        input=json.dumps({"finals": new}),
    )
    logger.info("started %s for %d finals", execution["executionArn"], len(new))
    return execution["executionArn"]


def _load_state() -> dict[str, Any]:
    try:
        body = _s3.get_object(
            Bucket=os.environ["STATE_BUCKET"], Key=os.environ["STATE_KEY"]
        )["Body"].read()
    except ClientError as error:
        # Only a missing object is a first run. Anything else -- AccessDenied
        # above all -- is raised: read as "no state", it would make every
        # final on the board new on every tick, and start a chain each time.
        if error.response["Error"]["Code"] == "NoSuchKey":
            return {}
        raise
    return json.loads(body)


def _save_state(state: dict[str, Any]) -> None:
    _s3.put_object(
        Bucket=os.environ["STATE_BUCKET"],
        Key=os.environ["STATE_KEY"],
        Body=json.dumps(state, indent=1, sort_keys=True).encode(),
        ContentType="application/json",
    )
