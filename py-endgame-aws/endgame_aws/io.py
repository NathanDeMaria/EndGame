import pickle
from csv import DictWriter, DictReader
from io import StringIO
from aiobotocore.session import get_session
from dataclasses_json import DataClassJsonMixin
from endgame.ncaabb.ncaabb import Season
from endgame.ncaabb.possession_side import PossessionSide


class _SerializablePossession(PossessionSide, DataClassJsonMixin):
    pass


async def save_to_s3(seasons: list[Season], bucket: str, key: str):
    """
    Save these seasons to a pickle in S3
    """
    dumped = pickle.dumps(seasons)
    session = get_session()
    async with session.create_client("s3") as client:
        await client.put_object(Bucket=bucket, Key=key, Body=dumped)


async def save_csv_to_s3(data: list[dict], bucket: str, key: str):
    with StringIO() as stream:
        writer = DictWriter(stream, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
        body = stream.getvalue()

    session = get_session()
    async with session.create_client("s3") as client:
        await client.put_object(Bucket=bucket, Key=key, Body=body)


async def read_seasons(bucket: str, key: str) -> list[Season]:
    session = get_session()
    async with session.create_client("s3") as client:
        response = await client.get_object(Bucket=bucket, Key=key)
        async with response["Body"] as stream:
            raw = await stream.read()
    return pickle.loads(raw)


async def read_possessions(bucket: str, key: str) -> list[PossessionSide]:
    session = get_session()
    async with session.create_client("s3") as client:
        response = await client.get_object(Bucket=bucket, Key=key)
        async with response["Body"] as stream:
            raw = await stream.read()
    with StringIO(raw.decode()) as read_stream:
        reader = DictReader(read_stream)
        return [_SerializablePossession.schema().load(row) for row in reader]
