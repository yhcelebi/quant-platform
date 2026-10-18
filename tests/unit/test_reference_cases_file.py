"""Read-only checks that the owner-owned reference file is present and well-formed.

The calculations themselves are tested against these cases from phase 1 (ledger) and
phase 3 (performance). This test never writes to tests/reference/.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

REFERENCE = Path(__file__).resolve().parents[1] / "reference" / "ledger_cases.yaml"
EXPECTED = {
    "a_weighted_average_buy_sell",
    "b_bonus_issue",
    "c_cash_dividend",
    "d_foreign_position_fx_split",
    "e_twr_and_xirr",
    "f_real_return",
}


@pytest.mark.reference
def test_reference_file_is_well_formed() -> None:
    data = yaml.safe_load(REFERENCE.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert set(data["cases"]) == EXPECTED


@pytest.mark.reference
def test_reference_numbers_are_strings_not_floats() -> None:
    """Values must be quoted strings so they load as exact Decimals, never YAML floats."""

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        else:
            assert not isinstance(node, float), f"float at {path}: {node!r}"

    data = yaml.safe_load(REFERENCE.read_text(encoding="utf-8"))
    walk(data["cases"], "cases")
    assert Decimal(data["cases"]["e_twr_and_xirr"]["expect"]["xirr"]["value"]) < 0
