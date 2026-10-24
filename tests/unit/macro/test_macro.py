"""Macro transforms, registry, vintages and curves (ADR 0012), by hand."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from qp.data.series import ManualSeries, SeriesPoint
from qp.macro.curves import US_TREASURY, curve
from qp.macro.registry import RegistryError, indicator_history, load_registry
from qp.macro.transforms import (
    Frequency,
    Transform,
    annualized,
    apply,
    change,
    pct_change,
    rebased,
    rolling_mean,
)
from qp.macro.vintages import revisions

REPO = Path(__file__).resolve().parents[3]
LATE = datetime(2030, 1, 1, tzinfo=UTC)


def monthly(values: list[float], start: date = date(2024, 1, 1)) -> list[tuple[date, float]]:
    out = []
    y, m = start.year, start.month
    for v in values:
        out.append((date(y, m, 1), v))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def same(got: list[tuple[date, float]], want: list[tuple[date, float]]) -> bool:
    return [d for d, _ in got] == [d for d, _ in want] and all(
        abs(a - b) < 1e-12 for (_, a), (_, b) in zip(got, want, strict=True)
    )


def test_pct_change_mom_yoy_and_gaps() -> None:
    obs = monthly([100 + i for i in range(14)])  # Jan 2024 .. Feb 2025
    mom = pct_change(obs, 1, Frequency.MONTHLY)
    assert same(mom[:1], [(date(2024, 2, 1), 0.01)])
    yoy = apply(Transform.YOY, obs, Frequency.MONTHLY)
    assert same(yoy, [(date(2025, 1, 1), 112 / 100 - 1), (date(2025, 2, 1), 113 / 101 - 1)])
    gappy = [o for o in obs if o[0] != date(2024, 6, 1)]  # May -> Jul: no June
    assert date(2024, 7, 1) not in {d for d, _ in pct_change(gappy, 1, Frequency.MONTHLY)}


def test_change_annualized_rolling_rebased() -> None:
    obs = monthly([100.0, 101.0, 102.01, 103.0301])  # +1% a month
    assert change(obs, 1, Frequency.MONTHLY)[0][1] == pytest.approx(1.0)
    # 3-month change 3.0301% at an annual rate: 1.01^12 - 1
    assert same(annualized(obs, 3, Frequency.MONTHLY), [(date(2024, 4, 1), 1.01**12 - 1)])
    assert same(rolling_mean(obs, 2)[:1], [(date(2024, 2, 1), 100.5)])
    assert rebased(obs, date(2024, 2, 15))[0][1] == pytest.approx(100 / 101 * 100)
    daily = [(date(2025, 1, 1) + timedelta(days=i), float(i + 1)) for i in range(5)]
    assert same(pct_change(daily, 2, Frequency.DAILY)[:1], [(date(2025, 1, 3), 2.0)])


@given(
    st.lists(
        st.tuples(st.integers(0, 23), st.integers(1, 500), st.integers(0, 60)),
        min_size=1,
        max_size=40,
    ),
    st.integers(0, 60),
)
def test_transforms_never_see_later_facts(facts: list[tuple[int, int, int]], cutoff: int) -> None:
    """Values computed as of t are identical whatever is published after t."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    seen: set[tuple[int, int]] = set()
    points = []
    for month, value, known in facts:
        if (month, known) in seen:
            continue
        seen.add((month, known))
        d = monthly([0.0] * (month + 1))[-1][0]
        points.append(SeriesPoint("S", d, Decimal(value), "t", t0 + timedelta(days=known)))
    as_of = t0 + timedelta(days=cutoff)
    early = [p for p in points if p.known_time <= as_of]
    for transform in Transform:
        full = apply(transform, _obs(ManualSeries(points), as_of), Frequency.MONTHLY)
        only_known = apply(transform, _obs(ManualSeries(early), as_of), Frequency.MONTHLY)
        assert full == only_known


def _obs(s: ManualSeries, as_of: datetime) -> list[tuple[date, float]]:
    return [(p.valid_date, float(p.value)) for p in s.history("S", date(2030, 1, 1), as_of=as_of)]


def test_registry_loads_and_rejects_duplicates(tmp_path: Path) -> None:
    reg = load_registry(REPO / "configs" / "indicators.yaml")
    assert reg.get("us_cpi").series == "fred:CPIAUCSL"
    assert "inflation" in reg.categories
    bad = tmp_path / "i.yaml"
    bad.write_text(
        "indicators:\n"
        "  - {id: a, name: A, series: x, frequency: monthly, unit: u, category: c, country: TR}\n"
        "  - {id: a, name: A, series: x, frequency: monthly, unit: u, category: c, country: TR}\n"
    )
    with pytest.raises(RegistryError, match="duplicate"):
        load_registry(bad)
    with pytest.raises(RegistryError):
        reg.get("nope")


def test_vintages_show_revisions_and_new_releases() -> None:
    t1, t2 = datetime(2025, 2, 15, tzinfo=UTC), datetime(2025, 3, 15, tzinfo=UTC)
    s = ManualSeries(
        [
            SeriesPoint("S", date(2025, 1, 1), Decimal("100"), "t", t1 - timedelta(days=5)),
            SeriesPoint("S", date(2025, 1, 1), Decimal("100.4"), "t", t2 - timedelta(days=5)),
            SeriesPoint("S", date(2025, 2, 1), Decimal("101"), "t", t2 - timedelta(days=5)),
        ]
    )
    revs = revisions(s, "S", until=date(2025, 12, 1), earlier=t1, later=t2)
    assert [(r.valid_date, r.before, r.after, r.change) for r in revs] == [
        (date(2025, 1, 1), Decimal(100), Decimal("100.4"), Decimal("0.4")),
        (date(2025, 2, 1), None, Decimal(101), None),
    ]
    reg = load_registry(REPO / "configs" / "indicators.yaml")
    obs, raw = indicator_history(reg.get("us_cpi"), s, until=date(2025, 12, 1), as_of=LATE)
    assert obs == []  # no fred:CPIAUCSL data: unavailable, never invented
    assert raw == []


def test_curve_interpolation_spreads_and_staleness() -> None:
    day = date(2026, 10, 23)
    known = datetime(2026, 10, 24, tzinfo=UTC)
    values = {"fred:DGS3MO": "4.00", "fred:DGS2": "3.50", "fred:DGS10": "4.25"}
    pts = [SeriesPoint(k, day, Decimal(v), "t", known) for k, v in values.items()]
    pts.append(SeriesPoint("fred:DGS30", day - timedelta(days=10), Decimal("4.6"), "t", known))
    c = curve(US_TREASURY, ManualSeries(pts), day, as_of=LATE)
    assert [p.label for p in c.points] == ["3M", "2Y", "10Y", "30Y"]
    assert c.spread("10Y", "2Y") == Decimal("0.75")
    assert c.spread("10Y", "3M") == Decimal("0.25")
    assert c.at(6.0) == pytest.approx(3.5 + 0.5 * 0.75)  # halfway between 2Y and 10Y
    assert c.at(0.1) is None  # below the shortest known tenor: no extrapolation
    assert next(p for p in c.points if p.label == "30Y").stale
    assert "1M" in c.missing
    assert curve(US_TREASURY, ManualSeries(pts), day, as_of=known - timedelta(days=1)).points == []
