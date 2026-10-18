"""Smoke-test that the research and trading dependency groups import and run together.

Verifies the single uv lockfile is usable at runtime (e.g. pandas 3 with vectorbt,
riskfolio-lib, cvxpy and nautilus_trader), not only resolvable. Run: `make deps-smoke`.
This script is exempt from the TID251 import bans: it checks installation, it is not
application code.
"""

from __future__ import annotations

import importlib.metadata as md
import sys
import traceback
from collections.abc import Callable

import numpy as np
import pandas as pd


def check_vectorbt() -> str:
    import vectorbt as vbt

    idx = pd.date_range("2025-01-01", periods=6, freq="D")
    close = pd.Series([10.0, 11.0, 12.0, 11.0, 13.0, 14.0], index=idx)
    entries = pd.Series([True, False, False, False, False, False], index=idx)
    exits = pd.Series([False, False, False, False, False, True], index=idx)
    pf = vbt.Portfolio.from_signals(close, entries, exits, init_cash=100.0, fees=0.0)
    total = float(pf.total_return())
    assert abs(total - 0.4) < 1e-9, total
    return f"total_return={total:.4f}"


def check_cvxpy() -> str:
    import cvxpy as cp

    x = cp.Variable(2)
    prob = cp.Problem(cp.Minimize(cp.sum_squares(x - np.array([1.0, 2.0]))), [cp.sum(x) == 1])
    prob.solve()
    assert prob.status == cp.OPTIMAL, prob.status
    return f"status={prob.status} solvers={cp.installed_solvers()}"


def check_riskfolio() -> str:
    import riskfolio as rp

    rng = np.random.default_rng(7)
    returns = pd.DataFrame(rng.normal(0.0005, 0.01, size=(250, 3)), columns=["A", "B", "C"])
    port = rp.Portfolio(returns=returns)
    port.assets_stats(method_mu="hist", method_cov="hist")
    w = port.optimization(model="Classic", rm="MV", obj="MinRisk", hist=True)
    assert w is not None
    total = float(w.sum().iloc[0])
    assert abs(total - 1.0) < 1e-6, total
    return f"weights_sum={total:.6f}"


def check_nautilus() -> str:
    from nautilus_trader.model.identifiers import InstrumentId

    iid = InstrumentId.from_str("AAPL.NASDAQ")
    assert str(iid) == "AAPL.NASDAQ"
    return f"instrument_id={iid}"


def check_ccxt() -> str:
    import ccxt

    assert "binance" in ccxt.exchanges
    return f"exchanges={len(ccxt.exchanges)}"


CHECKS: dict[str, Callable[[], str]] = {
    "vectorbt": check_vectorbt,
    "cvxpy": check_cvxpy,
    "riskfolio-lib": check_riskfolio,
    "nautilus_trader": check_nautilus,
    "ccxt": check_ccxt,
}


def main() -> int:
    print(f"python {sys.version.split()[0]} | pandas {pd.__version__} | numpy {np.__version__}")
    failures = 0
    for dist, check in CHECKS.items():
        try:
            detail = check()
            print(f"ok    {dist} {md.version(dist)}: {detail}")
        except Exception:
            failures += 1
            print(f"FAIL  {dist}:")
            traceback.print_exc()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
