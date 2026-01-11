import json
from fire import Fire
from datetime import date, datetime
from zoneinfo import ZoneInfo
from endgame.ncaabb import NcaabbGender
from endgame.ncaabb.possession_side import PossessionSide
from endgame.ncaabb.ncaabb import get_ncaabb_season, get_ncaabb_spreads, Season
from endgame.ncaabb.matchup import logger, apply_in_parallel, get_possessions
from endgame.ncaabb.box_score.all import get_season_box_scores
from endgame_aws import (
    save_to_s3,
    save_csv_to_s3,
    Config,
    FlattenedBoxScore,
    save_data_to_s3,
    read_possessions,
    read_box_scores,
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

    rows_so_far = await _load_possessions(_CONFIG.bucket, year, gender)
    rows: list[dict] = [r.to_dict() for r in rows_so_far]
    pulled_game_ids = {side.game_id for side in rows_so_far}
    for week in season.weeks:
        logger.info("Getting matchups for %d %d", season.year, week.number)
        args = [
            (gender, game.game_id)
            for game in week.games
            if game.game_id not in pulled_game_ids
        ]
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


async def odds(day: str | None = None):
    parsed_date = (
        datetime.fromisoformat(day).date()
        if day is not None
        else datetime.now(tz=ZoneInfo("America/Chicago")).date()
    )
    odds = [o async for o in get_ncaabb_spreads(parsed_date)]
    await save_data_to_s3(
        _CONFIG.bucket, f"odds/ncaabb/{parsed_date}.json", json.dumps(odds).encode()
    )


if __name__ == "__main__":
    Fire({"box_scores": box_scores, "odds": odds})
