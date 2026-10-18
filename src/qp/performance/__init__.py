"""qp.performance — TWR, XIRR, real returns, benchmarks, drawdowns and attribution.

Phase 1 provides the pure return functions in qp.performance.returns (needed by the
reference cases); phase 3 adds portfolio history, benchmarks, drawdowns and attribution.
May import portfolio and below; never factors/valuation. The per-position FX/price P&L
split lives in qp.portfolio (it is part of valuing a holding).
"""
