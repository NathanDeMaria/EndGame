import datetime
import gzip
import json
from contextlib import asynccontextmanager
from typing import (
    Any,
    AsyncContextManager,
    AsyncIterator,
    Callable,
    Mapping,
    Sequence,
    TypeVar,
)

from aiobotocore.session import get_session
from endgame.ncaabb import NcaabbGender

from .config import Config
from .io import list_keys, read_from_s3, save_data_to_s3


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


class _WeeklyStore[_StoreType]:
    """
    Objects keyed by league, season and week.

    The football counterpart to `_DatedStore`: the leagues that play a week
    at a time are pulled and thought about by week, and a week is also about
    the right amount of play-by-play to keep in one object -- see
    `get_football_plays_store`.
    """

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

    async def save(self, value: _StoreType, league: str, year: int, week: int) -> None:
        await save_data_to_s3(
            self._bucket,
            self._build_key(league, year, week),
            self._serializer(value),
        )

    async def load(self, league: str, year: int, week: int) -> _StoreType:
        data = await read_from_s3(
            self._bucket,
            self._build_key(league, year, week),
            self._client,
        )
        return self._deserializer(data)

    async def load_all(self, league: str, year: int) -> AsyncIterator[_StoreType]:
        prefix = self._build_prefix(league, year)
        async for key in list_keys(self._bucket, prefix, self._client):
            data = await read_from_s3(self._bucket, key, self._client)
            yield self._deserializer(data)

    def _build_prefix(self, league: str, year: int) -> str:
        return f"{self._prefix}/{league}/{year}"

    def _build_key(self, league: str, year: int, week: int) -> str:
        # Zero-padded so a listing sorts the way a season runs: week 10 lands
        # after week 9 rather than between weeks 1 and 2.
        return f"{self._build_prefix(league, year)}/{week:02d}.{self._extension}"


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


@asynccontextmanager
async def _get_weekly_store(
    prefix: str,
    serializer: Callable[[_StoreType], bytes],
    deserializer: Callable[[bytes], _StoreType],
    extension: str,
) -> AsyncIterator[_WeeklyStore[_StoreType]]:
    session = get_session()
    async with session.create_client("s3") as client:
        yield _WeeklyStore[_StoreType](
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


# What one object holds: every game of one league's week, as
# `{"game_id": ..., "drives": [...]}`, with the drives exactly as ESPN sent
# them.
FootballPlaysWeek = Sequence[Mapping[str, Any]]
# The store itself, named so a caller can annotate one without reaching for
# the private class.
FootballPlaysStore = _WeeklyStore[FootballPlaysWeek]


def get_football_plays_store() -> AsyncContextManager[FootballPlaysStore]:
    """
    Where football play-by-play lands: one object per league, season and
    week.

    A week is the middle ground between the two obvious layouts. One object
    per game means ~800 tiny objects for an NCAAFB season, and any read of it
    pays a request per game; one object per season means a run that adds a
    single game rewrites tens of megabytes. A week is a few hundred games at
    the widest, it's the unit the seasons are already fetched and merged in,
    and a re-run only rewrites the weeks it actually touched.

    Gzipped because this is raw ESPN JSON -- deeply nested, every key spelled
    out on every play, team logo urls repeated per drive -- which is about a
    tenth of the size compressed.
    """
    return _get_weekly_store(
        "plays",
        lambda d: gzip.compress(json.dumps(d).encode()),
        lambda b: json.loads(gzip.decompress(b).decode()),
        "json.gz",
    )
