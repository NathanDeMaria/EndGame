import asyncio
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from functools import wraps
from inspect import iscoroutinefunction
from typing import AsyncIterator, Awaitable, Callable
from zoneinfo import ZoneInfo

from endgame.date import get_end_year
from endgame.espn_odds import Odds as EspnOdds
from endgame.ncaabb import NcaabbGender, get_plays_for_day
from endgame.ncaabb.box_score.all import get_season_box_scores
from endgame.ncaabb.matchup import apply_in_parallel, get_possessions, logger
from endgame.ncaabb.ncaabb import (
    REGULAR_SEASON_START,
    Season,
    get_ncaabb_season,
    get_ncaabb_spreads,
)
from endgame.ncaabb.possession_side import PossessionSide
from endgame.ncaafb import FIRST_WEEK_ZERO_SEASON
from endgame.ncaafb import SEASON_END as NCAAFB_SEASON_END
from endgame.ncaafb import get_current_odds as get_ncaafb_current_odds
from endgame.ncaafb import get_season as get_ncaafb_season
from endgame.nfl.games import get_current_odds as get_nfl_current_odds
from endgame.nfl.games import get_season as get_nfl_season
from endgame.nhl import get_nhl_odds, get_nhl_season
from endgame.types import (
    Game,
    group_games_into_weeks,
    iter_weeks,
    merge_weekly_seasons,
)
from endgame.wnba import get_wnba_odds, get_wnba_season
from fire import Fire

from . import (
    Config,
    FlattenedBoxScore,
    get_pbp_store,
    list_all_keys,
    read_box_scores,
    read_possessions,
    save_csv_to_s3,
    save_data_to_s3,
    save_to_s3,
)
from .io import S3NotFoundException, read_seasons

_CONFIG = Config.init_from_file()


def _build_season_key(year: int, gender: NcaabbGender) -> str:
    return f"seasons/{year}/{gender.name}.pkl"


def _build_possession_key(year: int, gender: NcaabbGender) -> str:
    return f"seasons/{year}/{gender.name}.csv"


def _build_box_score_key(year: int, gender: NcaabbGender) -> str:
    return f"seasons/{year}/{gender.name}_box.csv"


async def _load_season(bucket: str, key: str) -> Season | None:
    try:
        seasons = await read_seasons(bucket, key)
        return seasons[0]
    except S3NotFoundException:
        return None


async def _load_possessions(
    bucket: str, year: int, gender: NcaabbGender
) -> list[PossessionSide]:
    try:
        return await read_possessions(bucket, _build_possession_key(year, gender))
    except S3NotFoundException:
        return []


async def _load_box_scores(
    bucket: str, year: int, gender: NcaabbGender
) -> list[FlattenedBoxScore]:
    try:
        return await read_box_scores(bucket, _build_box_score_key(year, gender))
    except S3NotFoundException:
        return []


