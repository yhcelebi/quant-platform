# ADR 0005 — Ledger model: facts, replay, append-only

- Status: accepted
- Date: 2026-10-18

## Context

The ledger must give correct holdings and P&L at any date, survive corrections without
losing history, and reproduce past reports exactly (point-in-time honesty).

## Decision

1. **Facts only.** `ledger.transactions` (header: type, dates, account, `fx_rate_try`, links)
   and `ledger.transaction_legs` (position, cash, fee, tax, income movements as on the
   statement). Positions, average cost, FIFO lots and realized P&L are never stored; one
   pure engine (`qp.ledger.engine.replay`) derives them. Leg layouts per type are produced
   only by `qp.ledger.builders`.
2. **Cost basis.** Weighted average per (account, instrument) is the default; FIFO lots are
   tracked alongside (needed later for Turkish tax on foreign securities). Every lot keeps
   its TRY cost at the trade-time rate. Cost of a sale = cost × sold / held, exact when the
   whole position is sold.
3. **Append-only, enforced by PostgreSQL.** Triggers reject UPDATE, DELETE and TRUNCATE on
   transactions, legs, import batches and corporate actions, and set `recorded_at = now()`
   on insert so nothing can be backdated. Migration 0002's downgrade refuses a non-empty
   ledger.
4. **Corrections.** A `reversal` transaction references the original; replay drops both, as
   if the original never happened. A reversal is refused if the ledger would become
   inconsistent without the original (e.g. a later sale would oversell), and a reversal
   cannot itself be reversed.
5. **Two time axes.** `as_of` filters by trade date. `known_at` filters by `recorded_at` and
   reproduces the ledger as it was known then. Because `recorded_at` comes from the database
   clock, `known_at` is never defaulted from the application clock (a 1.5 s skew between
   host and container was measured); default views use every recorded fact.
6. **Ordering within a date:** corporate actions, transfers out, other transactions,
   transfers in; then timed before untimed, then `recorded_at`, `sequence` (file row), id.
   Share-count actions and mergers are booked on the ex-date; dividends on the pay date with
   eligibility taken from the holding at the end of the day before the ex-date.
7. **Corporate actions** are instrument-level facts in `ref.corporate_actions` (with
   `source` and `known_time`); each account's effect is a linked ledger transaction. Received
   share counts are validated against holding × ratio (difference < 1 share is a warning,
   for broker rounding). Mergers with a cash component, trading of subscription rights and
   cash-in-lieu of fractions are recorded as separate transactions.
8. **FX.** Only types that convert value to TRY (buy, sell, rights subscription, dividend,
   interest, opening position, delisting with proceeds) require `fx_rate_try`. No rate is
   ever invented for transactions that move no value.
9. **Sleeves.** Every account gets a `main` sleeve; transactions reference
   (account, sleeve). Strategy sleeves arrive with trading (phase 7).

## Consequences

- Any number shown can be re-derived from facts; a bug fix in the engine fixes history.
- Replay cost is linear in the ledger size, which is fine for a personal ledger. Snapshots
  can be added later as caches without changing the source of truth.
- Correcting a corporate action's terms has no workflow yet (the table is append-only); it
  needs its own ADR when first required.
