# Phase 3 proposal — performance and risk

Owner "go": 2026-10-25 (after the graphify setup). Phase 1 (broker reconcile) and Phase 2
(keys, price source) stay open; everything here is built and tested on fixtures and the
example ledger, and runs on real data as soon as that data exists.

## Goal

Answer "how am I doing and how much can I lose" from the ledger and the lake, in TRY, USD and
real TRY, with every number reproducible and never estimated.

## Exit criteria

1. TWR, XIRR, real return, annualization, drawdowns, benchmark returns, P&L attribution and
   the FX/price split follow `docs/DOMAIN.md` and ADR 0010, with hand-calculated unit tests
   (derivations in the test docstrings) and property tests (TWR without flows = simple
   return; attribution sums exactly to total P&L; USD TWR = TRY TWR adjusted by USD/TRY).
2. VaR/ES (historical and parametric), exposures and correlation with hand-calculated tests.
3. `qp perf show`, `qp perf attribution`, `qp risk show` and `qp alerts run` work on the
   example ledger; Overview, Performance and Risk pages render.
4. Alerts are deduplicated and go to Telegram when configured, else to the log.
5. Missing prices, FX or inflation make a figure "unavailable" with the reason, never a guess.
6. `make check` green in CI.

Proposed new owner reference cases (for you to add to `tests/reference/` if you agree; I
do not edit that directory) are in `docs/owner-questions.md`, Phase 3 section.

## Design (details and conventions: ADR 0010)

- `qp.ledger.engine.replay_steps`: the replay engine yields its state after each trade date,
  so a daily history costs one replay, not one per day. `replay` uses it (one implementation).
- `qp.data.market`: a `MarketHistory` interface (all points up to a date, as known at
  `as_of`) and `PreloadedMarketData`, which answers point queries from memory.
  `qp.data.series`: index and rate series (TÜFE, deposit rates) from the lake (EVDS) or a
  manual CSV (`data/manual/series.csv`) until the EVDS key exists.
- `qp.stats`: pure float64 statistics (volatility, VaR, ES, drawdown, correlation).
- `qp.performance`: `history` (daily values and external flows per scope and currency),
  `metrics` (TWR, XIRR, real, annualized, monthly table, drawdowns), `benchmarks`,
  `attribution`, `service` (wiring for apps).
- `qp.risk`: `var` (portfolio and position-level historical simulation, parametric),
  `exposure` (currency, asset class, account, concentration), `correlation`, `service`.
- `qp.alerts`: rules (daily loss, drawdown, VaR breach, stale data), `ops.alert_events` for
  deduplication (migration 0004), a workers job after the evening TCMB fetch.
- Apps: `qp perf`, `qp risk`, `qp alerts`; Overview, Performance and Risk pages.

## Risks

- Real returns need TÜFE. Until the EVDS key is in, enter index levels in
  `data/manual/series.csv`, or real figures show as unavailable.
- Benchmarks other than USD/TRY need price or rate data (Q7/Q8, EVDS). They are configured
  but show as unavailable until data exists.
- Nothing is checked against the owner's real portfolio until Phase 1 is reconciled.
