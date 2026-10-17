"""
Re-pull every stored NCAABB season's box scores, keeping each game's summary.

One Batch job per league-season, each `box_scores {league} {season}
--refresh_box_scores`: every finished game's summary is fetched again,
stored whole under summaries/ncaabb/, and the season's box score file is
rewritten from them -- with `starter` and `position`, which the box scores
stored before the summary didn't have. ~6,000 requests a season; a season
that fails is redone whole by running it again.
"""

import asyncio
from datetime import date

from aiobotocore.session import get_session
from batch_core import Config, JobDefinition

# The first season with box scores worth pulling: the play-by-play starts in
# 2013-14 too.
_FIRST_SEASON = 2013


def _current_season() -> int:
    today = date.today()
    return today.year if today.month >= 7 else today.year - 1


async def _main(first: int = _FIRST_SEASON, last: int | None = None) -> None:
    last = last if last is not None else _current_season()
    params = [
        (league, season)
        for league in ("mens", "womens")
        for season in range(first, last + 1)
    ]
    print(f"Launching {len(params)} box score seasons")

    session = get_session()
    async with session.create_client("batch") as client:
        config = Config.load()
        definition = await JobDefinition.create_from_image(
            client, config.repo_urls["endgame"]
        )
        sem = asyncio.Semaphore(6)

        async def _run(league: str, season: int) -> tuple[str, int, bool]:
            async with sem:
                job = await definition.run(
                    "box_scores", league, str(season), refresh_box_scores="True"
                )
                return league, season, await job.wait()

        results = await asyncio.gather(
            *(_run(league, season) for league, season in params),
            return_exceptions=True,
        )
        for result in results:
            print(result)


if __name__ == "__main__":
    asyncio.run(_main())
