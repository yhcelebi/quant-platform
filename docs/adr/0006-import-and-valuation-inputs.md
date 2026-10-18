# ADR 0006 — Ledger import format and phase 1 valuation inputs

- Status: accepted
- Date: 2026-10-18

## Decision

1. **One canonical CSV format** (docs/ledger-csv-format.md) for all brokers. Broker-specific
   exports are converted to it (by hand now; adapters later, each one a new module that
   emits canonical rows). Unknown columns are errors so typos fail loudly.
2. **Import = plan, then commit.** `plan_import` is pure: parse, validate per type, build
   with the canonical builders, and simulate on top of the existing ledger. `--dry-run`
   shows the resulting position and cash changes. `commit_import` writes the import batch,
   any new corporate actions and the transactions in one database transaction.
3. **Idempotency.** A file's SHA-256 is unique in `ledger.import_batches`;
   `(account, external_ref)` is unique in `ledger.transactions`.
4. **FX for TRY cost** comes from the row (`fx_rate_try`), i.e. from the broker statement or
   a rate the owner chooses. There is no silent fallback. Automatic TCMB rates arrive with
   the phase 2 connectors once the owner chooses which TCMB rate applies.
5. **Withholding** comes from the row (`withholding` amount or `withholding_rate`) or from
   dated rates in `configs/rates/*.yaml`. That table ships empty: a rate is added only with a
   cited official source confirmed by the owner.
6. **Phase 1 valuation inputs** are owner-maintained `prices.csv` and `fx.csv` in
   `data/manual/` (gitignored), behind the `qp.data.market.MarketData` interface that the
   phase 2 lake will also implement. Each row has `source` and `known_time` (default end of
   day, Istanbul). Missing or stale (> 7 days, configurable) prices and FX rates are
   reported, never estimated; totals are marked incomplete.
7. **USD view** converts TRY values at the as-of USD/TRY rate. USD-basis cost and P&L for
   TRY assets need historical USD rates per lot and are deferred to phase 3 (performance).

## Consequences

- The owner's real data never enters the repository; examples and tests are synthetic.
- Phase 2 replaces the manual tables without touching portfolio code.
