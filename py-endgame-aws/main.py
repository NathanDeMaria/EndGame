import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import AsyncIterator, Awaitable, Callable
from zoneinfo import ZoneInfo

from endgame.espn_odds import Odds as EspnOdds
from endgame.ncaabb import NcaabbGender, get_plays_for_day
from endgame.ncaabb.box_score.all import get_season_box_scores
from endgame.ncaabb.matchup import apply_in_parallel, get_possessions, logger
from endgame.ncaabb.ncaabb import Season, get_ncaabb_season, get_ncaabb_spreads
from endgame.ncaabb.possession_side import PossessionSide
from endgame.ncaafb import get_current_odds as get_ncaafb_current_odds
from endgame.ncaafb import get_season as get_ncaafb_season
from endgame.nfl.games import get_current_odds as get_nfl_current_odds
from endgame.nfl.games import get_season as get_nfl_season
from fire import Fire

from endgame_aws import (
    Config,
    FlattenedBoxScore,
    get_pbp_store,
    read_box_scores,
    read_possessions,
    save_csv_to_s3,
    save_data_to_s3,
    save_to_s3,
)
from endgame_aws.io import S3NotFoundException, read_seasons

_CONFIG = Config.init_from_file()


def _build_season_key(year: int, gender: NcaabbGender) -> str:
    return f"seasons/{year}/{gender.name}.pkl"


def _build_possession_key(year: int, gender: NcaabbGender) -> str:
    return f"seasons/{year}/{gender.name}.csv"


def _build_box_score_key(year: int, gender: NcaabbGender) -> str:
    return f"seasons/{year}/{gender.name}_box.csv"


async def _load_season(bucket: str, year: int, gender: NcaabbGender) -> Season | None:
    try:
        seasons = await read_seasons(bucket, _build_season_key(year, gender))
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
    season_so_far = await _load_season(_CONFIG.bucket, year, gender)
    season = await get_ncaabb_season(year, gender, season_so_far)
    await save_to_s3([season], _CONFIG.bucket, _build_season_key(year, gender))

    has_games = any(game for week in season.weeks for game in week.games)
    if not has_games:
        logger.warning("No games found (yet) for %s %d", gender.name, year)

    rows_so_far = await _load_possessions(_CONFIG.bucket, year, gender)
    rows: list[dict] = [r.to_dict() for r in rows_so_far]
    pulled_game_ids = {side.game_id for side in rows_so_far}
    for week in season.weeks:
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
            FlattenedBoxScore(
                **player.to_dict(),  # type: ignore[arg-type]
                team_id=box_score.home.team_id,
                game_id=box_score.game_id,
            ).to_dict()
            for player in box_score.home.players
        )
        box_score_rows.extend(
            FlattenedBoxScore(
                **player.to_dict(),  # type: ignore[arg-type]
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


# Leagues whose games are a single "get the season, save it" pull.
# ncaabb isn't here: its `box_scores` command also pulls possessions/box
# scores, so it stays a separate, bigger pipeline.
_GAMES_LEAGUES: dict[str, Callable[[int], Awaitable[Season]]] = {
    "nfl": get_nfl_season,
    "ncaafb": get_ncaafb_season,
}


async def games(league: str, year: int) -> None:
    season = await _GAMES_LEAGUES[league](year)
    await save_to_s3([season], _CONFIG.bucket, f"seasons/{year}/{league}.pkl")


def _parse_date(date_str: str | None) -> date:
    if date_str is None:
        return datetime.now(tz=ZoneInfo("America/Chicago")).date()
    return datetime.fromisoformat(date_str).date()


@dataclass
class _OddsLeague:
    # `day` is only meaningful for ncaabb, which is scheduled by day rather
    # than by week; nfl/ncaafb just return whatever ESPN calls "this week".
    get_odds: Callable[[date], AsyncIterator[EspnOdds]]


_ODDS_LEAGUES: dict[str, _OddsLeague] = {
    "ncaabb": _OddsLeague(get_odds=get_ncaabb_spreads),
    "nfl": _OddsLeague(get_odds=lambda _day: get_nfl_current_odds()),
    "ncaafb": _OddsLeague(get_odds=lambda _day: get_ncaafb_current_odds()),
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


async def plays(league: str, day: str | None = None) -> None:
    parsed_date = _parse_date(day)
    pbps = get_plays_for_day(parsed_date, NcaabbGender[league])
    all_plays = [plays async for plays in pbps]
    async with get_pbp_store() as store:
        await store.save(all_plays, parsed_date, NcaabbGender[league])
    print(f"Saved pbp for {len(all_plays)} games for {league} on {parsed_date}.")


if __name__ == "__main__":
    Fire(
        {
            "box_scores": box_scores,
            "games": games,
            "odds": odds,
            "plays": plays,
        }
    )
