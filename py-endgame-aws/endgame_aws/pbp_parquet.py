"""
The processed (parquet) layer for football play-by-play.

The raw layer -- `get_football_plays_store`, at `plays/{league}/{year}/{week}.json.gz`
-- stays exactly as ESPN sent it and is never edited; everything here is
derived from it and can be thrown away and rebuilt. What this adds is a
layout a reader can actually query: one parquet object per league-week, rows
sorted by `game_id`, at

    processed/plays/league={league}/season={season}/week={week}/data.parquet

Hive-style directories rather than the flat `plays/{league}/{year}/{week}`
the raw layer uses, so that the tree opens as one pyarrow dataset with
`league`, `season` and `week` readable off the path -- which is what any
other reader pointed at the prefix will expect.

The two access patterns this is built around:

- one game at a time, inside a Batch container. `load_single_game` pushes the
  `game_id` filter into the parquet reader rather than filtering after the
  read: on a real NCAAFB week (123 games, 927 KB) that moves ~150 KB instead
  of the whole file.
- a range of weeks, for a historical query -- `load_weeks`.

Both build their paths from the season and week they were asked for, so
neither ever lists the tree. The partition directories are the layout, not
the lookup.
"""

import asyncio
from logging import getLogger
from typing import Any, Iterable, Mapping, Sequence

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.fs as fs
import pyarrow.parquet as pq

from .config import Config
from .io import S3NotFoundException, save_data_to_s3
from .pbp_transform import PLAY_SCHEMA, transform_week_to_table

logger = getLogger(__name__)

_PREFIX = "processed/plays"

# The size that made the single-game read cheapest, measured on a real
# NCAAFB week (2025 week 4: 123 games, 21,677 plays):
#
#   row group   file      median bytes read for one game
#         512   1.24 MB   258 KB (21%)
#        1024   1.03 MB   199 KB (19%)
#        2048   0.93 MB   150 KB (16%)
#        4096   0.86 MB   227 KB (26%)
#
# Both directions get worse for different reasons. Bigger row groups mean a
# game shares one with more of the week, so the reader fetches games nobody
# asked for. Smaller ones grow the footer -- min/max per column per row group,
# and there are 38 columns -- and every read pays the whole footer before it
# can skip anything.
#
# The default (1M rows) is a week in one row group, which prunes nothing: the
# filter would still be correct, it would just download the whole week to
# apply it.
_ROW_GROUP_SIZE = 2048

# Declared rather than inferred. `ds.dataset(..., partitioning="hive")` reads
# `season=2025` as an int32, which collides with the int16 the same column
# has inside the file (the partition values are repeated as real columns so a
# table read from one partition still says what it is). Spelling the types
# out here makes the two agree.
PLAYS_PARTITIONING = ds.partitioning(
    pa.schema(
        [
            PLAY_SCHEMA.field("league"),
            PLAY_SCHEMA.field("season"),
            PLAY_SCHEMA.field("week"),
        ]
    ),
    flavor="hive",
)


def build_week_prefix(league: str, season: int, week: int) -> str:
    # Zero-padded to match the raw layer's keys, and so a listing sorts the
    # way the season runs.
    return f"{_PREFIX}/league={league}/season={season}/week={week:02d}"


def build_week_key(league: str, season: int, week: int) -> str:
    return f"{build_week_prefix(league, season, week)}/data.parquet"


