import asyncio
import json
import pickle
from csv import DictReader, DictWriter
from dataclasses import dataclass
from io import StringIO
from typing import Any, AsyncIterator, Type, TypeVar

from aiobotocore.session import get_session
from botocore.exceptions import ClientError
from dataclasses_json import DataClassJsonMixin
from endgame.ncaabb.box_score import PlayerBoxScore
from endgame.ncaabb.ncaabb import Season
from endgame.ncaabb.possession_side import PossessionSide


class _SerializablePossession(PossessionSide, DataClassJsonMixin):
    # Both bases define `to_dict`, with signatures neither a type checker nor
    # the MRO can reconcile: `PossessionSide.to_dict()` takes no arguments and
    # returns `dict[str, Primitive]`, while `DataClassJsonMixin.to_dict()`
    # takes `encode_json` and returns `dict[str, Json]`. Spell out an override
    # that both bases accept -- it delegates to the `PossessionSide` one, which
    # is what the MRO already picked, so this is only making the resolution
    # explicit. This class is only used for its `schema()`, anyway.
    def to_dict(self, encode_json: bool = False) -> dict[str, Any]:
        return PossessionSide.to_dict(self)


@dataclass
class FlattenedBoxScore(PlayerBoxScore, DataClassJsonMixin):
    game_id: str
    team_id: str

    @classmethod
    def from_player(
        cls, player: PlayerBoxScore, *, game_id: str, team_id: str
    ) -> "FlattenedBoxScore":
        """
        Build a flattened row from a player's box score plus the ids of the
        game and team it came from.

        Copies the fields out one by one rather than splatting
        `**player.to_dict()`: the dict is typed as `dict[str, Json]`, so
        splatting it hides every field from the type checker. Written out, a
        new field on `PlayerBoxScore` shows up here as a missing argument.
        """
        return cls(
            player_id=player.player_id,
            short_name=player.short_name,
            minutes_played=player.minutes_played,
            field_goal_makes=player.field_goal_makes,
            field_goal_attempts=player.field_goal_attempts,
            three_point_makes=player.three_point_makes,
            three_point_attempts=player.three_point_attempts,
            free_throw_makes=player.free_throw_makes,
            free_throw_attempts=player.free_throw_attempts,
            offensive_rebounds=player.offensive_rebounds,
            defensive_rebounds=player.defensive_rebounds,
            rebounds=player.rebounds,
            assists=player.assists,
            steals=player.steals,
            blocks=player.blocks,
            turnovers=player.turnovers,
            fouls=player.fouls,
            points=player.points,
            game_id=game_id,
            team_id=team_id,
        )


async def save_to_s3(seasons: list[Season], bucket: str, key: str):
    """
    Save these seasons to a pickle in S3
    """
    dumped = pickle.dumps(seasons)
    await save_data_to_s3(bucket, key, dumped)


async def save_csv_to_s3(data: list[dict], bucket: str, key: str):
    with StringIO() as stream:
        writer = DictWriter(stream, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
        body = stream.getvalue()
    await save_data_to_s3(bucket, key, body.encode())


async def read_seasons(bucket: str, key: str) -> list[Season]:
    session = get_session()
    async with session.create_client("s3") as client:
        raw = await read_from_s3(bucket, key, client)
    return pickle.loads(raw)


async def read_possessions(bucket: str, key: str) -> list[PossessionSide]:
    return [
        possession
        async for possession in _read_csv(bucket, key, _SerializablePossession)
    ]


async def read_box_scores(bucket: str, key: str) -> list[FlattenedBoxScore]:
    return [box async for box in _read_csv(bucket, key, FlattenedBoxScore)]


_DataclassJsonType = TypeVar("_DataclassJsonType", bound=DataClassJsonMixin)


async def _read_csv(
    bucket: str, key: str, data_class: Type[_DataclassJsonType]
) -> AsyncIterator[_DataclassJsonType]:
    session = get_session()
    async with session.create_client("s3") as client:
        raw = await read_from_s3(bucket, key, client)
    with StringIO(raw.decode()) as read_stream:
        reader = DictReader(read_stream)
        for item in reader:
            cleaned = {k: None if v == "" else v for k, v in item.items()}
            yield data_class.schema().load(cleaned)


async def save_data_to_s3(bucket: str, key: str, data: bytes):
    session = get_session()
    async with session.create_client("s3") as client:
        await client.put_object(Bucket=bucket, Key=key, Body=data)


class S3NotFoundException(Exception):
    pass


async def read_from_s3(bucket: str, key: str, client) -> bytes:
    try:
        response = await client.get_object(Bucket=bucket, Key=key)
    except ClientError as ex:
        if ex.response["Error"]["Code"] == "NoSuchKey":
            raise S3NotFoundException from ex
        else:
            raise
    async with response["Body"] as stream:
        return await stream.read()


async def list_keys(bucket: str, prefix: str, client) -> AsyncIterator[str]:
    paginator = client.get_paginator("list_objects_v2")
    async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            yield obj["Key"]


async def list_all_keys(bucket: str, prefix: str) -> AsyncIterator[str]:
    """
    Same as list_keys, but creates its own client so callers outside this
    package don't need to reach into aiobotocore themselves.
    """
    session = get_session()
    async with session.create_client("s3") as client:
        async for key in list_keys(bucket, prefix, client):
            yield key


async def read_all_odds(bucket: str, prefix: str) -> AsyncIterator[dict]:
    session = get_session()
    async with session.create_client("s3") as client:
        odds_keys = list_keys(bucket, prefix, client)
        tasks = [read_from_s3(bucket, key, client) async for key in odds_keys]
        bodies = await asyncio.gather(*tasks)
        for body in bodies:
            parsed = json.loads(body.decode())
            for o in parsed:
                yield o
