import datetime
import json
from typing import (
    Any,
    AsyncContextManager,
    AsyncIterator,
    Callable,
    Mapping,
    Sequence,
    TypeVar,
)
from contextlib import asynccontextmanager

from aiobotocore.session import get_session
from endgame.ncaabb import NcaabbGender

from .io import list_keys, save_data_to_s3, read_from_s3
from .config import Config


class _DatedStore[_StoreType]:
    def __init__(
        self,
        client,
        bucket: str,
        prefix: str,
        serializer: Callable[[_StoreType], bytes],
        deserializer: Callable[[bytes], _StoreType],
        extension: str,
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._prefix = prefix
        self._serializer = serializer
        self._deserializer = deserializer
        self._extension = extension

    async def save(
        self, value: _StoreType, date: datetime.date, league: NcaabbGender
    ) -> None:
        await save_data_to_s3(
            self._bucket,
            self._build_key(date, league),
            self._serializer(value),
        )

    async def load(self, date: datetime.date, league: NcaabbGender) -> _StoreType:
        data = await read_from_s3(
            self._bucket,
            self._build_key(date, league),
            self._client,
        )
        return self._deserializer(data)

    async def load_all(self, league: NcaabbGender) -> AsyncIterator[_StoreType]:
        prefix = self._build_prefix(league)
        async for key in list_keys(self._bucket, prefix, self._client):
            data = await read_from_s3(self._bucket, key, self._client)
            yield self._deserializer(data)

    def _build_prefix(self, league: NcaabbGender) -> str:
        return f"{self._prefix}/{league.name}"

    def _build_key(self, date: datetime.date, league: NcaabbGender) -> str:
        return f"{self._build_prefix(league)}/{date.isoformat()}.{self._extension}"


_StoreType = TypeVar("_StoreType")


@asynccontextmanager
async def _get_store(
    prefix: str,
    serializer: Callable[[_StoreType], bytes],
    deserializer: Callable[[bytes], _StoreType],
    extension: str,
) -> AsyncIterator[_DatedStore[_StoreType]]:
    session = get_session()
    async with session.create_client("s3") as client:
        yield _DatedStore[_StoreType](
            client,
            Config.init_from_file().bucket,
            prefix,
            serializer,
            deserializer,
            extension,
        )


def get_pbp_store() -> AsyncContextManager[_DatedStore[Sequence[Mapping[str, Any]]]]:
    return _get_store(
        "plays/ncaabb",
        lambda d: json.dumps(d).encode(),
        lambda b: json.loads(b.decode()),
        "json",
    )
