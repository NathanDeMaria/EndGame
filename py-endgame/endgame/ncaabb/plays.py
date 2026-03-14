import datetime
import json
from typing import AsyncIterator, TypedDict

import aiohttp
from bs4 import BeautifulSoup

from endgame.async_tools import apply_in_parallel

from .ncaabb import NcaabbGender, NcaabbGroup, get_ncaabb_games


class _PlayByPlay(TypedDict):
    game_id: str
    plays: list[dict]


async def get_plays_for_day(date: datetime.date, league: NcaabbGender) -> AsyncIterator[_PlayByPlay]:
    for group in NcaabbGroup:
        games = await get_ncaabb_games(date, league, group)
        args = [(game.game_id, league) for game in games]
        plays_list = [p async for p in apply_in_parallel(get_plays, args)]
        for game, plays in zip(games, plays_list, strict=True):
            yield _PlayByPlay(game_id=game.game_id, plays=plays)


async def get_plays(game_id: str, league: NcaabbGender) -> list[dict]:
    async with aiohttp.ClientSession() as session:
        url = f"https://www.espn.com/{league.value}-college-basketball/playbyplay/_/gameId/{game_id}"
        async with session.get(url) as response:
            response.raise_for_status()
            raw = await response.text()
    soup = BeautifulSoup(raw, "html.parser")
    scripts = soup.select("script")
    fit_script = next(script for script in scripts if "espnfitt" in script.text)
    prefix = "window['__espnfitt__']="
    interesting_json = fit_script.text.split(prefix)[-1][:-1]
    data = json.loads(interesting_json)
    # There's other data in here, like page.content.gamepackage.pbp.hasFullPbp
    return data["page"]["content"]["gamepackage"]["pbp"]["plays"]
