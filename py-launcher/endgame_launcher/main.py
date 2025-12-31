from batch_core import JobDefinition, Config
from aiobotocore.session import get_session
import asyncio


def _build_params() -> list[tuple[str, int]]:
    params = []
    for gender in ["mens", "womens"]:
        # TODO: actually figure out a good first year
        params.extend((gender, year) for year in range(2010, 2026))
    return params


async def _main() -> None:
    config = Config.load()

    current_params = _build_params()
    params = []
    session = get_session()
    async with session.create_client('batch') as client:
        # TODO: probably don't need to create this every time
        definition = await JobDefinition.create_from_image(client, config.repo_urls['endgame'])

        job_tasks = ((definition.run("box_scores", gender_name=gender, year=year) for gender, year in current_params))
        jobs = await asyncio.gather(*job_tasks)

        # Wait for all jobs in parallel
        results = await asyncio.gather(
            *(job.wait() for job in jobs),
            return_exceptions=True
        )
        
        # Process results
        for result, (gender, year) in zip(results, current_params):
            if isinstance(result, Exception):
                print(f"Error waiting for job {gender} {year}: {result}")
                params.append((gender, year))
            else:
                print(gender, year, result)
                if not result:
                    params.append((gender, year))



if __name__ == "__main__":
    asyncio.run(_main())
