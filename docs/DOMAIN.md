# Domain conventions

These are binding. A calculation that disagrees with this file is a bug, unless an ADR
changes this file first.

## Identifiers

- `InstrumentId` is `SYMBOL.VENUE`: `THYAO.BIST`, `AAPL.NASDAQ`, `BTCUSDT.BINANCE`.
- Provider symbols (`THYAO.IS`, Yahoo/EVDS/ccxt codes) go in `ref.instrument_aliases`.
- Currencies use ISO 4217 codes (`TRY`, `USD`, `EUR`). Crypto assets get their own codes
  in the instrument master, not in the currency enum.

## Money and numbers

- Ledger and portfolio: `Decimal` + explicit currency (`Money`). Never float.
- Analytics (stats, risk, backtests) may use float64, converted at one explicit boundary
  function.
- Percentages are stored as fractions (`0.05`, not `5`).
- Tax and commission rates are dated config data, never constants in code.

## Time

- Timestamps are tz-aware UTC in storage; displayed in `Europe/Istanbul`.
  A naive `datetime` is a bug.
- Calendar dates (`datetime.date`) are allowed for date-valued facts (trade date, ex-date,
  period end). They are never silently converted to midnight timestamps.
- Every external fact has `source`, `valid_time`, `known_time`. Data functions take
  `as_of` and return only facts with `known_time <= as_of`.

## Cost basis

- Weighted average per (account, instrument) by default; FIFO optional.
- Buy fees are added to cost; sell fees are deducted from proceeds.
- Cost of a sale = quantity sold × current average cost. Average cost is unchanged by a sale.
- Each lot's cost is also stored in TRY at the trade-time FX rate.
- Positions, costs and P&L are derived by replaying ledger facts (ADR 0005); nothing derived
  is stored. Selling more than is held is an error.
- Same-date order: corporate actions, transfers out, other transactions, transfers in; then
  by trade time, recording time and file order.
- A reversal cancels its original (both are ignored from then on); `known_at` views still
  show the ledger as it was known before the reversal.

## Corporate actions

Types: `split`, `bonus_issue` (bedelsiz), `rights_issue` (bedelli), `cash_dividend`
(gross, withholding, net), `stock_dividend`, `merger`, `delisting`.

- Bonus issue with ratio *k*: quantity × (1 + *k*), total cost unchanged, pre-ex-date price
  factor 1 / (1 + *k*).
- Cash dividend: gross = shares × amount per share; withholding = gross × rate (dated
  config); net = gross − withholding, each rounded half-even to the currency's minor unit.
  Cost basis unchanged. Eligible shares = holding at the end of the day before the ex-date;
  the dividend is booked on the pay date.
- Split ratio *r*: quantity × *r*, cost unchanged, price factor 1 / *r*. Share-count actions
  and mergers are booked on the ex-date; merger cost carries over to the new instrument.
- Corporate actions are point-in-time facts too: adjusted prices as of *t* use only actions
  known at *t*.

## Returns

- **TWR**: chain sub-period returns split at each external flow.
  TWR = ∏(1 + rᵢ) − 1, where 1 + rᵢ = V_end / V_start, V_start is taken *after* that day's
  flow and V_end *before* the next flow.
- **XIRR (money-weighted)**: solve Σ CFₖ / (1 + x)^((tₖ − t₀)/365) = 0. CFₖ are
  investor-perspective flows (contributions negative; the ending value is a final positive
  flow). Day count actual/365.
- **Real return** = (1 + r_nom) / (1 + inflation) − 1, using TÜFE index levels.
  Never r_nom − inflation.
- **Annualization**: returns (1 + R)^(365/d) − 1 with *d* calendar days. Volatility ×√252
  for daily equities, ×√365 for crypto. Do not annualize periods under one year by default.

## FX P&L split

For a position with cost C and current value V₁ (both in instrument currency), FX rates X₀
at purchase and X₁ now (reporting currency per instrument currency):

- FX effect = C × (X₁ − X₀)
- Price effect = (V₁ − C) × X₁
- Sum = V₁·X₁ − C·X₀ = unrealized P&L in reporting currency.

## Reporting

- Views: TRY nominal, USD, TRY real.
- Benchmarks: BIST 100, USD/TRY, a TRY deposit rate, gold, S&P 500 total return.

## Data vintages and provenance

- Macro revisions: use ALFRED vintages. EVDS has no vintages, so snapshot on every fetch.
- Fundamentals are keyed by announcement time.
- News: `known_time = max(published_at, fetched_at)`. Store metadata and own summaries,
  never full copyrighted articles.

## Trading

- Promotion lifecycle: `research → backtested → paper → approved_live → retired`.
  Records store evidence: out-of-sample and walk-forward results, deflated Sharpe / PBO,
  and a paper-vs-backtest comparison.
- Backtests without a cost model (commission, BSMV, spread, slippage) are invalid.
