from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

import pytest

from qp.core import QPValidationError
from qp.performance.returns import xirr

D0 = date(2025, 1, 1)


@pytest.mark.parametrize(
    ("end", "value", "expected"),
    [
        (date(2025, 1, 2), "105", 1.05**365 - 1),  # +5% in one day: ~5.4e7 annualized
        (date(2025, 1, 11), "150", 1.5**36.5 - 1),
        (date(2025, 1, 11), "40", 0.4**36.5 - 1),  # -60% in ten days: just above -100%
        (date(2026, 1, 1), "110", 0.10),
        (date(2027, 1, 1), "81", -0.10),  # 730 days: (1 + x)^2 = 0.81
    ],
)
def test_xirr_two_flows_matches_closed_form(end: date, value: str, expected: float) -> None:
    result = xirr([(D0, Decimal(-100)), (end, Decimal(value))])
    assert math.isclose(result, expected, rel_tol=1e-9, abs_tol=1e-12)


def test_xirr_multiple_flows_has_zero_npv() -> None:
    flows = [
        (D0, Decimal(-1000)),
        (date(2025, 3, 1), Decimal(-500)),
        (date(2025, 8, 15), Decimal(200)),
        (date(2026, 2, 1), Decimal(1450)),
    ]
    x = xirr(flows)
    npv = sum(float(cf) / (1 + x) ** ((d - D0).days / 365) for d, cf in flows)
    assert abs(npv) < 1e-6


def test_xirr_needs_both_signs() -> None:
    with pytest.raises(QPValidationError, match="both negative and positive"):
        xirr([(D0, Decimal(-1)), (date(2025, 2, 1), Decimal(-1))])
