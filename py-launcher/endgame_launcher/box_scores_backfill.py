"""
Re-pull every stored NCAABB season's box scores, keeping each game's summary.

One Batch job per league-season, each `refresh_box_scores {league}
{season}`: every finished game's summary is fetched again and stored whole
under summaries/ncaabb/, and each game it has a box score for is rewritten
in the season's box score file -- with `starter` and `position`, which the
box scores stored before the summary didn't have. A game ESPN no longer has
a box score for keeps the rows stored for it. The season and its
possessions aren't touched. ~6,000 requests a season; a season that fails
is redone whole by running it again.

Stops at the season before this one: the current season is the daily
`box_scores` job's, and running both on it would race on the same file.
"""

import asyncio
from datetime import date

from aiobotocore.session import get_session
from batch_core import Config, JobDefinition

# endgame's first NCAABB season. ESPN's summary has the box score (with
# starters and positions) and the plays (with player ids) that far back --
# further than the play-by-play stored under plays/, which starts in 2013-14.
_FIRST_SEASON = 2010


def _current_season() -> int:
    today = date.today()
    return today.year if today.month >= 7 else today.year - 1


async def _main(first: int = _FIRST_SEASON, last: int | None = None) -> None:
    last = last if last is not None else _current_season() - 1
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
                job = await definition.run("refresh_box_scores", league, str(season))
                return league, season, await job.wait()

        results = await asyncio.gather(
            *(_run(league, season) for league, season in params),
            return_exceptions=True,
        )
        for result in results:
            print(result)


if __name__ == "__main__":
    asyncio.run(_main())
