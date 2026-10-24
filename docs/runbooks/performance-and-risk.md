# Runbook: performance, risk and alerts (phase 3)

Conventions: ADR 0010. Every figure comes from the ledger and market data; a figure that
cannot be computed says why (missing price, rate or index) instead of guessing.

```bash
make db-up && make migrate                      # migration 0004 adds ops.alert_events

# Performance (TRY, USD or real TRY); default: since the first transaction, to today
uv run qp perf show
uv run qp perf show --view usd --start 2025-12-31 --end 2026-06-30
uv run qp perf show --view real                 # needs performance.inflation_series
uv run qp perf show --account ISY-MAIN --known-at 2026-03-01T00:00:00+03:00

# Where the P&L came from: instruments (with FX / price split), cash, fees and interest
uv run qp perf attribution --start 2025-12-31 --end 2026-06-30

# One-day VaR/ES, exposures, concentration, correlation
uv run qp risk show --as-of 2026-06-30

# Alerts (also scheduled by `make workers`, Mon-Fri 18:30 Istanbul)
uv run qp alerts run --dry-run                  # what would alert today
uv run qp alerts run                            # store new ones once, send (Telegram or log)
uv run qp alerts list
```

Terminal: Overview, Performance and Risk pages (`make terminal`); the sidebar's "Reload
data" re-reads the ledger after an import.

## Data the figures need

- **Prices** for every holding on every valuation day (Mon-Fri). Until a price source is
  chosen (owner questions Q7/Q8) they come from `data/manual/prices.csv`; the latest price on
  or before a day is used and flagged as stale after 7 days. A holding with no price yet makes
  the window unavailable from that day: start the window later or add the price.
- **FX**: TCMB rates from the lake (`qp data fetch tcmb`), or `data/manual/fx.csv`.
- **Inflation (real view)**: a monthly index. With an EVDS key set
  `performance.inflation_series: evds:CODE` (confirm the code with `qp data evds-series`);
  without one, enter levels in `data/manual/series.csv`:

  ```csv
  valid_date,series,value,source,known_time
  2025-12-01,TUFE,3200.50,TÜİK,2026-01-05T10:00:00+03:00
  ```

  `valid_date` is the first day of the month; `known_time` (required) is when TÜİK
  published it. Then `QP_PERFORMANCE__INFLATION_SERIES=TUFE` or set it in `configs/base.yaml`.
- **Benchmarks** (`performance.benchmarks` in `configs/base.yaml`): `fx` uses TCMB rates;
  `price` benchmarks need prices for their instrument id (e.g. `XU100.BIST`) in
  `prices.csv`; the `rate` benchmark needs a deposit-rate series (percent, `scale: 0.01`).
- **VaR** needs at least 60 daily returns (`risk.min_observations`); position-level
  simulation also needs daily prices per holding. Monthly prices are not enough and the
  holding is listed as left out.

## When something looks wrong

- "unavailable: DATE: X: no price on or before DATE": add a price for X on or before that
  day, or start the window after it.
- Attribution prints a residual: that is a bug (the buckets must sum exactly); report it.
- A cash bucket shows FX P&L after a foreign trade: the trade rate in the ledger differs
  from the TCMB rate of that day (ADR 0010, consequences).
