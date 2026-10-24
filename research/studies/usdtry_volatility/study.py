"""USD/TRY volatility from TCMB indicative rates in the lake (point in time)."""

from __future__ import annotations

import math
from itertools import pairwise
from typing import Any

import numpy as np

from qp.core import to_float64
from qp.data.connectors import tcmb
from qp.stats import bootstrap, garch11, stationarity, volatility


def run(ctx: Any) -> dict[str, Any]:
    rows = ctx.lake(
        tcmb.DATASET, currency=ctx.params["currency"], rate_kind=ctx.params["rate_kind"]
    )
    if len(rows) < 120:
        return {"error": f"only {len(rows)} TCMB observations known at {ctx.as_of}"}
    levels = [to_float64(r["value"]) / r["unit"] for r in rows]
    dates = [r["valid_date"] for r in rows]
    rets = [math.log(b / a) for a, b in pairwise(levels)]
    ctx.note(f"{len(rows)} bulletins {dates[0]}..{dates[-1]}; log returns between bulletins")
    st = stationarity(rets)
    g = garch11(rets)
    ci = bootstrap(
        rets, lambda x: float(np.mean(x)) * 252, reps=ctx.params["bootstrap_reps"], seed=ctx.seed
    )
    return {
        "observations": len(rets),
        "first": dates[0],
        "last": dates[-1],
        "annualized_volatility": volatility(rets),
        "annualized_mean_log_return": ci.estimate,
        "mean_ci_95": [ci.low, ci.high],
        "stationarity": {
            "verdict": st.verdict(),
            "adf_p": st.adf.pvalue,
            "kpss_p": st.kpss.pvalue,
            "kpss_p_is_bound": st.kpss.pvalue_is_bound,
        },
        "garch": {
            "omega": g.omega,
            "alpha": g.alpha,
            "beta": g.beta,
            "persistence": g.persistence,
            "converged": g.converged,
            "reliable": g.reliable,
            "next_day_volatility": g.next_volatility,
            "next_day_volatility_annualized": g.next_volatility * math.sqrt(252),
        },
        "hypothesis_holds": (
            None if not g.reliable else st.verdict() == "stationary" and g.persistence > 0.8
        ),
    }
