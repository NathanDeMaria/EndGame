import pickle
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
