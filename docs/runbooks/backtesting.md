# Runbook: backtesting (phase 6)

Conventions: ADR 0014. Engine: NautilusTrader 1.231 (installed by default with the
`trading` group).

```bash
# one backtest (stored with provenance under data/lake/results/backtests/)
uv run qp backtest run --fx USD --signal sma_cross --param fast=10 --param slow=50 --costs fx_try
uv run qp backtest run --instrument THYAO.BIST --signal momentum --param lookback=60 --costs bist_equity
# walk-forward over the signal's parameter grid: DSR and PBO count every grid point as a trial
uv run qp backtest walkforward --fx USD --signal sma_cross --costs fx_try --train 252 --test 63
uv run qp backtest list
```

Terminal: Backtests page.

## Rules that are enforced

- A signal decided at a close is traded at the **next** bar (`backtest.lag_bars`).
- Costs are always charged from a dated profile in `backtest.costs` (commission with a
  minimum, BSMV on the commission, half spread, slippage). `--costs costless` runs are stored
  but labelled **invalid**. The shipped profiles are placeholders: put your broker's rates in.
- Cash accounts are long-only; a short target is an error.
- Trades smaller than 1% of equity are skipped unless they go flat (no daily churn).
- Nautilus and the reference loop must agree to the cent (`tests/unit/backtest`).

## Adding a strategy

1. A pure function in `src/qp/strategies/signals/core.py`: `signal(closes, **params) ->
   list[float]` in [0, 1], value at t from closes up to t only; add it to `REGISTRY` with
   defaults and a parameter grid.
2. The look-ahead property test covers every registered signal automatically.
3. Run a walk-forward before believing any single backtest; report DSR and PBO with it.

## Data caveats

- Prices come from the manual table or the lake as known at `--known-at`. Until a price
  source exists (owner Q7/Q8) only TCMB FX rates have history, and those are indicative
  central-bank rates, not prices you could trade: their volatility is understated.
- Bars are close-only (O = H = L = C); no intraday stops or limits.
