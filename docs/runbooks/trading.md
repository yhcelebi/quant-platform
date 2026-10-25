# Runbook: paper trading (phase 7)

Conventions: ADR 0015. Paper only: there is no live path. Live trading (phase 8) needs the
owner's explicit approval, an `approved_live` promotion recorded by the owner at an
interactive terminal, `mode: live` and `QP_LIVE_TRADING` in the process environment.

## Lifecycle of a strategy

```bash
# 1. deploy (status: research)
uv run qp trader deploy usd-sma --signal sma_cross --param fast=10 --param slow=50 \
    --fx USD --costs fx_try --capital 100000
# 2. evidence: a costed walk-forward of the same signal
uv run qp backtest walkforward --fx USD --signal sma_cross --costs fx_try
uv run qp trader promote usd-sma backtested --evidence data/lake/results/backtests/<id>/<run>
# 3. paper
uv run qp trader promote usd-sma paper --note "walk-forward PBO ..., DSR ..."
uv run qp trader status
```

## Running

- `make workers` runs the paper cycle Mon-Fri 18:45 Istanbul (after the TCMB fetch at
  18:15) and checks heartbeats hourly 09:00-21:00.
- By hand: `uv run qp trader run` or `make trader-paper`. A rerun the same day does nothing.
- The first cycle only records a target; trades start on the next bar (one-bar lag).

## Kill switch: halt vs flatten

```bash
uv run qp trader halt global --reason "..."        # everything stops; positions kept
uv run qp trader halt usd-sma --reason "..."       # one strategy
uv run qp trader resume usd-sma --reason "checked: ..."
uv run qp trader flatten usd-sma                   # sell to flat at the next bar, then halt
```

The Trading page has HALT ALL / Halt / Resume buttons (the terminal never places orders).
Halt drops pending targets. Flatten works even while halted (getting out must always work).

## Alerts and what to do

| Alert / state | Meaning | Action |
|---|---|---|
| `missing_heartbeat` | no paper cycle for 30 h on a weekday | is `make workers` running? `qp data status` for the TCMB fetch |
| order rejected: `stale data` | newest close older than 5 days | fetch data (`qp data fetch tcmb`); the order waits |
| halted: `daily loss` | equity fell more than 5% in one bar | look at the market, then `resume` or `flatten` |
| halted: `reconciliation mismatch` | broker position or cash differs from the ledger replay | a bug: do not resume; report it with `qp trader status` and the reconciliation detail |

## Reports

```bash
uv run qp trader report usd-sma   # paper vs the reference backtest, day by day
```

Phase 7 exits after **4+ weeks** of paper trading with this report. Differences can only come
from gate decisions or data revised after a cycle ran; both are listed.
