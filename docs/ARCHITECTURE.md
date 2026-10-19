# Architecture

Modular monolith: one Python package `qp` (src layout) and a few processes.
No microservices. No C++ unless profiling proves a need. See ADR 0001.

## Processes

| Process          | Role                                                                                       |
|------------------|--------------------------------------------------------------------------------------------|
| `apps/workers`   | APScheduler jobs: ingestion, quality checks, derived data, daily portfolio snapshots, alert evaluation |
| `apps/trader`    | NautilusTrader `TradingNode`, the qp risk gate, the fill → ledger bridge, heartbeat, reconciliation |
| `apps/terminal`  | Streamlit multipage UI. Read-mostly; its only writes are journal entries and the kill switch |
| `apps/cli`       | Typer CLI (`qp ...`) for imports, backfills, backtests, reports and admin                  |
| `research/`      | Notebooks that call `qp` functions. They write only to `data/lake/results/`                |

Apps are thin: argument parsing, wiring and presentation. Every calculation lives in `qp`.

## Storage

### PostgreSQL 16 — operational state (concurrent writers)

| Schema    | Tables                                                                                     |
|-----------|--------------------------------------------------------------------------------------------|
| `ref`     | instruments, instrument_aliases, venues, calendars, corporate_actions                      |
| `ledger`  | accounts, sleeves, transactions, transaction_legs, import_batches                          |
| `trading` | strategies, promotions, orders, fills, positions_snapshot, reconciliations, trading_state, heartbeats |
| `news`    | items, item_entities, item_tags, sources                                                   |
| `journal` | theses, entries, reviews, watchlists                                                       |
| `ops`     | job_runs, data_quality_results, alerts, alert_events                                       |

Local server: Docker Compose, `postgres:16.15`, bound to `127.0.0.1:5433`.
Migrations: Alembic in `db/migrations/` (the URL comes from qp settings, never alembic.ini). The ledger tables are append-only (no UPDATE/DELETE
grants for the application role on `ledger.transactions` / `ledger.transaction_legs`).

### Parquet lake — history, queried with DuckDB

```
data/lake/raw/{source}/{dataset}/fetched_date=YYYY-MM-DD/   immutable, as fetched
data/lake/clean/                                             normalized, typed, PIT columns
data/lake/derived/                                           adjusted prices, returns, factors
data/lake/results/{backtests,studies}/{id}/                  research and backtest outputs
```

- Every row of external data carries `source`, `valid_time`, `known_time`, plus
  `known_time_basis` and `fetched_at`; queries take `as_of` (ADR 0007).
- Unadjusted prices are always kept. Adjusted prices are derived from `ref.corporate_actions`.
- Every result records: git commit, config hash, dataset snapshot (max `known_time`),
  parameters, seed and package versions.

## Layers

High → low. A layer imports only lower layers. `|` marks siblings that must not import each
other. Enforced by `.importlinter` (contract `qp-layers`).

```
assistant
reporting
decisions | alerts
backtest  | execution          must never import each other: keeps parity real
strategies                     signals/ (pure), Nautilus wrappers, registry + promotions
optimization | scenarios
risk                           portfolio risk + pre-trade gate + kill switch
performance | factors | valuation
portfolio
ledger | macro | news | fundamentals
data                           connectors/, lake/, quality/, catalog, as_of queries
reference                      instrument master, calendars, corporate actions
platform                       settings, logging, DB session, lake paths, notifier
stats                          pure statistics
core                           Money, Currency, Quantity, InstrumentId, time, Clock, errors
```

### Library boundaries

| Library            | Allowed importers                                       | Enforcement                         |
|--------------------|---------------------------------------------------------|-------------------------------------|
| `nautilus_trader`  | `qp.strategies` (not `qp.strategies.signals`), `qp.backtest`, `qp.execution` | import-linter                       |
| `vectorbt`         | `research/` only                                        | import-linter (qp), ruff banned-api (apps, notebooks) |
| `streamlit`, `typer` | `apps/` only                                          | import-linter (qp), ruff banned-api (research) |

`qp.strategies.signals` must stay pure: no Nautilus, no DB, no lake, no network. It may use
`qp.core`, `qp.stats`, numpy and pandas.

### Notable consequences of the layer map

- `qp.strategies` cannot import `qp.execution`, so strategies can never reach a broker adapter.
- `qp.risk` sits below `qp.strategies`. The pre-trade gate's "strategy is approved" check
  therefore reads approval status through a lower-layer interface (a promotion-status
  reader in `qp.risk`, backed by the `trading.promotions` table via `qp.platform`'s DB
  session) or receives it by injection. The promotion *workflow* lives in `qp.strategies`.
- `qp.backtest` and `qp.execution` share only what lives in `qp.strategies` and below.
  That is what makes backtest/paper/live parity a real property, not a hope.

## Trading path

```
signal (pure fn) → QPStrategy (Nautilus wrapper) → qp.risk gate + kill switch
                 → Nautilus RiskEngine (second layer) → ExecutionClient (adapter)
fill → fill→ledger bridge → ledger.transactions (trading accounts/sleeves only)
```

- Default mode is paper. Live requires config `mode: live` AND env
  `QP_LIVE_TRADING=I_UNDERSTAND_THE_RISK` AND an `approved_live` promotion.
- Gate rejects on: limit breach, kill switch, stale data, unapproved strategy.
- Kill switch lives in `trading.trading_state`. Halt = stop strategies + cancel open orders.
  Flatten is a separate explicit command.
- Missing heartbeat → alert. Reconciliation on startup and daily; any mismatch halts that
  account.
- Trading capital lives in its own accounts and sleeves. Long-term holdings are unreachable
  by strategies.

## Configuration

pydantic-settings, precedence low → high: `configs/base.yaml` → mode overlay
(`configs/{backtest,paper,live}/*.yaml`) → `.env` → process environment (`QP_*`). See
ADR 0003. Secrets only from env / `.env`; a secret in YAML is an error.
Tax and commission rates are dated config data.

## Repo layout

```
src/qp/<layers above>
apps/{terminal,workers,trader,cli}
research/{notebooks,studies,templates}
tests/{unit,reference,integration,bias_checks,regression}
configs/{base.yaml,backtest/,paper/,live/}
db/migrations/
docs/{adr,runbooks}/
scripts/
data/            (gitignored)
```
