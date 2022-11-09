from fire import Fire
from endgame.ncaabb import NcaabbGender
from endgame.ncaabb.ncaabb import _get_ncaabb_season
from endgame.ncaabb.matchup import logger, apply_in_parallel, get_possessions
from endgame_aws import save_to_s3, save_csv_to_s3, Config


_CONFIG = Config.init_from_file()


async def main(gender_name: str, year: int):
    gender = NcaabbGender[gender_name]
    season = await _get_ncaabb_season(year, gender)

    await save_to_s3([season], _CONFIG.bucket, f"seasons/{year}/{gender.name}.pkl")

    rows: list[dict] = []
    for week in season.weeks:
        logger.info("Getting matchups for %d %d", season.year, week.number)
        args = [(gender, game.game_id) for game in week.games]
        games = apply_in_parallel(get_possessions, args)
        async for sides in games:
            if sides is None:
                continue
            rows.extend(side.to_dict() for side in sides)
    await save_csv_to_s3(rows, _CONFIG.bucket, f"seasons/{year}/{gender.name}.csv")


if __name__ == "__main__":
    Fire(main)