async def box_scores(gender_name: str, year: int):
    gender = NcaabbGender[gender_name]
    season_so_far = await _load_season(_CONFIG.bucket, _build_season_key(year, gender))
    season = await get_ncaabb_season(year, gender, season_so_far)
    await save_to_s3([season], _CONFIG.bucket, _build_season_key(year, gender))

    has_games = any(game for week in season.weeks for game in week.games)
    if not has_games:
        logger.warning("No games found (yet) for %s %d", gender.name, year)
        return

    rows_so_far = await _load_possessions(_CONFIG.bucket, year, gender)
    rows: list[dict] = [r.to_dict() for r in rows_so_far]
    pulled_game_ids = {side.game_id for side in rows_so_far}
    # A week at a time, so the work is batched (and logged) in chunks
    # rather than firing off a whole season's games at once.
    for week in iter_weeks(season):
        # Only finished games have possessions to count. A season that
        # carries games which haven't been played would otherwise spend a
        # request on each of them, every run, until they're played.
        finished = [game for game in week.games if game.completed]
        args = [
            (gender, game.game_id)
            for game in finished
            if game.game_id not in pulled_game_ids and game.completed
        ]
        if not args:
            logger.info(
                "No new matchups for %d %d (%d of %d games finished)",
                season.year,
                week.number,
                len(finished),
                len(week.games),
            )
            continue
        logger.info("Getting matchups for %d %d", season.year, week.number)
        games = apply_in_parallel(get_possessions, args)
        async for sides in games:
            if sides is None:
                continue
            rows.extend(side.to_dict() for side in sides)
    await save_csv_to_s3(rows, _CONFIG.bucket, _build_possession_key(year, gender))

    box_score_rows_so_far = await _load_box_scores(_CONFIG.bucket, year, gender)
    box_score_rows: list[dict] = [r.to_dict() for r in box_score_rows_so_far]
    pulled_game_ids = {side.game_id for side in box_score_rows_so_far}
    async for box_score in get_season_box_scores(season, gender, pulled_game_ids):
        box_score_rows.extend(
            FlattenedBoxScore.from_player(
                player,
                team_id=box_score.home.team_id,
                game_id=box_score.game_id,
            ).to_dict()
            for player in box_score.home.players
        )
        box_score_rows.extend(
            FlattenedBoxScore.from_player(
                player,
                team_id=box_score.away.team_id,
                game_id=box_score.game_id,
            ).to_dict()
            for player in box_score.away.players
        )
    if not box_score_rows:
        # Early seasons (ex: NCAAWBB 2011) don't have box scores
        return
    await save_csv_to_s3(
        box_score_rows, _CONFIG.bucket, _build_box_score_key(year, gender)
    )


@dataclass
class _GamesLeague:
    # `season_so_far` is only useful for leagues pulled a day at a time
    # (nhl, wnba): the job starts with an empty web cache, so handing it
    # what's already in S3 means a run picks up from the last day it has
    # instead of walking the whole season again. nfl/ncaafb are pulled a
    # week at a time and don't take one.
    get_season: Callable[[int, Season | None], Awaitable[Season]]
    incremental: bool = False


# Leagues whose games are a single "get the season, save it" pull.
# ncaabb isn't here: its `box_scores` command also pulls possessions/box
# scores, so it stays a separate, bigger pipeline.
_GAMES_LEAGUES: dict[str, _GamesLeague] = {
    "nfl": _GamesLeague(get_season=lambda year, _so_far: get_nfl_season(year)),
    # The one league that carries its fixtures as well as its results, so
    # downstream can see what's coming. Everything else is results-only
    # until it has a reason not to be. Readers split the two on
    # `game.completed`, and tell scheduled from cancelled on `game.status`.
    "ncaafb": _GamesLeague(
        get_season=lambda year, _so_far: get_ncaafb_season(year, include_unplayed=True)
    ),
    "nhl": _GamesLeague(get_season=get_nhl_season, incremental=True),
    "wnba": _GamesLeague(get_season=get_wnba_season, incremental=True),
}


def _count_games(season: Season | None) -> int:
    """
    How many results a season holds.

    Completed games only, which is what the shrink guard is protecting: a
    season that carries its fixtures too has a game count that moves for
    reasons that aren't losses -- a cancellation, a fixture ESPN drops --
    and guarding that number would fire on a schedule doing what schedules
    do. Results are the thing a pull must never lose.
    """
    if season is None:
        return 0
    return sum(1 for week in season.weeks for game in week.games if game.completed)


async def _save_merged(key: str, existing: Season | None, season: Season) -> int:
    """
    Write `season` folded over what is already in the bucket, never under it.

    A pull is a full re-fetch for the leagues fetched by week, so a bad one
    -- an ESPN 5xx, a rate limit, a week that lands in `trouble_params` --
    used to write a smaller season straight over a complete one and say
    nothing about it. Merging means a run can add games and correct them,
    and cannot drop them.

    The leagues fetched by day have already folded in `season_so_far` by the
    time they get here, so this is a no-op union for them. Uniform is easier
    to reason about than conditional, and the cost is one dict pass.
    """
    if existing is not None:
        season = merge_weekly_seasons([existing, season])

    before, after = _count_games(existing), _count_games(season)
    # Unreachable after the merge above, which is exactly why it raises
    # rather than warns: fewer games than the bucket already had means the
    # merge is broken, and the write on the next line would make that
    # permanent.
    if after < before:
        raise RuntimeError(
            f"refusing to shrink {key}: {before} games in the bucket, "
            f"{after} after merging this pull"
        )

    await save_to_s3([season], _CONFIG.bucket, key)
    return after


