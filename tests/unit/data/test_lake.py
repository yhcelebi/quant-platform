"""Lake: raw immutability, clean appends, revisions and point-in-time (no look-ahead) queries."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from qp.data.lake import CleanStore, Dataset, LakeError, RawStore
from qp.platform.lake import LakePaths

DS = Dataset(
    name="test_series",
    keys=("series",),
    values=("value",),
    fields=(
        pa.field("series", pa.string(), nullable=False),
        pa.field("valid_date", pa.date32(), nullable=False),
        pa.field("value", pa.decimal128(38, 18)),
    ),
)
T0 = datetime(2026, 1, 5, 12, tzinfo=UTC)
D0 = date(2026, 1, 5)


def row(
    value: str,
    *,
    valid: date = D0,
    known: datetime = T0,
    fetched: datetime | None = None,
    basis: str = "schedule",
    series: str = "S",
) -> dict[str, Any]:
    return {
        "series": series,
        "valid_date": valid,
        "value": Decimal(value),
        "source": "test",
        "known_time": known,
        "known_time_basis": basis,
        "fetched_at": fetched or known,
        "raw_ref": "raw/x",
    }


@pytest.fixture
def store(tmp_path: Path) -> CleanStore:
    return CleanStore(LakePaths(tmp_path))


# ---------------------------------------------------------------- raw
def test_raw_is_write_once_and_read_only(tmp_path: Path) -> None:
    raw = RawStore(LakePaths(tmp_path))
    rec = raw.write("tcmb", "fx_daily", "a.xml", b"<x/>", fetched_at=T0, meta={"url": "u"})
    assert rec.ref == "raw/tcmb/fx_daily/fetched_date=2026-01-05/a.xml"
    assert raw.read(rec.ref) == b"<x/>"
    assert raw.meta(rec.ref)["sha256"] == rec.sha256
    with pytest.raises(LakeError, match="immutable"):
        raw.write("tcmb", "fx_daily", "a.xml", b"other", fetched_at=T0, meta={})
    with pytest.raises(PermissionError):
        rec.path.write_bytes(b"tamper")


def test_raw_rejects_bad_names(tmp_path: Path) -> None:
    raw = RawStore(LakePaths(tmp_path))
    for name in ("../x", "A.xml", "x.meta.json"):
        with pytest.raises(LakeError):
            raw.write("tcmb", "fx", name, b"", fetched_at=T0, meta={})


# ---------------------------------------------------------------- clean writes
def test_append_and_query_round_trip(store: CleanStore) -> None:
    assert store.append(DS, [row("1.5"), row("2", valid=D0 + timedelta(days=1))]) == 2
    rows = store.query(DS, as_of=T0 + timedelta(days=5))
    assert [(r["valid_date"], r["value"]) for r in rows] == [
        (D0, Decimal("1.5")),
        (D0 + timedelta(days=1), Decimal("2")),
    ]
    assert rows[0]["known_time"] == T0
    assert rows[0]["known_time"].tzinfo is not None


def test_refetch_of_same_value_writes_nothing(store: CleanStore) -> None:
    store.append(DS, [row("1.5")])
    assert store.append(DS, [row("1.5", fetched=T0 + timedelta(days=2))]) == 0


def test_changed_value_is_a_revision_known_when_fetched(store: CleanStore) -> None:
    store.append(DS, [row("1.5")])
    later = T0 + timedelta(days=2)
    assert store.append(DS, [row("1.6", fetched=later)]) == 1
    before = store.query(DS, as_of=later - timedelta(seconds=1))
    after = store.query(DS, as_of=later)
    assert before[0]["value"] == Decimal("1.5")
    assert after[0]["value"] == Decimal("1.6")
    assert after[0]["known_time_basis"] == "fetched"


def test_conflicting_values_in_one_fetch_are_rejected(store: CleanStore) -> None:
    with pytest.raises(LakeError, match="conflicting values"):
        store.append(DS, [row("1"), row("2", basis="source")])
    assert store.query(DS, as_of=T0) == []  # the whole batch was refused


def test_source_stated_vintages_are_kept(store: CleanStore) -> None:
    v1, v2, v3 = T0, T0 + timedelta(days=30), T0 + timedelta(days=60)
    later = T0 + timedelta(days=90)
    vintages = [
        row("100", known=v1, fetched=later, basis="source"),
        row("101", known=v2, fetched=later, basis="source"),
        row("100", known=v3, fetched=later, basis="source"),  # revised back
    ]
    assert store.append(DS, vintages) == 3
    assert store.append(DS, vintages) == 0  # refetch: nothing new
    for at, expected in ((v1, "100"), (v2, "101"), (v3, "100")):
        assert store.query(DS, as_of=at)[0]["value"] == Decimal(expected)
    assert store.query(DS, as_of=v3, observed_only=True) == []  # qp saw them only later


def test_rows_are_validated(store: CleanStore) -> None:
    naive = row("1")
    naive["known_time"] = datetime(2026, 1, 5, 12)  # noqa: DTZ001 - the bug being tested
    with pytest.raises(LakeError, match="tz-aware"):
        store.append(DS, [naive])
    with pytest.raises(LakeError, match="after fetched_at"):
        store.append(DS, [row("1", known=T0, fetched=T0 - timedelta(hours=1))])
    with pytest.raises(LakeError, match="columns differ"):
        store.append(DS, [{**row("1"), "extra": 1}])
    with pytest.raises(ValueError, match="nope"):
        store.append(DS, [row("1", basis="nope")])
    assert store.query(DS, as_of=T0) == []  # nothing was written


def test_latest_on_picks_last_valid_date_known(store: CleanStore) -> None:
    store.append(
        DS,
        [
            row("1", valid=D0),
            row("2", valid=D0 + timedelta(days=1), known=T0 + timedelta(days=1)),
            row("9", valid=D0, series="OTHER"),
        ],
    )
    hit = store.latest_on(DS, {"series": "S"}, D0 + timedelta(days=3), as_of=T0 + timedelta(days=9))
    assert hit is not None
    assert hit["value"] == Decimal("2")
    early = store.latest_on(DS, {"series": "S"}, D0 + timedelta(days=3), as_of=T0)
    assert early is not None
    assert early["value"] == Decimal("1")  # the day-2 value was not known yet
    assert store.latest_on(DS, {"series": "S"}, D0 - timedelta(days=1), as_of=T0) is None
    with pytest.raises(LakeError, match="key columns"):
        store.latest_on(DS, {}, D0, as_of=T0)


def test_empty_dataset_queries(store: CleanStore) -> None:
    assert store.query(DS, as_of=T0) == []
    assert store.stats(DS) is None


# ---------------------------------------------------------------- leak property
facts = st.lists(
    st.tuples(
        st.integers(0, 4),  # valid day offset
        st.integers(0, 20),  # known hour offset (relative to the valid day)
        st.integers(0, 30),  # extra fetch delay hours
        st.integers(0, 3),  # value
        st.sampled_from(["schedule", "source", "fetched"]),
    ),
    min_size=1,
    max_size=25,
)


@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(batches=st.lists(facts, min_size=1, max_size=3), probe_hours=st.integers(-5, 200))
def test_property_query_never_returns_unknown_facts(
    tmp_path_factory: pytest.TempPathFactory,
    batches: list[list[tuple[int, int, int, int, str]]],
    probe_hours: int,
) -> None:
    store = CleanStore(LakePaths(tmp_path_factory.mktemp("lake")))
    for batch in batches:
        rows = []
        for vday, khour, delay, value, basis in batch:
            known = T0 + timedelta(days=vday, hours=khour)
            rows.append(
                row(
                    str(value),
                    valid=D0 + timedelta(days=vday),
                    known=known,
                    fetched=known + timedelta(hours=delay),
                    basis=basis,
                )
            )
        try:
            store.append(DS, rows)
        except LakeError as exc:  # same series/date/instant, different values: all rejected
            if "conflicting values" not in str(exc):
                raise
    stored = store._select(DS, "SELECT * FROM src", [])
    as_of = T0 + timedelta(hours=probe_hours)
    for observed_only in (False, True):
        result = store.query(DS, as_of=as_of, observed_only=observed_only)

        def visible(r: dict[str, Any], strict: bool = observed_only) -> bool:
            return r["known_time"] <= as_of and (not strict or r["fetched_at"] <= as_of)

        # brute-force model: per valid date, the latest stored revision visible at as_of
        expected: dict[date, dict[str, Any]] = {}
        for r in stored:
            if not visible(r):
                continue
            best = expected.get(r["valid_date"])
            if best is None or (r["known_time"], r["fetched_at"]) > (
                best["known_time"],
                best["fetched_at"],
            ):
                expected[r["valid_date"]] = r
        assert all(visible(r) for r in result)  # no look-ahead, ever
        assert {r["valid_date"]: r["value"] for r in result} == {
            d: r["value"] for d, r in expected.items()
        }
