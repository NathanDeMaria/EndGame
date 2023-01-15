from fire import Fire
from endgame.ncaabb import NcaabbGender

from endgame.ncaabb.ncaabb import get_ncaabb_season
from endgame.ncaabb.matchup import logger, apply_in_parallel, get_possessions
from endgame.ncaabb.box_score.all import _get_season_box_scores
from endgame_aws import save_to_s3, save_csv_to_s3, Config, FlattenedBoxScore


_CONFIG = Config.init_from_file()


async def main(gender_name: str, year: int):
    gender = NcaabbGender[gender_name]
    season = await get_ncaabb_season(year, gender)

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

    box_score_rows: list[dict] = []
    async for box_score in _get_season_box_scores(season, gender):
        box_score_rows.extend(
            FlattenedBoxScore(
                **player.to_dict(),
                team_id=box_score.home.team_id,
                game_id=box_score.game_id,
            ).to_dict()
            for player in box_score.home.players
        )
        box_score_rows.extend(
            FlattenedBoxScore(
                **player.to_dict(),
                team_id=box_score.away.team_id,
                game_id=box_score.game_id,
            ).to_dict()
            for player in box_score.away.players
        )

    await save_csv_to_s3(
        box_score_rows, _CONFIG.bucket, f"seasons/{year}/{gender.name}_box.csv"
    )


if __name__ == "__main__":
    Fire(main)
