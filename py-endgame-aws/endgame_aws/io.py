import pickle
from csv import DictWriter
from io import StringIO
from aiobotocore.session import get_session
from endgame.ncaabb.ncaabb import Season


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
