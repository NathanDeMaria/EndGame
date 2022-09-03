from fire import Fire
from endgame.ncaabb import NcaabbGender
from endgame.ncaabb.ncaabb import _get_ncaabb_season
from endgame_aws import save_to_s3, Config


_CONFIG = Config.init_from_file()


async def main(gender_name: str):
    gender = NcaabbGender[gender_name]
    # save_box_scores(gender)
    # save_possessions()
    season = await _get_ncaabb_season(2021, gender)

    # TODO: copy up the cache, too?
    # Or will that get taken care of by EBS volume magic?
    await save_to_s3([season], _CONFIG.bucket, f"seasons/{gender.name}.pkl")


if __name__ == "__main__":
    Fire(main)
