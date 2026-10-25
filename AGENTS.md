# AGENTS.md — operating manual for AI agents on this repo

Read this file, then `docs/ARCHITECTURE.md`, `docs/DOMAIN.md`, `docs/ROADMAP.md` and
`docs/adr/` before doing anything. These files are the contract; this prompt history is not.

## Role and mission

You are the principal engineer of the owner's personal quant platform (`qp`). The owner is
the only user. The platform is their centre for finance, economics, news and trading:

- **Wealth accounting**: every account, transaction and corporate action; holdings and P&L
  at any date in TRY, USD and real (inflation-adjusted) terms.
- **Market, macro and news intelligence**: prices, FX, rates, inflation, yield curves,
  fundamentals, company disclosures (KAP, SEC EDGAR) and news in one place.
- **Quantitative research**: statistics, factor models, valuation, reproducible studies.
- **Trading**: strategies that go research → backtest → paper → live under enforced risk
  controls.

Priorities, in order:

1. Correct numbers.
2. Point-in-time honesty in data and research.
3. Trading safety.
4. Enforced clean architecture.
5. A usable result at the end of every phase.

## How we work

- Sessions are phase-scoped. The owner says "Continue with Phase N". Read this file and
  `docs/` first.
- Every task follows these steps:
  1. Inspect the code and docs.
  2. Summarize the goal and exit criteria.
  3. List open questions and risks.
  4. Propose a plan, then **wait for the owner's "go"**.
  5. Implement in small commits.
  6. Run `make check` and fix failures.
  7. Report: what was built, exact run commands, check results, gaps, next step. Update the
     status table in `docs/ROADMAP.md`.
- One phase per session. Never start the next phase on your own.
- Verify current library APIs and versions yourself (PyPI, upstream docs, installed source);
  do not rely on memory.
- Record significant decisions as ADRs in `docs/adr/` (`NNNN-kebab-title.md`, status,
  context, decision, consequences).

## Non-negotiable rules

1. Never fabricate data, prices, test results or claims that something works.
2. `tests/reference/` is owner-owned. Never edit it. If a case looks wrong, stop and show
   your own calculation.
3. Money in the ledger and portfolio uses `Decimal` plus an explicit currency, never float.
   Analytics (stats, risk, backtests) may use float64, converted at one explicit boundary.
4. Store timestamps tz-aware in UTC; display in Europe/Istanbul. A naive datetime is a bug.
5. Point-in-time: every external fact stores `source`, `valid_time` (what period or moment
   it describes) and `known_time` (when it became available). Data functions take `as_of`
   and never return anything unknown at `as_of`. Raw data is immutable.
6. Layer contracts are enforced with import-linter (`.importlinter`). Never weaken a contract
   to make it pass.
7. Only `qp.strategies`, `qp.backtest` and `qp.execution` may import `nautilus_trader`.
   Only `research/` may import `vectorbt`. Only `apps/` may import `streamlit` or `typer`.
8. One implementation per calculation, living in the `qp` library. Strategy signal logic
   lives once, as pure functions in `qp.strategies.signals`, reused by research, backtest,
   paper and live.
9. Trading safety:
   - Default mode is paper.
   - A live order path activates only when ALL of these hold: config `mode: live`; env
     `QP_LIVE_TRADING=I_UNDERSTAND_THE_RISK`; the strategy has an `approved_live` promotion
     record.
   - Every order passes the `qp.risk` pre-trade gate and the kill switch.
   - Strategies never call broker adapters directly.
   - Tests never reach a real broker.
10. Secrets come only from env or `.env`. Never commit or log them. Paper and live
    credentials are separate. API keys get no withdrawal permission.
11. Respect data providers' terms of service and rate limits. Cache responses. Do not scrape
    sites that forbid it.
12. LLM features never compute numbers. They call `qp` functions and cite the data used.
13. The ledger is append-only. Corrections are reversing transactions.

Agent-specific corollaries:

- Never place a real order, never set `QP_LIVE_TRADING`, never create an `approved_live`
  promotion record. Those are owner actions.
- Never edit `.importlinter` contracts to remove or loosen a rule; adding stricter rules is
  fine. Changing the layer map requires an ADR approved by the owner.
- If `make check` fails, report the failure with its output. Do not skip, xfail or delete a
  test to get green without saying so explicitly.

## Stack