async def games(league: str, year: int) -> None:
    games_league = _GAMES_LEAGUES[league]
    key = f"seasons/{year}/{league}.pkl"
    existing = await _load_season(_CONFIG.bucket, key)
    season = await games_league.get_season(
        year, existing if games_league.incremental else None
    )
    saved = await _save_merged(key, existing, season)
    logger.info(
        "Saved %d games for %s %d (%d before this run)",
        saved,
        league,
        year,
        _count_games(existing),
    )


async def backfill_week_zero(
    first_year: int = FIRST_WEEK_ZERO_SEASON,
    last_year: int | None = None,
    dry_run: bool = True,
) -> None:
    """
    Re-pull ncaafb seasons so they pick up week 0.

    The daily job fixes the current season on its own -- it re-fetches the
    whole thing every run -- so this is for the seasons already sitting in
    the bucket, written before week 0 was asked for.

    Defaults to a dry run; pass --dry_run=False to write. Run one year
    first and read the numbers before turning it loose on the range: every
    line should show a season gaining games or standing still, and the
    write refuses if one would shrink.

    `first_year` defaults to the first season ESPN has a week 0 for. Lower
    it to find out whether an earlier one does -- a dry run costs a
    re-fetch and writes nothing, which is the cheap way to check that
    constant rather than trusting it.

    Every season is fetched from ESPN rather than from the local season
    cache, so a run is slow on purpose: it is dozens of requests per
    season. A run that finishes instantly, with no "Getting NCAAFB ..."
    lines per week, is not doing what it says.
    """
    last = last_year if last_year is not None else get_end_year(NCAAFB_SEASON_END)
    for year in range(first_year, last + 1):
        key = f"seasons/{year}/ncaafb.pkl"
        existing = await _load_season(_CONFIG.bucket, key)
        if existing is None:
            logger.warning("%s is not in the bucket; skipping", key)
            continue

        # `use_cache=False` is load-bearing, not tidiness. The season cache
        # is written for every season that has ended, so any machine that
        # has pulled these years already has one -- and a cache hit hands
        # back the very pre-week-0 season this command exists to replace,
        # without a single request. The first real run of this reported
        # that every season gained exactly nothing.
        season = merge_weekly_seasons(
            [existing, await get_ncaafb_season(year, use_cache=False)]
        )
        before, after = _count_games(existing), _count_games(season)
        logger.info(
            "%s: %d -> %d games (+%d)%s",
            key,
            before,
            after,
            after - before,
            " (dry run)" if dry_run else "",
        )

        if after == before:
            # Nothing to add, so nothing is written -- an object rewritten
            # to the same contents still costs a version and moves its
            # last-modified, which is the signal the job dashboard reads.
            continue
        if not dry_run:
            await _save_merged(key, existing, season)


_NCAABB_SEASON_KEY_RE = re.compile(r"^seasons/(\d+)/(mens|womens)\.pkl$")


