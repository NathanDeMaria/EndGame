import pickle
from csv import DictWriter, DictReader
from dataclasses import dataclass
from io import StringIO
from typing import AsyncIterator, Type, TypeVar
from aiobotocore.session import get_session
from dataclasses_json import DataClassJsonMixin
from endgame.ncaabb.ncaabb import Season
from endgame.ncaabb.box_score import PlayerBoxScore
from endgame.ncaabb.possession_side import PossessionSide


class _SerializablePossession(PossessionSide, DataClassJsonMixin):
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
        response = await client.get_object(Bucket=bucket, Key=key)
        async with response["Body"] as stream:
            raw = await stream.read()
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
        response = await client.get_object(Bucket=bucket, Key=key)
        async with response["Body"] as stream:
            raw = await stream.read()
    with StringIO(raw.decode()) as read_stream:
        reader = DictReader(read_stream)
        for item in reader:
            cleaned = {k: None if v == "" else v for k, v in item.items()}
            yield data_class.schema().load(cleaned)


async def save_data_to_s3(bucket: str, key: str, data: bytes):
    session = get_session()
    async with session.create_client("s3") as client:
        await client.put_object(Bucket=bucket, Key=key, Body=data)
