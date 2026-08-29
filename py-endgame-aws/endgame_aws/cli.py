import json
import re
from dataclasses import dataclass
from datetime import date, datetime
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
from endgame.types import group_games_into_weeks, iter_weeks, merge_weekly_seasons
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
        args = [
            (gender, game.game_id)
            for game in week.games
            if game.game_id not in pulled_game_ids
        ]
        if not args:
            logger.info("No new matchups for %d %d", season.year, week.number)
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
    "ncaafb": _GamesLeague(get_season=lambda year, _so_far: get_ncaafb_season(year)),
    "nhl": _GamesLeague(get_season=get_nhl_season, incremental=True),
    "wnba": _GamesLeague(get_season=get_wnba_season, incremental=True),
}


def _count_games(season: Season | None) -> int:
    if season is None:
        return 0
    return sum(len(week.games) for week in season.weeks)


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
    """
    last = last_year if last_year is not None else get_end_year(NCAAFB_SEASON_END)
    for year in range(first_year, last + 1):
        key = f"seasons/{year}/ncaafb.pkl"
        existing = await _load_season(_CONFIG.bucket, key)
        if existing is None:
            logger.warning("%s is not in the bucket; skipping", key)
            continue

        season = merge_weekly_seasons([existing, await get_ncaafb_season(year)])
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


def main():
    Fire(
        {
            "backfill_week_zero": backfill_week_zero,
            "box_scores": box_scores,
            "games": games,
            "odds": odds,
            "plays": plays,
            "regroup_ncaabb_weeks": regroup_ncaabb_weeks,
        }
    )


if __name__ == "__main__":
    main()
