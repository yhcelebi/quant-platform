# Roadmap

Each phase must meet its exit criteria before the next starts. One phase per session.

## Status

| Phase | Name                         | Status       | Exit criteria met | Notes |
|-------|------------------------------|--------------|-------------------|-------|
| 0     | Foundation                   | done         | yes (2026-10-18)  | `make check` green in CI ([run 39115989762](https://github.com/yhcelebi/quant-platform/actions/runs/39115989762)); local `db-up`/`migrate` not yet run on owner machine (Docker was off) |
| 1     | Ledger & portfolio           | not started  | —                 |       |
| 2     | Data lake & connectors       | not started  | —                 |       |
| 3     | Performance & risk           | not started  | —                 |       |
| 4     | Macro & news center          | not started  | —                 |       |
| 5     | Research lab                 | not started  | —                 |       |
| 6     | Backtesting                  | not started  | —                 |       |
| 7     | Paper trading                | not started  | —                 |       |
| 8     | Live, small capital          | not started  | —                 | Owner approval required |
| 9     | Optimization, scenarios, assistant | not started | —            |       |

Status values: `not started`, `in progress`, `done`, `blocked`.

## Phases

### 0 — Foundation
- pyproject, full `qp` package tree as documented stubs, `qp.core` and `qp.platform` fully
  built and tested.
- Tooling, Makefile (`setup`, `check`, `test`, `db-up`, `migrate`, `terminal`, `workers`,
  `trader-paper`), docker-compose with Postgres, Alembic baseline with schemas, CI,
  `qp doctor`, Streamlit skeleton with placeholder pages.
- **Exit:** `make check` green in CI.

### 1 — Ledger & portfolio
- `ref` and `ledger` schemas, all transaction types and corporate actions, holdings at any
  `as_of`.
- Canonical CSV import with dry-run, valuation from a manual price/FX table.
- CLI: `qp ledger import`, `qp portfolio show`; terminal Portfolio page.
- **Exit:** all reference cases pass; holdings match the owner's broker statements.

### 2 — Data lake & connectors
- Lake + `as_of` with leak tests, quality checks, venue calendars.
- Connectors with fixture tests, adjusted prices, a review queue for unrecorded corporate
  actions, workers jobs, lake-backed valuation.

### 3 — Performance & risk
- TWR, XIRR, real returns, benchmarks, drawdowns, attribution, FX split.
- VaR/ES, exposures, correlation.
- Terminal Overview, Performance and Risk pages; Telegram alerts.

### 4 — Macro & news center
- Indicator registry, transformations, vintages view, yield curves, economic calendar.
- News ingestion, dedup, entity linking, rule-based tags; optional LLM tagging (off by
  default).
- Holding-related alerts.

### 5 — Research lab
- Study template and registry; regression, stationarity, cointegration, GARCH, bootstrap,
  deflated Sharpe, PBO.
- Point-in-time fundamentals (KAP, EDGAR), DCF / multiples / reverse DCF, factors.
- Decisions journal (theses with invalidation criteria and review dates).

### 6 — Backtesting
- Signals, `QPStrategy`, lake-to-Nautilus catalog, cost models, walk-forward.
- Parity and bias-check tests, results with provenance, Backtests page.

### 7 — Paper trading
- Trader app, risk gate, kill switch, heartbeat, reconciliation, fill → ledger bridge,
  runbooks.
- **Exit:** 4+ weeks of paper trading and a paper-vs-backtest report.

### 8 — Live, small capital
- ONLY with the owner's explicit approval: live gating, strict limits, VPS deploy, backups
  plus a restore test.
- The agent never places a real order. The owner does the first live start manually.

### 9 — Optimization, scenarios, assistant
- Optimization: mean-variance, risk parity, HRP, CVaR.
- Stress scenarios: rate shocks, TRY depreciation, historical replay.
- Read-only LLM assistant over `qp` functions.
