# ADR 0004 — Money, numbers and time primitives

- Status: accepted
- Date: 2026-10-18

## Decision

### Money and numbers
- `Money(amount: Decimal, currency: Currency)`, `Quantity(value: Decimal)`. Floats are
  rejected at every entry point (`FloatNotAllowedError`); amounts are built from `str`,
  `int` or `Decimal`.
- `Currency` accepts only ISO 4217 codes listed in `qp.core.currency` with their minor
  units. Crypto assets are instruments, not currencies.
- Arithmetic between Money values requires one currency; conversion is explicit through
  `FxRate(base, quote, rate)`, which converts base -> quote only (`inverse()` for the other
  direction).
- **No implicit rounding.** Average cost and intermediate results keep full precision
  (15.015 stays 15.015). `Money.quantize()` rounds to the currency's minor unit,
  `ROUND_HALF_EVEN` by default, and is called only where cash is actually booked.
- **Decimal context.** qp uses Python's default context: 28 significant digits,
  `ROUND_HALF_EVEN`. Addition and subtraction are exact for amounts below 1e15 with up to
  10 decimal places. Results needing more than 28 significant digits are rounded by the
  context; division results (e.g. averages) are rounded at 28 significant digits. qp does
  not modify the global context. A property test found the 28-digit limit; the test domain
  is bounded accordingly and a test documents the behaviour.
- `qp.core.numeric.to_float64` / `to_float64_list` is the one Decimal -> float64 boundary
  for analytics; `float_to_decimal(x, places=)` is the explicit way back.

### Identifiers
- `InstrumentId(symbol, venue)` parses `SYMBOL.VENUE` by splitting on the **last** dot, so
  `BRK.B.NYSE` is symbol `BRK.B`, venue `NYSE`. Canonical IDs are upper case and parsing
  does not normalise case.

### Time
- `ensure_utc` raises `NaiveDatetimeError` on naive input; `to_istanbul` is for display.
- `assume_istanbul(naive)` is the single, explicit way to interpret a naive local
  wall-clock time (e.g. broker CSVs). It uses the IANA database, so historical Turkish DST
  (before 2016) is handled.
- Code obtains "now" from a `Clock` (`SystemClock`, `ManualClock` for tests).
- Database sessions run with `timezone=UTC`.

## Consequences
- Rounding happens at a small number of visible call sites, which keeps reconciliation
  with broker statements auditable.
- The FX rate source for TRY lot cost (TCMB indicative vs broker rate) is not decided here;
  it is a phase 1 decision.