async def regroup_ncaabb_weeks(dry_run: bool = True) -> None:
    """
    Rebuild the week grouping on already-saved ncaabb seasons.

    Weeks used to be numbered by position among whatever games a run
    happened to fetch, so an incremental pull restarted at 1 and merged its
    games into the season's opening weeks. Regrouping from game dates fixes
    the numbering and puts the games back in the right weeks.

    Only touches ncaabb: nfl/ncaafb week numbers come from ESPN.

    Defaults to a dry run -- pass --dry_run=False to actually write.
    """
    async for key in list_all_keys(_CONFIG.bucket, "seasons/"):
        match = _NCAABB_SEASON_KEY_RE.match(key)
        if match is None:
            continue
        year = int(match.group(1))

        seasons = await read_seasons(_CONFIG.bucket, key)
        regrouped = [
            Season(
                group_games_into_weeks(
                    (game for week in season.weeks for game in week.games),
                    year,
                    REGULAR_SEASON_START,
                ),
                season.year,
                season.trouble_params,
                REGULAR_SEASON_START,
            )
            for season in seasons
        ]

        before = [len(s.weeks) for s in seasons]
        after = [len(s.weeks) for s in regrouped]
        if before == after and all(
            [w.number for w in old.weeks] == [w.number for w in new.weeks]
            for old, new in zip(seasons, regrouped)
        ):
            logger.info("%s already grouped correctly", key)
            continue

        logger.info(
            "%s: %s weeks -> %s weeks%s",
            key,
            before,
            after,
            " (dry run)" if dry_run else "",
        )
        if not dry_run:
            await save_to_s3(regrouped, _CONFIG.bucket, key)


def _parse_date(date_str: str | None) -> date:
    if date_str is None:
        return datetime.now(tz=ZoneInfo("America/Chicago")).date()
    return datetime.fromisoformat(date_str).date()


@dataclass
class _OddsLeague:
    # `day` is only meaningful for the leagues scheduled by day rather than
    # by week (ncaabb, nhl, wnba); nfl/ncaafb just return whatever ESPN
    # calls "this week".
    get_odds: Callable[[date], AsyncIterator[EspnOdds]]


_ODDS_LEAGUES: dict[str, _OddsLeague] = {
    "ncaabb": _OddsLeague(get_odds=get_ncaabb_spreads),
    "nfl": _OddsLeague(get_odds=lambda _day: get_nfl_current_odds()),
    "ncaafb": _OddsLeague(get_odds=lambda _day: get_ncaafb_current_odds()),
    "nhl": _OddsLeague(get_odds=get_nhl_odds),
    "wnba": _OddsLeague(get_odds=get_wnba_odds),
}


async def odds(league: str, day: str | None = None, time: str | None = None) -> None:
    now = datetime.now(tz=ZoneInfo("America/Chicago"))
    parsed_date = _parse_date(day)
    parsed_time = time if time is not None else now.strftime("%H-%M")
    league_odds = [o async for o in _ODDS_LEAGUES[league].get_odds(parsed_date)]
    await save_data_to_s3(
        _CONFIG.bucket,
        f"odds/{league}/{parsed_date}/{parsed_time}.json",
        json.dumps(league_odds).encode(),
    )
    logger.info(
        "Saved %d odds for %s on %s at %s",
        len(league_odds),
        league,
        parsed_date,
        parsed_time,
    )


async def plays(league: str, day: str | None = None) -> None:
    parsed_date = _parse_date(day)
    pbps = get_plays_for_day(parsed_date, NcaabbGender[league])
    all_plays = [plays async for plays in pbps]
    async with get_pbp_store() as store:
        await store.save(all_plays, parsed_date, NcaabbGender[league])
    logger.info(
        "Saved pbp for %d games for %s on %s.", len(all_plays), league, parsed_date
    )