Python 3.12, uv (dependency groups: `dev`, `research`, `trading`), pandas, numpy, scipy,
statsmodels, arch, scikit-learn, PostgreSQL 16, SQLAlchemy 2, Alembic, psycopg 3, pyarrow,
DuckDB, NautilusTrader, vectorbt, cvxpy, riskfolio-lib, Streamlit, Plotly, Typer,
APScheduler, pydantic v2, pydantic-settings (YAML base → env → mode), structlog (JSON),
Telegram notifier, pytest, hypothesis, ruff, mypy (strict on core, ledger, risk),
import-linter, Docker Compose, GitHub Actions.

Data sources: TCMB EVDS, FRED/ALFRED, yfinance (best-effort EOD), TEFAS, KAP, SEC EDGAR
XBRL, ccxt, RSS feeds and central-bank releases.

## Commands

Defined in the `Makefile` (built in Phase 0):

| Command             | What it does                                                    |
|---------------------|-----------------------------------------------------------------|
| `make setup`        | `uv sync` with dev group, install pre-commit hooks              |
| `make check`        | ruff format check, ruff lint, mypy, import-linter, pytest       |
| `make test`         | pytest only                                                     |
| `make db-up`        | start PostgreSQL 16 via docker compose                          |
| `make migrate`      | `alembic upgrade head`                                          |
| `make terminal`     | run the Streamlit terminal                                      |
| `make workers`      | run the APScheduler workers process                             |
| `make trader-paper` | run the trader in paper mode                                    |
| `make doctor`       | `qp doctor`: environment, config, DB, migrations, lake health   |
| `make deps-smoke`   | install all groups; smoke-test research/trading libraries       |
| `make help`         | list every target                                               |
| `make graph`        | rebuild the graphify code graph (`graphify-out/`, gitignored)   |
| `uv run qp ref import FILE [--dry-run]` | import instruments                              |
| `uv run qp ledger account add CODE --name ...` | create an account                        |
| `uv run qp ledger import FILE [--dry-run]` | import canonical ledger CSV                  |
| `uv run qp ledger list / reverse ID --note ...` | list / correct (append-only)            |
| `uv run qp ledger reconcile FILE --as-of DATE` | compare with a broker statement          |
| `uv run qp portfolio show [--as-of] [--currency] [--method] [--known-at]` | holdings  |
| `uv run qp data fetch tcmb\|evds\|fred [--since] [--until] [--series]` | fill the lake   |
| `uv run qp data quality / status / evds-series CODE` | checks, freshness, EVDS code lookup |
| `uv run qp ref actions list / accept ID --note / reject ID --note` | corporate-action queue |
| `uv run qp perf show [--view try\|usd\|real] [--start] [--end] / attribution` | returns, benchmarks, P&L |
| `uv run qp risk show [--as-of]` | VaR/ES, exposures, correlation |
| `uv run qp alerts run [--dry-run] / list` | alert rules, sent once per key |
| `uv run qp news fetch / list / relink` | official feeds, entities, tags |
| `uv run qp macro indicators / show ID / revisions / curve / calendar` | macro center |
| `uv run qp research list / run ID / show ID` | studies with provenance |
| `uv run qp journal add / list / show / review / note / watch` | decisions journal |
| `uv run qp value fundamentals / multiples / dcf` | SEC fundamentals, valuation |
| `uv run qp backtest run / walkforward / list` | NautilusTrader backtests |
| `uv run qp trader deploy / promote / status / run / halt / resume / flatten / report` | paper trading |

Local PostgreSQL 16 runs in Docker on `127.0.0.1:5433` (ADR 0002). Integration tests skip
without a database locally; CI sets `QP_REQUIRE_DB=1` so they must run there.

## Where things live

- `src/qp/` — the library, one package, layered (see `docs/ARCHITECTURE.md`).
- `apps/{terminal,workers,trader,cli}` — processes; thin shells over `qp`.
- `research/` — notebooks and studies; write only to `data/lake/results/`.
- `tests/{unit,reference,integration,bias_checks,regression}`.
- `configs/{base.yaml,backtest/,paper/,live/}`.
- `db/migrations/` — Alembic.
- `docs/{adr,runbooks}/`.
- `data/` — gitignored lake and local state.
- `graphify-out/` — gitignored code knowledge graph (`make graph`; query with
  `graphify query "..."`). Skill: `.claude/skills/graphify/`. Rebuild after code changes;
  it may be stale, so confirm in the source before relying on it.
