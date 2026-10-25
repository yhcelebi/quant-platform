# ADR 0014 — Backtesting conventions (NautilusTrader 1.231)

- Status: accepted (owner standing instruction, 2026-10-25). *Default* items can change.
- Date: 2026-10-25

## Context

Phase 6 needs backtests whose numbers can be trusted and reproduced, and whose strategy
code is the code that will run in paper and live (rule 8). NautilusTrader 1.231.0 was
installed and probed on 2026-10-25: with bar data, a market order submitted in `on_bar`
fills immediately at that bar's close (same timestamp), and a Python `FeeModel` subclass is
called per fill with `(order, fill_qty, fill_px, instrument)`.

## Decision

1. **Dependencies**: the `trading` group (nautilus_trader, ccxt) becomes a default uv group
   so `make check` and CI type-check and test the backtest code (`default-groups = ["dev",
   "trading"]`; one ~156 MB wheel).
2. **Signals** are pure functions `signal(closes, **params) -> targets` in
   `qp.strategies.signals` (target in [-1, 1] of the strategy's equity; the value at bar t
   depends only on closes up to t). A property test perturbs future closes and checks earlier
   targets are unchanged.
3. **Execution lag** *(default 1 bar)*: the target decided at bar t's close is traded at bar
   t + 1. `QPStrategy` holds the decision and submits on the next bar, so with close-only
   bars the fill is the next close. Trading at the close a signal was computed from is not
   allowed.
4. **Sizing**: quantity = the target fraction of equity (cash + position at the current
   close) divided by the price grossed up by the cost rate, truncated toward zero to the
   lot size. Cash account; equity compounds. A trade worth less than 1% of equity *(default
   no-trade band)* is skipped unless it goes flat, so price drift does not cause daily churn.
5. **Costs are mandatory** (DOMAIN): a backtest needs a cost profile; `--costless` runs are
   labelled invalid in results. Cost per fill = `max(notional × commission, minimum)` ×
   (1 + BSMV) + notional × (half spread + slippage), in Decimal, rounded half-even to the
   currency's minor unit, charged by a Nautilus `FeeModel` and by the reference identically.
   Rates are **dated config** (`backtest.costs` profiles with `valid_from`); the fill date
   picks the rate. The defaults are placeholders the owner must replace with their broker's
   schedule (owner question 27).
6. **Data**: bars from the point-in-time price/FX history (manual table and lake) as known
   at the backtest's `as_of`; close-only, so O = H = L = C = close; bar timestamp = the
   price's `known_time` (when the close became known). No volume model.
7. **Parity**: `qp.backtest.reference` is a plain loop with the same signal, lag, sizing
   and costs. A test requires Nautilus and the reference to agree to the cent on every
   day's equity. Walk-forward grid searches use the reference for speed; reported
   out-of-sample results come from Nautilus.
8. **Walk-forward** *(default)*: rolling windows (train 252 bars, test 63 bars, step 63);
   in-sample choice by Sharpe over the signal's parameter grid; out-of-sample segments are
   concatenated. Every grid point counts as a trial: the result reports the deflated Sharpe
   ratio and PBO (CSCV) over the grid.
9. **Results** go to `data/lake/results/backtests/<id>/<UTC timestamp>/` (spec, daily
   equity, fills, metrics, provenance as for studies). Never overwritten.
