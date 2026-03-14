from endgame.ncaabb import NcaabbGender

from .stores import get_pbp_store


async def test_pbp_store_list() -> None:
    n_test_days = 2
    tested_days = 0
    async with get_pbp_store() as store:
        async for pbp_day in store.load_all(NcaabbGender.mens):
            assert isinstance(pbp_day, list)
            if len(pbp_day) > 0 and any(len(pbp["plays"]) for pbp in pbp_day):
                tested_days += 1
            if tested_days >= n_test_days:
                break
        else:
            raise AssertionError(
                f"Expected to find at least {n_test_days} days with real pbp data, "
                f"but only found {tested_days}"
            )