class ProcessedPlaysStore:
    """
    Read and write the weekly parquet objects.

    Writes go out through the same aiobotocore path as everything else in
    this package: a week is one object, so a write is one PUT, which S3
    makes atomic -- a reader either sees the whole new week or the whole old
    one, and a run that dies mid-write leaves the previous week intact. That
    is the reason for the single object per week rather than
    `ds.write_dataset` into the partition directory: write_dataset's
    `existing_data_behavior` has to delete the old fragments before writing
    the new ones, and a failure in that window leaves the partition empty.

    Reads go through `pyarrow.fs.S3FileSystem` instead, because that is what
    turns a filter into byte-range requests. Both are sync C++, so they run
    in a thread.
    """

    def __init__(self, bucket: str) -> None:
        self._bucket = bucket
        self._filesystem: fs.S3FileSystem | None = None

    @property
    def filesystem(self) -> fs.S3FileSystem:
        """
        Built on first use and kept: constructing one resolves the bucket's
        region, which is a request, and doing that per read would double the
        cost of the small reads this exists to make cheap.
        """
        if self._filesystem is None:
            self._filesystem = fs.S3FileSystem(
                # Batch runs on spot, where a connection dying mid-read is
                # ordinary. The default strategy gives up after 3 tries.
                retry_strategy=fs.AwsDefaultS3RetryStrategy(max_attempts=5),
            )
        return self._filesystem

    def _path(self, league: str, season: int, week: int) -> str:
        return f"{self._bucket}/{build_week_key(league, season, week)}"

    async def save_week(self, table: pa.Table, league: str, season: int, week: int):
        """
        Replace a week's parquet with `table`.

        Refuses to write an empty table: an empty week is what a bug that
        drops rows looks like, and overwriting a good week with zero rows is
        the one mistake this layer can't recover from without a reprocess.
        Callers with genuinely nothing to write should not call this.
        """
        if table.num_rows == 0:
            raise ValueError(
                f"Refusing to write an empty {league} {season} week {week}; "
                "nothing to save"
            )
        key = build_week_key(league, season, week)
        await save_data_to_s3(self._bucket, key, _serialize(table))
        logger.info(
            "Wrote %d plays from %d games to %s",
            table.num_rows,
            _count_games(table),
            key,
        )

    async def load_week(self, league: str, season: int, week: int) -> pa.Table:
        """
        A whole week. Raises `S3NotFoundException` if it hasn't been
        processed yet.
        """
        return await asyncio.to_thread(self._read_week, league, season, week)

    async def load_week_or_empty(self, league: str, season: int, week: int) -> pa.Table:
        """
        A whole week, or an empty table with the right schema if there isn't
        one yet -- which is the normal state of the first run against a week,
        not an error.
        """
        try:
            return await self.load_week(league, season, week)
        except S3NotFoundException:
            return PLAY_SCHEMA.empty_table()

    async def load_single_game(
        self, league: str, season: int, week: int, game_id: str
    ) -> pa.Table:
        """
        One game's plays, without downloading the rest of its week.

        The filter is handed to the parquet reader rather than applied after
        the fact. Because `transform_week_to_table` sorts the week by
        `game_id` and the file is written in small row groups, the reader
        compares the filter against each row group's min/max `game_id` in the
        footer and fetches only the byte ranges that can match -- measured at
        ~150 KB of a 927 KB NCAAFB week, against 927 KB for an unfiltered
        read of the same file.

        Comes back empty (with the full schema) for a game that isn't in the
        week, which includes every game ESPN had no play-by-play for.
        """
        return await asyncio.to_thread(
            self._read_single_game, league, season, week, game_id
        )

    async def load_weeks(
        self, league: str, season: int, weeks: Iterable[int]
    ) -> pa.Table:
        """
        Several weeks of one season as a single table -- the "games nearby in
        time" read.

        Opens exactly the weeks asked for, so the weeks in between cost
        nothing whether or not they exist. Weeks that haven't been processed
        are skipped rather than raising: asking for weeks 1-18 of a season in
        progress is a normal thing to do.
        """
        return await asyncio.to_thread(self._read_weeks, league, season, list(weeks))

    async def append_games(
        self,
        games: Sequence[Mapping[str, Any]],
        league: str,
        season: int,
        week: int,
    ) -> pa.Table:
        """
        Transform a batch of raw games and merge them into their week.

        `games` is the raw shape -- `[{"game_id": ..., "drives": [...]}, ...]`.
        A game already in the week is replaced by the incoming version rather
        than appended to, so re-running a day (or re-processing after ESPN
        revised a game) converges instead of duplicating. Returns the merged
        week.

        An empty batch writes nothing and returns the week as it stands: a
        day with no finished games is a normal day, not a failure.
        """
        if not games:
            logger.info("No games to process for %s %d week %d", league, season, week)
            return await self.load_week_or_empty(league, season, week)

        incoming = transform_week_to_table(games, league, season, week)
        existing = await self.load_week_or_empty(league, season, week)
        merged = merge_weeks(existing, incoming)
        if merged.num_rows == 0:
            # Every game in the batch had no play-by-play (the D2/D3 half of
            # an NCAAFB week) and the week had nothing before. There is
            # nothing to write, and writing an empty object would only make
            # the next run think the week exists.
            logger.info(
                "None of the %d games for %s %d week %d have plays; nothing written",
                len(games),
                league,
                season,
                week,
            )
            return merged
        await self.save_week(merged, league, season, week)
        return merged

    def _read_week(self, league: str, season: int, week: int) -> pa.Table:
        dataset = self._open(self._path(league, season, week))
        return conform_to_schema(dataset.to_table())

    def _read_single_game(
        self, league: str, season: int, week: int, game_id: str
    ) -> pa.Table:
        try:
            dataset = self._open(self._path(league, season, week))
        except S3NotFoundException:
            return PLAY_SCHEMA.empty_table()
        return conform_to_schema(
            dataset.to_table(filter=ds.field("game_id") == game_id)
        )

    def _read_weeks(self, league: str, season: int, weeks: Sequence[int]) -> pa.Table:
        paths = []
        for week in weeks:
            path = self._path(league, season, week)
            if self.filesystem.get_file_info(path).type == fs.FileType.NotFound:
                logger.info("%s hasn't been processed; skipping", path)
                continue
            paths.append(path)
        if not paths:
            return PLAY_SCHEMA.empty_table()
        dataset = ds.dataset(
            paths,
            filesystem=self.filesystem,
            format="parquet",
            partitioning=PLAYS_PARTITIONING,
        )
        return conform_to_schema(dataset.to_table())

    def _open(self, path: str) -> ds.Dataset:
        """
        A dataset over one week's file.

        Pointed at the file, not at the tree above it: the season and week
        are already known at every call site, so there is nothing to discover
        and no reason to pay a LIST for it.

        Translates the miss into this package's `S3NotFoundException` so a
        caller handles a missing week the same way whichever store it came
        from.
        """
        try:
            return ds.dataset(
                path,
                filesystem=self.filesystem,
                format="parquet",
                partitioning=PLAYS_PARTITIONING,
            )
        except FileNotFoundError as ex:
            raise S3NotFoundException(path) from ex


