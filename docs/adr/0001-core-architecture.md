# ADR 0001 — Core architecture

- Status: accepted
- Date: 2026-10-18

## Context

A single owner needs one platform for wealth accounting, market/macro/news intelligence,
quantitative research and trading. Priorities: correct numbers, point-in-time honesty,
trading safety, enforced clean architecture, and a usable result at the end of every phase.
There is one developer (the owner, assisted by AI agents), so operational simplicity
matters more than independent scaling.

## Decision

1. **Modular monolith.** One Python 3.12 package `qp` (src layout), managed by uv with
   dependency groups `dev`, `research`, `trading`. A handful of processes (`workers`,
   `trader`, `terminal`, `cli`) are thin shells over the library. No microservices; no C++
   unless profiling proves a need.
2. **Two stores.** PostgreSQL 16 for operational state with concurrent writers (schemas
   `ref`, `ledger`, `trading`, `news`, `journal`, `ops`); a Parquet lake queried with DuckDB
   for history (`raw` immutable → `clean` → `derived` → `results`).
3. **Strict layering**, high → low, enforced by import-linter (`.importlinter`):
   `assistant > reporting > decisions|alerts > backtest|execution > strategies >
   optimization|scenarios > risk > performance|factors|valuation > portfolio >
   ledger|macro|news|fundamentals > data > reference > platform > stats > core`.
   Siblings separated by `|` are independent. The contract is exhaustive: any new top-level
   module under `qp` must be placed in the map (by ADR) before it can exist.
4. **Library boundaries.** `nautilus_trader` only in `qp.strategies` (excluding the pure
   `qp.strategies.signals`), `qp.backtest`, `qp.execution`. `vectorbt` only in `research/`.
   `streamlit` and `typer` only in `apps/`. import-linter enforces these for `qp`; ruff's
   `flake8-tidy-imports.banned-api` with per-path ignores enforces them for `apps/` and
   `research/` (including notebooks), which are not importable packages.
5. **NautilusTrader** for backtest, paper and live, so one strategy implementation runs in
   all three. vectorbt only for fast exploration; parity tests bound the disagreement.
6. **Decimal money** in ledger and portfolio; float64 in analytics behind one explicit
   conversion boundary.
7. **Point-in-time data model**: every external fact carries `source`, `valid_time`,
   `known_time`; every data accessor takes `as_of`.
8. **Append-only ledger**; corrections are reversing transactions.

## Consequences

- Cross-cutting features must be placed carefully. Example: the pre-trade gate in `qp.risk`
  cannot import the promotion workflow in `qp.strategies`; it reads approval status through
  its own reader over `trading.promotions` or by injection.
- `backtest` and `execution` cannot share code except via `strategies` and lower layers,
  which keeps backtest/live parity structural.
- Optional LLM features in low layers (e.g. news tagging in Phase 4) cannot live in
  `qp.news` if they need `qp.assistant`; they will need their own placement decision (ADR)
  when built.
- Dependency groups are resolved into one uv lockfile. If `research` (vectorbt) and
  `trading` (nautilus_trader) ever pin incompatible versions of shared libraries, uv's
  `conflicts` declaration (separate resolution forks) is the remedy, recorded in a new ADR.
- Changing the layer map requires a new ADR approved by the owner. Contracts are never
  weakened to make a check pass.
