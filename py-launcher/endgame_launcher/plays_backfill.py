import asyncio
from datetime import date, timedelta
from typing import AsyncIterator, Iterator, NamedTuple

from aiobotocore.session import get_session
from batch_core import Config, JobDefinition


class _Params(NamedTuple):
    league: str
    day: str


async def _find_covered_parameters(bucket: str) -> AsyncIterator[_Params]:
    """
    List all files under config.bucket/plays/ncaabb/ using aiobotocore.
    """
    session = get_session()
    prefix = "plays/ncaabb/"
    async with session.create_client('s3') as client:
        paginator = client.get_paginator('list_objects_v2')
        async for page in paginator.paginate(Bucket=bucket, Prefix=prefix): # type: ignore
            for obj in page.get('Contents', []):
                league, filename = obj['Key'].removeprefix(prefix).split("/")
                day = filename.removesuffix(".json")
                yield _Params(league=league, day=day)


def _current_season() -> int:
    current_date = date.today()
    return current_date.year - 1 if current_date.month < 11 else current_date.year

def _list_days(start_year: int) -> Iterator[str]:
    # Determine start date for NCAABB season (typically November 1 to April 1)
    # Season starts in November of the previous year if current month < November
    start_date = date(start_year, 11, 1)
    end_date = min(
        date(start_year + 1, 4, 1),
        date.today() - timedelta(days=1),
    )
    current_day = end_date
    while current_day >= start_date:
        yield current_day.isoformat()
        current_day -= timedelta(days=1)


def _build_params() -> Iterator[_Params]:
    current_season = _current_season()
    for league in ["mens", "womens"]:
        # "Seasons" I have back to 2010
        # but I think the first pbp+shot chart is 2013, 2014 for womens? I'll try both
        for season in range(2013, current_season + 1):
            for day in _list_days(season):
                yield _Params(league=league, day=day)


async def _main() -> None:
    config = Config.load()
    covered_parameters = {p async for p in _find_covered_parameters(config.bucket)}
    params = [p for p in _build_params() if p not in covered_parameters]
    print(f"Backlog has {len(covered_parameters)}, need {len(params)} new runs")

    session = get_session()
    async with session.create_client('batch') as client:
        definition = await JobDefinition.create_from_image(client, config.repo_urls['endgame'])

        sem = asyncio.Semaphore(10)

        async def _run_single_job(param: _Params) -> tuple[bool, _Params]:
            async with sem:
                job = await definition.run("plays", **param._asdict())
                result = await job.wait()
                return result, param

        job_tasks = [_run_single_job(param) for param in params]
        results = await asyncio.gather(*job_tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, BaseException):
                print(f"Error: {result}")
            else:
                status, param = result
                print(param, status)


if __name__ == "__main__":
    asyncio.run(_main())
