"""
The processed (parquet) layer for NCAABB play-by-play.

The raw layer -- `get_pbp_store`, at `plays/ncaabb/{league}/{day}.json` --
stays as scraped; everything here is derived from it and can be rebuilt. One
parquet object per league-day, rows sorted by `game_id`, at

    processed/basketball_plays/league={league}/season={season}/date={day}/data.parquet

A day rather than football's week because a day is what the raw layer
stores and what the daily pull adds, so a run rewrites exactly the objects
whose raw day changed. A day is also small: the busiest, opening day 2025,
is 169 games and 82,000 plays, 2.1 MB of parquet against 85 MB of raw JSON.

Its own prefix rather than a league under football's `processed/plays`: the
two schemas share almost nothing, and that tree has to open as one dataset.
"""

import asyncio
from datetime import date, datetime
from logging import getLogger
from typing import Iterable, Sequence

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.fs as fs

from .basketball_transform import BASKETBALL_PLAY_SCHEMA
from .config import Config
from .io import S3NotFoundException, save_data_to_s3
from .pbp_parquet import _serialize

logger = getLogger(__name__)

_PREFIX = "processed/basketball_plays"

# Declared for the same reason as football's `PLAYS_PARTITIONING`: inferred,
# `season=2025` reads as int32 and `date=2026-01-21` as a string, and neither
# matches the column of the same name inside the file.
BASKETBALL_PLAYS_PARTITIONING = ds.partitioning(
    pa.schema(
        [
            BASKETBALL_PLAY_SCHEMA.field("league"),
            BASKETBALL_PLAY_SCHEMA.field("season"),
            BASKETBALL_PLAY_SCHEMA.field("date"),
        ]
    ),
    flavor="hive",
)


def build_season_prefix(league: str, season: int) -> str:
    return f"{_PREFIX}/league={league}/season={season}"


def build_day_key(league: str, season: int, day: date) -> str:
    return f"{build_season_prefix(league, season)}/date={day.isoformat()}/data.parquet"


def season_of(day: date) -> int:
    """
    The season a day belongs to, named for the year it starts in: November
    2025 through April 2026 is 2025. Split in the summer, when nothing is
    played.
    """
    return day.year if day.month >= 7 else day.year - 1


class ProcessedBasketballPlaysStore:
    """
    Read and write the daily parquet objects.

    Writes are one PUT per day, through the same path as football's
    `ProcessedPlaysStore` and for the same reason: S3 makes a single PUT
    atomic, so a reader sees the whole new day or the whole old one. Reads
    and listings go through `pyarrow.fs.S3FileSystem`, in a thread.
    """

    def __init__(self, bucket: str) -> None:
        self._bucket = bucket
        self._filesystem: fs.S3FileSystem | None = None

    @property
    def filesystem(self) -> fs.S3FileSystem:
        if self._filesystem is None:
            self._filesystem = fs.S3FileSystem(
                retry_strategy=fs.AwsDefaultS3RetryStrategy(max_attempts=5),
            )
        return self._filesystem

    async def save_day(self, table: pa.Table, league: str, day: date) -> None:
        """
        Replace a day's parquet with `table`.

        Refuses an empty table, like `ProcessedPlaysStore.save_week`: zero
        rows is what a bug that drops plays looks like, and a day with no
        games is one a caller shouldn't be writing at all.
        """
        if table.num_rows == 0:
            raise ValueError(f"Refusing to write an empty {league} {day}")
        key = build_day_key(league, season_of(day), day)
        await save_data_to_s3(self._bucket, key, _serialize(table))
        logger.info(
            "Wrote %d plays from %d games to %s",
            table.num_rows,
            len(table.column("game_id").unique()),
            key,
        )

    async def load_day(self, league: str, day: date) -> pa.Table:
        """One day. Raises `S3NotFoundException` if it hasn't been processed."""
        return await asyncio.to_thread(self._read, [self._path(league, day)])

    async def load_days(self, league: str, days: Iterable[date]) -> pa.Table:
        """
        Several days as one table. Days that haven't been processed -- most
        of them days nobody played -- are skipped rather than raising.
        """
        return await asyncio.to_thread(self._read_existing, league, list(days))

    async def load_season(self, league: str, season: int) -> pa.Table:
        """Every processed day of a season."""
        days = await self.processed_days(league, season)
        return await self.load_days(league, sorted(days))

    async def processed_days(self, league: str, season: int) -> dict[date, datetime]:
        """
        Each processed day of a season, with when it was written -- which is
        what `process_basketball_plays` compares against the raw day's to
        decide whether the day needs doing again.
        """
        return await asyncio.to_thread(self._list_season, league, season)

    def _path(self, league: str, day: date) -> str:
        return f"{self._bucket}/{build_day_key(league, season_of(day), day)}"

    def _list_season(self, league: str, season: int) -> dict[date, datetime]:
        selector = fs.FileSelector(
            f"{self._bucket}/{build_season_prefix(league, season)}",
            recursive=True,
            allow_not_found=True,
        )
        days = {}
        for info in self.filesystem.get_file_info(selector):
            if info.type != fs.FileType.File or not info.path.endswith("/data.parquet"):
                continue
            partition = info.path.split("/")[-2]
            days[date.fromisoformat(partition.removeprefix("date="))] = info.mtime
        return days

    def _read_existing(self, league: str, days: Sequence[date]) -> pa.Table:
        paths = [
            path
            for path in (self._path(league, day) for day in days)
            if self.filesystem.get_file_info(path).type != fs.FileType.NotFound
        ]
        if not paths:
            return BASKETBALL_PLAY_SCHEMA.empty_table()
        return self._read(paths)

    def _read(self, paths: Sequence[str]) -> pa.Table:
        try:
            dataset = ds.dataset(
                list(paths),
                filesystem=self.filesystem,
                format="parquet",
                partitioning=BASKETBALL_PLAYS_PARTITIONING,
            )
        except FileNotFoundError as ex:
            raise S3NotFoundException(paths) from ex
        return conform_to_basketball_schema(dataset.to_table())


def conform_to_basketball_schema(table: pa.Table) -> pa.Table:
    """
    A stored table put back into `BASKETBALL_PLAY_SCHEMA`: a column added
    since the file was written comes back null, one removed is dropped, and
    the order is the schema's. See `pbp_parquet.conform_to_schema`.
    """
    if table.schema.equals(BASKETBALL_PLAY_SCHEMA):
        return table
    columns = [
        table.column(field.name).cast(field.type)
        if field.name in table.column_names
        else pa.nulls(table.num_rows, field.type)
        for field in BASKETBALL_PLAY_SCHEMA
    ]
    return pa.Table.from_arrays(columns, schema=BASKETBALL_PLAY_SCHEMA)


def get_processed_basketball_plays_store() -> ProcessedBasketballPlaysStore:
    return ProcessedBasketballPlaysStore(Config.init_from_file().bucket)
