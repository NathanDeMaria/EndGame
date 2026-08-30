"""
Play-by-play for football games, from ESPN's game summary API.

The NFL and college football are the same endpoint with a different league
segment in the url, and the plays come back in the same shape, so one
function covers both.

The scoreboard endpoint the seasons are built from doesn't carry plays -- it
has to be one request per game -- so a caller pulling a whole week wants
`endgame.async_tools.apply_in_parallel` around `get_game_plays`.
"""

import json
from enum import Enum
from logging import getLogger
from typing import Dict, List

from .web import get

logger = getLogger(__name__)

_SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/football/{league}/summary"


class FootballLeague(Enum):
    """
    The football leagues ESPN serves play-by-play for.

    The value is how the league is spelled in an ESPN url, which is not what
    this repo calls it -- hence the enum rather than passing the string
    around.
    """

    nfl = "nfl"
    ncaafb = "college-football"


async def get_game_plays(game_id: str, league: FootballLeague) -> List[Dict]:
    """
    A game's completed drives, each with its plays, exactly as ESPN sends
    them.

    Unparsed on purpose, like the ncaabb play-by-play: what's worth pulling
    out of a play isn't settled yet, and re-deciding that later is a rewrite
    of whatever reads these, not another season of requests.

    Comes back empty for a game ESPN has no play-by-play for, which is normal
    rather than exceptional: the D2/D3 games in an NCAAFB week mostly have
    none, and neither do a scattering of older games.

    Only completed drives (`drives.previous`) are returned. A game in
    progress also has a `drives.current`, which is left alone: its plays land
    in `previous` once the drive ends, and taking both means deciding what to
    do about the overlap.

    Goes through `web.get` for its retries and backoff, but deliberately
    never saves to the web cache. A summary response is ~450KB of which the
    drives are a fraction -- box scores, news, win probability and standings
    ride along -- so caching a season of them costs a few hundred MB to save
    requests that the stored play-by-play already stops anyone from making.
    """
    content = await get(_SUMMARY_URL.format(league=league.value), {"event": game_id})
    summary = json.loads(content.data)
    return summary.get("drives", {}).get("previous", [])
