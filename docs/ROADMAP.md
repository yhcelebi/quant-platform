# Roadmap

Each phase must meet its exit criteria before the next starts. One phase per session.

## Status

| Phase | Name                         | Status       | Exit criteria met | Notes |
|-------|------------------------------|--------------|-------------------|-------|
| 0     | Foundation                   | done         | yes (2026-10-18)  | `make check` green in CI ([run 39115989762](https://github.com/yhcelebi/quant-platform/actions/runs/39115989762)); local `db-up`/`migrate`/`doctor` ok on owner machine (2026-10-18) |
| 1     | Ledger & portfolio           | in progress  | partly            | Built; reference cases a–f pass; CI green ([run 39149610358](https://github.com/yhcelebi/quant-platform/actions/runs/39149610358)). Local DB at head `0002`. 2026-10-19: correctness review, 11 issues fixed with regression tests (248 tests). Awaiting owner data: holdings vs broker statements (`qp ledger reconcile`); see `docs/owner-questions.md` |
| 2     | Data lake & connectors       | in progress  | partly            | Started on owner "go" with Phase 1 reconcile still open. Lake + leak property tests, calendars, TCMB FX (backfilled 2025-01-02..2026-10-16, quality ok), FRED/EVDS connectors (need keys), adjusted prices, action review queue, workers, Data page. Open: price source (owner Q7/Q8), first keyed FRED/EVDS runs |
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
