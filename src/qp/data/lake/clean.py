"""Clean zone: typed, point-in-time Parquet rows, queried with DuckDB (ADR 0007).

Every row carries the point-in-time columns

    source            where the fact comes from
    <valid column>    the date the fact describes (``valid_date`` for daily series)
    known_time        when it became available (UTC)
    known_time_basis  how known_time was determined (:class:`KnownTimeBasis`)
    fetched_at        when qp obtained it (UTC); known_time <= fetched_at always
    raw_ref           the raw file it was parsed from

Rows are only ever appended, as new files. A re-fetch that repeats what is already known
writes nothing; a changed value becomes a revision row. Queries return, per series and
valid date, the latest revision with ``known_time <= as_of`` and never anything later.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from qp.core import ensure_utc
from qp.data.lake.raw import LakeError
from qp.platform.lake import LakePaths

_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")
Row = dict[str, Any]


class KnownTimeBasis(StrEnum):
    SCHEDULE = "schedule"  # the source's documented publication time (capped at fetched_at)
    SOURCE = "source"  # the source states it (e.g. an ALFRED vintage date)
    FETCHED = "fetched"  # first seen by qp at fetched_at (also every observed revision)


PIT_FIELDS = (
    pa.field("source", pa.string(), nullable=False),
    pa.field("known_time", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("known_time_basis", pa.string(), nullable=False),
    pa.field("fetched_at", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("raw_ref", pa.string(), nullable=False),
)
PIT_COLUMNS = tuple(f.name for f in PIT_FIELDS)


@dataclass(frozen=True)
class Dataset:
    """A clean dataset: ``keys`` identify a series, ``valid`` is the date a row describes,
    ``values`` are compared to detect revisions."""

    name: str
    keys: tuple[str, ...]
    values: tuple[str, ...]
    fields: tuple[pa.Field, ...]
    valid: str = "valid_date"

    def __post_init__(self) -> None:
        names = [f.name for f in self.fields]
        for n in (self.name, *names):
            if not _IDENT.fullmatch(n):
                raise LakeError(f"invalid identifier {n!r}")
        missing = {*self.keys, *self.values, self.valid} - set(names)
        if missing or set(names) & set(PIT_COLUMNS):
            raise LakeError(f"dataset {self.name}: bad columns (missing {sorted(missing)})")

    @property
    def schema(self) -> pa.Schema:
        return pa.schema([*self.fields, *PIT_FIELDS])

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(self.schema.names)


def _validate(ds: Dataset, row: Mapping[str, Any]) -> Row:
    if set(row) != set(ds.columns):
        raise LakeError(
            f"{ds.name}: row columns differ: missing {sorted(set(ds.columns) - set(row))}, "
            f"extra {sorted(set(row) - set(ds.columns))}"
        )
    out = dict(row)
    for name in ("known_time", "fetched_at"):
        value = out[name]
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise LakeError(f"{ds.name}: {name} must be a tz-aware datetime, got {value!r}")
        out[name] = ensure_utc(value)
    if out["known_time"] > out["fetched_at"]:
        raise LakeError(f"{ds.name}: known_time is after fetched_at")
    KnownTimeBasis(out["known_time_basis"])
    if not isinstance(out[ds.valid], date) or isinstance(out[ds.valid], datetime):
        raise LakeError(f"{ds.name}: {ds.valid} must be a date")
    return out


class CleanStore:
    def __init__(self, paths: LakePaths) -> None:
        self.paths = paths

    def _dir(self, ds: Dataset) -> Path:
        return self.paths.clean(ds.name)

    def _files(self, ds: Dataset) -> list[str]:
        folder = self._dir(ds)
        return sorted(str(p) for p in folder.glob("*.parquet")) if folder.is_dir() else []

    # ------------------------------------------------------------------ write
    def append(self, ds: Dataset, rows: Iterable[Mapping[str, Any]]) -> int:
        """Append new facts; returns how many rows were written.

        A row whose values equal what was already known for its series and valid date at its
        known_time is dropped. When a value already exists, anything new from a schedule or
        a plain fetch is a revision learned now: known_time = fetched_at (basis 'fetched').
        """
        incoming = [_validate(ds, r) for r in rows]
        if not incoming:
            return 0
        history: dict[tuple[Any, ...], list[Row]] = defaultdict(list)
        for r in self._existing(ds, incoming):
            history[self._ident(ds, r)].append(r)
        accepted: list[Row] = []
        for row in incoming:
            prior_rows = history[self._ident(ds, row)]
            basis = KnownTimeBasis(row["known_time_basis"])
            if prior_rows and basis is not KnownTimeBasis.SOURCE:
                row = {**row, "known_time": row["fetched_at"], "known_time_basis": "fetched"}
            elif any(p["known_time"] == row["known_time"] for p in prior_rows):
                same = [p for p in prior_rows if p["known_time"] == row["known_time"]]
                if all(self._vals(ds, p) == self._vals(ds, row) for p in same):
                    continue
                row = {**row, "known_time": row["fetched_at"], "known_time_basis": "fetched"}
            ties = [
                p
                for p in prior_rows
                if (p["known_time"], p["fetched_at"]) == (row["known_time"], row["fetched_at"])
            ]
            if any(self._vals(ds, p) != self._vals(ds, row) for p in ties):
                # Two values for one series and date, known and fetched at the same instant:
                # "latest" would be undefined. A source payload like that is rejected whole.
                raise LakeError(
                    f"{ds.name}: conflicting values for {self._ident(ds, row)} "
                    f"known at {row['known_time']} in one fetch"
                )
            prior = self._latest(prior_rows, row["known_time"])
            if prior is not None and self._vals(ds, prior) == self._vals(ds, row):
                continue
            prior_rows.append(row)
            accepted.append(row)
        if accepted:
            self._write(ds, accepted)
        return len(accepted)

    @staticmethod
    def _ident(ds: Dataset, row: Mapping[str, Any]) -> tuple[Any, ...]:
        return tuple(row[k] for k in (*ds.keys, ds.valid))

    @staticmethod
    def _vals(ds: Dataset, row: Mapping[str, Any]) -> tuple[Any, ...]:
        return tuple(row[v] for v in ds.values)

    @staticmethod
    def _latest(rows: Sequence[Row], known_at: datetime) -> Row | None:
        known = [r for r in rows if r["known_time"] <= known_at]
        return max(known, key=lambda r: (r["known_time"], r["fetched_at"]), default=None)

    def _existing(self, ds: Dataset, rows: Sequence[Row]) -> list[Row]:
        valid = [r[ds.valid] for r in rows]
        return self._select(
            ds,
            f"SELECT * FROM src WHERE {ds.valid} BETWEEN ? AND ?",  # noqa: S608
            [min(valid), max(valid)],
        )

    def _write(self, ds: Dataset, rows: list[Row]) -> None:
        folder = self._dir(ds)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        final = folder / f"part-{stamp}-{uuid4().hex[:8]}.parquet"
        tmp = folder / f".{final.name}.tmp"
        table = pa.Table.from_pylist(rows, schema=ds.schema)
        pq.write_table(table, tmp)
        os.rename(tmp, final)  # readers glob *.parquet: they never see a partial file

    # ------------------------------------------------------------------ read
    def _select(self, ds: Dataset, sql: str, params: Sequence[Any]) -> list[Row]:
        files = self._files(ds)
        if not files:
            return []
        con = duckdb.connect()
        try:
            con.execute("SET TimeZone = 'UTC'")
            sql = f"WITH src AS (SELECT * FROM read_parquet(?, union_by_name = true)) {sql}"  # noqa: S608
            rows: list[Row] = con.execute(sql, [files, *params]).to_arrow_table().to_pylist()
        finally:
            con.close()
        return rows

    def query(
        self,
        ds: Dataset,
        *,
        as_of: datetime,
        where: Mapping[str, Any] | None = None,
        valid_from: date | None = None,
        valid_to: date | None = None,
        observed_only: bool = False,
    ) -> list[Row]:
        """Per series and valid date, the latest revision known at ``as_of``.

        ``observed_only`` also requires ``fetched_at <= as_of``: only what qp itself had
        seen by then, ignoring schedule-based or source-stated publication times.
        """
        as_of = ensure_utc(as_of)
        conds = ["known_time <= ?::TIMESTAMPTZ"]
        params: list[Any] = [as_of.isoformat()]
        if observed_only:
            conds.append("fetched_at <= ?::TIMESTAMPTZ")
            params.append(as_of.isoformat())
        for column, value in (where or {}).items():
            if column not in ds.columns:
                raise LakeError(f"{ds.name}: unknown column {column!r}")
            conds.append(f"{column} = ?")
            params.append(value)
        if valid_from is not None:
            conds.append(f"{ds.valid} >= ?")
            params.append(valid_from)
        if valid_to is not None:
            conds.append(f"{ds.valid} <= ?")
            params.append(valid_to)
        partition = ", ".join((*ds.keys, ds.valid))
        sql = (
            "SELECT * EXCLUDE (rn) FROM ("  # noqa: S608 - identifiers validated by Dataset
            f"SELECT *, row_number() OVER (PARTITION BY {partition} "
            "ORDER BY known_time DESC, fetched_at DESC) AS rn "
            f"FROM src WHERE {' AND '.join(conds)}) WHERE rn = 1 "
            f"ORDER BY {partition}"
        )
        return self._select(ds, sql, params)

    def latest_on(
        self,
        ds: Dataset,
        series: Mapping[str, Any],
        on: date,
        *,
        as_of: datetime,
        observed_only: bool = False,
    ) -> Row | None:
        """The row with the greatest valid date <= ``on`` among facts known at ``as_of``."""
        if set(series) != set(ds.keys):
            raise LakeError(f"{ds.name}: give exactly the key columns {list(ds.keys)}")
        rows = self.query(ds, as_of=as_of, where=series, valid_to=on, observed_only=observed_only)
        return max(rows, key=lambda r: r[ds.valid], default=None)

    def max_known_time(self, ds: Dataset) -> datetime | None:
        """The latest ``known_time`` stored: what a result computed now could have seen."""
        rows = self._select(ds, "SELECT max(known_time) AS k FROM src", [])
        value = rows[0]["k"] if rows else None
        return value if isinstance(value, datetime) else None

    def stats(self, ds: Dataset) -> Row | None:
        """Row count, valid-date range and last fetch, for status displays."""
        rows = self._select(
            ds,
            f"SELECT count(*) AS rows, min({ds.valid}) AS first_valid, "  # noqa: S608
            f"max({ds.valid}) AS last_valid, max(fetched_at) AS last_fetched FROM src",
            [],
        )
        return rows[0] if rows else None
