import pickle
import json
import asyncio
from csv import DictWriter, DictReader
from dataclasses import dataclass
from io import StringIO
from typing import AsyncIterator, Type, TypeVar
from aiobotocore.session import get_session
from botocore.exceptions import ClientError
from dataclasses_json import DataClassJsonMixin
from endgame.ncaabb.ncaabb import Season
from endgame.ncaabb.box_score import PlayerBoxScore
from endgame.ncaabb.possession_side import PossessionSide


class _SerializablePossession(PossessionSide, DataClassJsonMixin):  # type: ignore[misc]
    # Ignore mypy because this double-defines to_dict, but it's unused anyway
    pass


@dataclass
class FlattenedBoxScore(PlayerBoxScore, DataClassJsonMixin):
    game_id: str
    team_id: str


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
        raw = await _read_from_s3(bucket, key, client)
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
        raw = await _read_from_s3(bucket, key, client)
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


async def _read_from_s3(bucket: str, key: str, client) -> bytes:
    try:
        response = await client.get_object(Bucket=bucket, Key=key)
    except ClientError as ex:
        if ex.response['Error']['Code'] == 'NoSuchKey':
            raise S3NotFoundException from ex
        else:
            raise
    async with response["Body"] as stream:
        return await stream.read()


async def _list_keys(bucket: str, prefix: str, client) -> AsyncIterator[str]:
    paginator = client.get_paginator("list_objects_v2")
    async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            yield obj["Key"]


async def read_all_odds(bucket: str, prefix: str) -> AsyncIterator[dict]:
    session = get_session()
    async with session.create_client("s3") as client:
        odds_keys = _list_keys(bucket, prefix, client)
        tasks = [
            _read_from_s3(bucket, key, client) async for key in odds_keys
        ]
        bodies = await asyncio.gather(*tasks)
        for body in bodies:
            parsed = json.loads(body.decode())
            for o in parsed:
                yield o
