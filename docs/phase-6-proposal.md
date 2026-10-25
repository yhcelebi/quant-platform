# Phase 6 proposal — backtesting

Status: accepted and built 2026-10-25 under the owner's standing instruction; conventions in
ADR 0014. Phases 3-5 still wait for a CI run (nothing pushed yet).

## Goal

Backtests whose numbers can be trusted: the strategy code is the code paper and live will
run, costs are always charged, no bar trades on its own close, and every variant tried is
counted.

## Exit criteria (status)

1. Pure signals with a look-ahead property test — done.
2. `QPStrategy` on NautilusTrader 1.231 with a dated cost model and one-bar lag — done.
3. Parity: Nautilus and the reference loop agree to the cent (fills, cash, daily equity) — done.
4. Walk-forward with deflated Sharpe and PBO over the grid — done.
5. Results with provenance; costless runs labelled invalid; Backtests page; `qp backtest` — done.
6. Real data: only TCMB USD/TRY (indicative rates, not tradable prices) until a price source
   is chosen (owner Q7/Q8). `make check` green in CI — pending push.
