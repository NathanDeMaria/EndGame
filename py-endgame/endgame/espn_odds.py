import json
from typing import AsyncIterator, TypedDict

from .web import RequestParameters, get


class Odds(TypedDict):
    competition_id: str
    odds: dict


async def get_odds(url: str, parameters: RequestParameters) -> AsyncIterator[Odds]:
    content = await get(url, parameters)
    tree = json.loads(content.data)
    events = tree["events"]
    for event in events:
        assert len(event["competitions"]) == 1
        competition = event["competitions"][0]
        odds = competition.get("odds")
        if not odds:
            continue
        competition_id = competition["id"]
        yield Odds(competition_id=competition_id, odds=odds)