async def preview_unplayed(year: int | None = None) -> None:
    """
    Report what turning on `include_unplayed` would do to ncaafb's key.

    Writes nothing -- this is the look before the one-line flip in
    `_GAMES_LEAGUES`, which is the first change that puts games with no
    result into the bucket the rest of the pipeline reads.

    The numbers to read before flipping:

    - "completed lost/downgraded" and "duplicate ids" must all be zero.
      Those are the properties a merge is supposed to guarantee, and the
      only ones whose failure is silent.
    - the two shrink-guard lines. `_count_games` counts every game, so the
      guard stops meaning "never lose a result" the moment a schedule is
      in the key -- the completed-only line is what it has to become.
    - the status breakdown, which is what invisible-string gets.

    Only ncaafb: it's the one league `get_season` takes the flag for. A
    run is a full re-fetch, so it's slow on purpose -- dozens of requests,
    the same as a real pull.
    """
    season_year = year if year is not None else get_end_year(NCAAFB_SEASON_END)
    key = f"seasons/{season_year}/ncaafb.pkl"

    existing = await _load_season(_CONFIG.bucket, key)
    pulled = await get_ncaafb_season(season_year, include_unplayed=True)

    def by_id(season: Season | None) -> dict[str, Game]:
        if season is None:
            return {}
        return {g.game_id: g for w in season.weeks for g in w.games}

    def raw_games(season: Season | None) -> list[Game]:
        if season is None:
            return []
        return [g for w in season.weeks for g in w.games]

    old_games = by_id(existing)
    # What the same pull would have returned with the flag off. Deriving it
    # rather than fetching twice: the flag only ever adds, so the completed
    # half of one pull is the whole of the other.
    off = {gid: g for gid, g in by_id(pulled).items() if g.completed}
    merged_season = merge_weekly_seasons([s for s in (existing, pulled) if s])
    merged = by_id(merged_season)

    logger.info("%s", key)
    logger.info(
        "  in the bucket now: %d games, %d completed",
        len(old_games),
        sum(g.completed for g in old_games.values()),
    )
    logger.info("  this pull, flag off: %d games", len(off))
    logger.info("  this pull, flag on:  %d games", len(by_id(pulled)))

    logger.info("  after merging, by status:")
    counts: dict[str, int] = {}
    for game in merged.values():
        counts[game.status or "(saved before status existed)"] = (
            counts.get(game.status or "(saved before status existed)", 0) + 1
        )
    for status, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        logger.info("    %-34s %5d", status, count)

    completed_before = sum(g.completed for g in old_games.values())
    completed_after = sum(g.completed for g in merged.values())
    logger.info(
        "  shrink guard, counting every game: %d -> %d",
        len(old_games),
        len(merged),
    )
    logger.info(
        "  shrink guard, completed only:      %d -> %d",
        completed_before,
        completed_after,
    )

    lost = [gid for gid in old_games if gid not in merged]
    downgraded = [
        gid
        for gid, game in old_games.items()
        if game.completed and not merged[gid].completed
    ]
    logger.info("  completed lost:       %s", lost or "none")
    logger.info("  completed downgraded: %s", downgraded or "none")

    # A game fetched under two divisions used to survive as two rows the
    # moment its copies disagreed, which they only do while it's being
    # played -- so this is checked here rather than trusted.
    seen = Counter(g.game_id for g in raw_games(merged_season))
    duplicated = sorted(gid for gid, n in seen.items() if n > 1)
    logger.info("  duplicate ids:        %s", duplicated or "none")

    added = sorted(
        (g for gid, g in merged.items() if gid not in old_games and not g.completed),
        key=lambda g: g.date,
    )
    logger.info("  %d games with no result yet would be added, earliest:", len(added))
    for game in added[:5]:
        logger.info(
            "    %s  %-34s %s at %s",
            game.date.strftime("%Y-%m-%d %H:%MZ"),
            game.status,
            game.away,
            game.home,
        )


def _run_with_asyncio(command):
    """
    Let Fire dispatch a coroutine command on Python 3.14.

    Fire runs one by reaching for `asyncio.get_event_loop()`
    (fire 0.7.1, core.py:681). That used to build a loop when there wasn't
    one; on 3.14 it raises `RuntimeError: There is no current event loop`.
    Every command here is async, so without this the CLI can't run any of
    them -- `games` and `odds` included, which is the whole scheduled job.

    `wraps` is what keeps `--help` working: Fire reads the docstring and
    signature off the wrapper, and follows `__wrapped__` to the real
    parameters.
    """

    @wraps(command)
    def run(*args, **kwargs):
        return asyncio.run(command(*args, **kwargs))

    return run


def main():
    commands = {
        "backfill_week_zero": backfill_week_zero,
        "box_scores": box_scores,
        "games": games,
        "odds": odds,
        "plays": plays,
        "preview_unplayed": preview_unplayed,
        "regroup_ncaabb_weeks": regroup_ncaabb_weeks,
    }
    Fire(
        {
            name: _run_with_asyncio(command)
            if iscoroutinefunction(command)
            else command
            for name, command in commands.items()
        }
    )


if __name__ == "__main__":
    main()
