from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from qp.core import TRY, USD, InstrumentId, QPValidationError
from qp.reference.domain import (
    AssetClass,
    CorporateAction,
    CorporateActionType,
    Instrument,
    price_adjustment_factor,
)
from qp.reference.importer import plan_instrument_import
from qp.reference.rates import DatedRate, RateTable

T = datetime(2025, 1, 1, tzinfo=UTC)
X = InstrumentId.parse("X.BIST")


def ca(kind: CorporateActionType, **kw: object) -> CorporateAction:
    return CorporateAction(X, kind, date(2025, 1, 2), "test", T, **kw)  # type: ignore[arg-type]


def test_share_factors_and_price_adjustment() -> None:
    assert price_adjustment_factor(
        ca(CorporateActionType.BONUS_ISSUE, ratio=Decimal(1))
    ) == Decimal("0.5")
    assert price_adjustment_factor(ca(CorporateActionType.SPLIT, ratio=Decimal(4))) == Decimal(
        "0.25"
    )
    assert ca(CorporateActionType.STOCK_DIVIDEND, ratio=Decimal("0.1")).share_factor == Decimal(
        "1.1"
    )
    with pytest.raises(NotImplementedError):
        price_adjustment_factor(
            ca(CorporateActionType.CASH_DIVIDEND, amount_per_share=Decimal(1), currency=TRY)
        )


@pytest.mark.parametrize(
    ("kind", "kw"),
    [
        (CorporateActionType.SPLIT, {}),
        (CorporateActionType.BONUS_ISSUE, {"ratio": Decimal(0)}),
        (CorporateActionType.CASH_DIVIDEND, {"amount_per_share": Decimal(1)}),
        (CorporateActionType.RIGHTS_ISSUE, {"ratio": Decimal(1)}),
        (CorporateActionType.MERGER, {"ratio": Decimal(1)}),
        (
            CorporateActionType.CASH_DIVIDEND,
            {"amount_per_share": Decimal(1), "currency": TRY, "pay_date": date(2025, 1, 1)},
        ),
    ],
)
def test_invalid_corporate_actions(kind: CorporateActionType, kw: dict[str, object]) -> None:
    with pytest.raises(QPValidationError):
        ca(kind, **kw)


def test_naive_known_time_is_rejected() -> None:
    naive = datetime(2025, 1, 1)  # noqa: DTZ001 - deliberately naive
    with pytest.raises(ValueError, match="naive"):
        CorporateAction(X, CorporateActionType.DELISTING, date(2025, 1, 2), "t", naive)


def test_rate_table_lookup_and_validation(tmp_path: Path) -> None:
    table = RateTable(
        [
            DatedRate("k", Decimal("0.10"), date(2020, 1, 1), "src A"),
            DatedRate("k", Decimal("0.15"), date(2024, 12, 22), "src B"),
        ]
    )
    assert table.lookup("k", date(2019, 1, 1)) is None
    r = table.lookup("k", date(2024, 12, 21))
    assert r is not None
    assert r.rate == Decimal("0.10")
    r = table.lookup("k", date(2024, 12, 22))
    assert r is not None
    assert r.rate == Decimal("0.15")
    assert table.lookup("other", date(2025, 1, 1)) is None
    with pytest.raises(QPValidationError, match="no source"):
        RateTable([DatedRate("k", Decimal("0.1"), date(2020, 1, 1), "")])
    with pytest.raises(QPValidationError, match="fraction"):
        RateTable([DatedRate("k", Decimal("15"), date(2020, 1, 1), "s")])
    (tmp_path / "r.yaml").write_text(
        "rates:\n  - {key: k, rate: '0.2', effective_from: 2021-01-01, source: s}\n"
    )
    loaded = RateTable.from_dir(tmp_path).lookup("k", date(2022, 1, 1))
    assert loaded is not None
    assert loaded.rate == Decimal("0.2")


def test_repo_rates_file_loads() -> None:
    RateTable.from_dir(Path(__file__).resolve().parents[3] / "configs" / "rates")


def test_instrument_import_plan() -> None:
    existing = {X: Instrument(X, TRY, AssetClass.EQUITY, "X")}
    text = (
        "id,asset_class,currency,name,isin\n"
        "X.BIST,equity,TRY,X,\n"
        "AAPL.NASDAQ,equity,USD,Apple,\n"
        "Y.NOWHERE,equity,TRY,,\n"
        "Z.BIST,stock,TRY,,\n"
    )
    plan = plan_instrument_import(text, existing, {"BIST", "NASDAQ"})
    assert [str(i.id) for i in plan.new] == ["AAPL.NASDAQ"]
    assert plan.new[0].currency == USD
    assert len(plan.unchanged) == 1
    assert len(plan.errors) == 2
    changed = plan_instrument_import(
        "id,asset_class,currency\nX.BIST,etf,TRY\n", existing, {"BIST"}
    )
    assert "different attributes" in changed.errors[0]
