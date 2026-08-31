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

from endgame.async_tools import apply_in_parallel
from endgame.date import get_end_year
from endgame.espn_odds import Odds as EspnOdds
from endgame.football_plays import FootballLeague, get_game_plays
from endgame.ncaabb import NcaabbGender, get_plays_for_day
from endgame.ncaabb.box_score.all import get_season_box_scores
from endgame.ncaabb.matchup import get_possessions, logger
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
    FootballPlaysStore,
    FootballPlaysWeek,
    get_football_plays_store,
    get_pbp_store,
    get_processed_plays_store,
    list_all_keys,
    read_box_scores,
    read_possessions,
    save_csv_to_s3,
    save_data_to_s3,
    save_to_s3,
)
from .io import S3NotFoundException, read_seasons
from .pbp_transform import transform_week_to_table

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
    season = await get_ncaabb_season(year, gender, season_so_far, include_unplayed=True)
    await save_to_s3([season], _CONFIG.bucket, _build_season_key(year, gender))

    # Results, not games. Now that the season carries its fixtures, a
    # schedule with nothing played yet -- the first weeks of November, or a
    # season pulled before it starts -- passes "any games at all" and then
    # falls through to `save_csv_to_s3`, which reads `data[0]` off an empty
    # list of possessions.
    has_results = any(game.completed for week in season.weeks for game in week.games)
    if not has_results:
        logger.warning("No completed games (yet) for %s %d", gender.name, year)
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
#
# Every league carries its fixtures as well as its results, so downstream
# can see what's coming. Readers split the two on `game.completed`, and tell
# scheduled from cancelled on `game.status`.
#
# What that costs differs by how a league is fetched. nfl and ncaafb are
# pulled a week at a time and get their schedule for nothing -- the request
# for a week comes back with its fixtures either way. nhl and wnba are
# walked a day at a time, so they pay one request per future day, bounded by
# `DailyLeague.lookahead_days`.
_GAMES_LEAGUES: dict[str, _GamesLeague] = {
    "nfl": _GamesLeague(
        get_season=lambda year, _so_far: get_nfl_season(year, include_unplayed=True)
    ),
    "ncaafb": _GamesLeague(
        get_season=lambda year, _so_far: get_ncaafb_season(year, include_unplayed=True)
    ),
    "nhl": _GamesLeague(
        get_season=lambda year, so_far: get_nhl_season(
            year, so_far, include_unplayed=True
        ),
        incremental=True,
    ),
    "wnba": _GamesLeague(
        get_season=lambda year, so_far: get_wnba_season(
            year, so_far, include_unplayed=True
        ),
        incremental=True,
    ),
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


async def _load_football_season(league: str, year: int) -> Season:
    """
    The season whose games the play-by-play pull walks.

    Read from the bucket rather than fetched, so a pull is one request per
    game and not a re-fetch of the whole schedule on top. `games` is what
    puts it there, and the daily job runs that first; a season that isn't
    there yet is fetched (and not saved) so a one-off backfill still works
    without having to run `games` by hand first.
    """
    key = f"seasons/{year}/{league}.pkl"
    season = await _load_season(_CONFIG.bucket, key)
    if season is None:
        logger.warning("%s isn't in the bucket -- fetching the season instead", key)
        season = await _GAMES_LEAGUES[league].get_season(year, None)
    return season


async def football_plays(
    league: str,
    year: int,
    week: int | None = None,
    refresh: bool = False,
) -> None:
    """
    Pull play-by-play for a football season, a week per object.

    `league` is nfl or ncaafb. Pass `week` to do a single week, which is the
    way to try this out -- a whole NCAAFB season is ~800 games, and every one
    of them is its own request.

    Incremental by default: a week already in the bucket is topped up with
    whatever games it's missing, and a week with nothing missing costs one
    read and no writes. That's what makes this cheap to run daily during the
    season and what keeps a re-run from re-fetching a finished September.
    Games ESPN has no play-by-play for -- most of the D2/D3 half of an NCAAFB
    week -- are stored with an empty `drives` list, so they're "done" rather
    than retried forever. Pass --refresh=True to re-fetch a week from
    scratch, for when the plays themselves were revised upstream.

    Only finished games are asked for. An unfinished one has partial
    play-by-play at best, and storing that would mark it done at whatever
    score the pull caught it at.

    The week numbers in the keys are the ones `iter_weeks` walks: ESPN's own
    for the NFL, and calendar weeks counted from the start of the season for
    NCAAFB, whose source numbering isn't chronological. They line up with the
    weeks the box score/possession pulls log, not with what a bowl game's
    ESPN url says.
    """
    try:
        football_league = FootballLeague[league]
    except KeyError:
        raise ValueError(
            f"{league!r} isn't a football league; expected one of "
            f"{[member.name for member in FootballLeague]}"
        ) from None

    season = await _load_football_season(league, year)
    async with get_football_plays_store() as store:
        for season_week in iter_weeks(season):
            if week is not None and season_week.number != week:
                continue

            stored = (
                []
                if refresh
                else await _load_football_plays(store, league, year, season_week.number)
            )
            already_pulled = {game["game_id"] for game in stored}
            to_pull = [
                game.game_id
                for game in season_week.games_in_order
                if game.completed and game.game_id not in already_pulled
            ]
            if not to_pull:
                logger.info(
                    "No new games for %s %d week %d (%d already stored)",
                    league,
                    year,
                    season_week.number,
                    len(stored),
                )
                continue

            logger.info(
                "Getting plays for %d games in %s %d week %d",
                len(to_pull),
                league,
                year,
                season_week.number,
            )
            args = [(game_id, football_league) for game_id in to_pull]
            pulled = [
                drives async for drives in apply_in_parallel(get_game_plays, args)
            ]
            games_plays: FootballPlaysWeek = list(stored) + [
                {"game_id": game_id, "drives": drives}
                for game_id, drives in zip(to_pull, pulled, strict=True)
            ]

            await store.save(games_plays, league, year, season_week.number)
            logger.info(
                "Saved %d games (%d plays) for %s %d week %d",
                len(games_plays),
                _count_plays(games_plays),
                league,
                year,
                season_week.number,
            )


def _count_plays(games_plays: FootballPlaysWeek) -> int:
    return sum(len(drive["plays"]) for game in games_plays for drive in game["drives"])


async def _load_football_plays(
    store: FootballPlaysStore, league: str, year: int, week: int
) -> FootballPlaysWeek:
    try:
        return await store.load(league, year, week)
    except S3NotFoundException:
        return []


async def process_football_plays(
    league: str,
    year: int,
    week: int | None = None,
) -> None:
    """
    Turn stored raw play-by-play into the weekly parquet files readers use.

    `league` is nfl or ncaafb. Pass `week` for a single week; otherwise every
    week of the season that has raw plays in the bucket is processed.

    Reads only the bucket -- never ESPN -- so it's safe to re-run, and a
    change to the transform is a re-run of this rather than another season of
    requests. That separation is the point of keeping the raw layer
    untouched: `football_plays` decides what to fetch, this decides what the
    columns mean.

    Each week is rewritten from its raw object rather than appended to, so
    the parquet is always a pure function of what `football_plays` stored.
    The append path (`ProcessedPlaysStore.append_games`) is there for a
    caller that has a batch of games in hand and doesn't want to re-read the
    week's raw object; it isn't what this uses.
    """
    _check_football_league(league)
    processed = get_processed_plays_store()
    written = 0
    async with get_football_plays_store() as raw_store:
        for week_number in _weeks_to_process(week):
            try:
                games = await raw_store.load(league, year, week_number)
            except S3NotFoundException:
                if week is not None:
                    logger.warning(
                        "No raw plays stored for %s %d week %d -- "
                        "run football_plays first",
                        league,
                        year,
                        week_number,
                    )
                continue

            table = transform_week_to_table(games, league, year, week_number)
            if table.num_rows == 0:
                logger.info(
                    "None of the %d stored games for %s %d week %d have plays",
                    len(games),
                    league,
                    year,
                    week_number,
                )
                continue
            await processed.save_week(table, league, year, week_number)
            written += 1

    # Said even when it's zero. A season with no play-by-play yet skips every
    # week quietly, and a scheduled job that logs nothing at all is
    # indistinguishable from one that died before its first line.
    logger.info("Wrote %d week(s) of %s %d play-by-play", written, league, year)


def _weeks_to_process(week: int | None) -> range:
    """
    The weeks to walk. A season's raw objects are keyed by the numbers
    `iter_weeks` walks, which for both football leagues stay inside 1..25
    (NCAAFB counts calendar weeks from the season start and runs into
    January).
    """
    return range(week, week + 1) if week is not None else range(1, 26)


def _check_football_league(league: str) -> None:
    if league not in {member.name for member in FootballLeague}:
        raise ValueError(
            f"{league!r} isn't a football league; expected one of "
            f"{[member.name for member in FootballLeague]}"
        )


async def preview_unplayed(league: str = "ncaafb", year: int | None = None) -> None:
    """
    Report what a fixture-carrying pull does to a league's key, against
    what's in the bucket now.

    Writes nothing. It was the look before the flip in `_GAMES_LEAGUES`,
    which is on for every league now; what it's still good for is checking a
    league after the fact, or after a change to a merge.

    The numbers to read:

    - "completed lost/downgraded" and "duplicate ids" must all be zero.
      Those are the properties a merge is supposed to guarantee, and the
      only ones whose failure is silent.
    - the two shrink-guard lines. `_count_games` counts completed games, so
      the completed-only line is the one the guard actually enforces; the
      every-game line moves for reasons that aren't losses.
    - the status breakdown, which is what invisible-string gets.

    `league` is any key of `_GAMES_LEAGUES` -- nfl, ncaafb, nhl, wnba. A run
    is a full re-fetch, so it's slow on purpose: dozens of requests for the
    week-fetched leagues, a season's worth of days for the daily ones (this
    passes no `season_so_far`, so there's nothing to resume from).

    `year` defaults to the current NCAAFB season, which is the right answer
    for the two football leagues and not for the others -- pass it for nhl
    and wnba rather than trusting the default.
    """
    season_year = year if year is not None else get_end_year(NCAAFB_SEASON_END)
    key = f"seasons/{season_year}/{league}.pkl"

    existing = await _load_season(_CONFIG.bucket, key)
    pulled = await _GAMES_LEAGUES[league].get_season(season_year, None)

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
        "football_plays": football_plays,
        "games": games,
        "odds": odds,
        "plays": plays,
        "process_football_plays": process_football_plays,
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