def conform_to_schema(table: pa.Table) -> pa.Table:
    """
    A table read out of the bucket, put back into `PLAY_SCHEMA`.

    Everything that reads a stored week goes through this, so a caller never
    has to think about which version of the code wrote the file it got. A
    column added to the schema since the file was written comes back all
    null; one since removed is dropped; column order is the schema's.

    Without it, the append path breaks the first time the schema moves:
    `merge_weeks` concatenates a stored week with a freshly transformed one,
    and Arrow refuses to concatenate tables whose schemas differ at all --
    including in ways nobody would call a difference, like the ms-vs-s
    timestamp precision parquet imposes on write.
    """
    if table.schema.equals(PLAY_SCHEMA):
        return table
    columns = [
        table.column(field.name).cast(field.type)
        if field.name in table.column_names
        else pa.nulls(table.num_rows, field.type)
        for field in PLAY_SCHEMA
    ]
    return pa.Table.from_arrays(columns, schema=PLAY_SCHEMA)


def merge_weeks(existing: pa.Table, incoming: pa.Table) -> pa.Table:
    """
    A week's stored plays with `incoming`'s games layered over them.

    Games in both are taken from `incoming` -- whole games, not row by row --
    so a re-pull of a game whose play count changed doesn't leave orphaned
    rows from the old version behind. The result is sorted by game, which is
    what the single-game read depends on.
    """
    if existing.num_rows == 0:
        return incoming.sort_by(
            [("game_id", "ascending"), ("play_number", "ascending")]
        )
    incoming_games = incoming.column("game_id").unique()
    kept = existing.filter(~ds.field("game_id").isin(incoming_games))
    combined = pa.concat_tables([kept, incoming])
    return combined.sort_by([("game_id", "ascending"), ("play_number", "ascending")])


def _serialize(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(
        table,
        sink,
        compression="zstd",
        row_group_size=_ROW_GROUP_SIZE,
        # No page index. It looks like it should help -- page-level min/max
        # inside the row group the read does have to touch -- but measured on
        # the same week it made a single-game read *bigger* (208 KB against
        # 199 KB): the reader doesn't prune pages for this filter, and the
        # index itself lands in the footer that every read pays for.
    )
    return sink.getvalue().to_pybytes()


def _count_games(table: pa.Table) -> int:
    if table.num_rows == 0:
        return 0
    return len(table.column("game_id").unique())


def get_processed_plays_store() -> ProcessedPlaysStore:
    return ProcessedPlaysStore(Config.init_from_file().bucket)
