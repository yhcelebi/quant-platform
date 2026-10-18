# Canonical ledger CSV format

`qp ledger import FILE [--dry-run]` reads this format. A working example that reproduces
reference cases a–d lives in [`docs/examples/`](examples/) and is imported by the test suite.

## Rules

- UTF-8 (a BOM from Excel is fine), comma-separated, one header row.
- Header: any subset of the columns below, in any order. Unknown or duplicated columns are
  an error.
- Every row has exactly as many fields as the header. A Turkish decimal comma (`12,50`) or an
  unquoted comma in a note adds a field and rejects the row; quote such text (`"a, b"`).
- Required in every row: `account`, `type`, `trade_date`.
- A row may fill only the columns its type uses (table below, plus `trade_time`,
  `settle_date`, `external_ref`, `note`). A value anywhere else is an error rather than being
  ignored: a fee on a deposit row would otherwise vanish. Record it as its own `fee` row.
- Numbers are plain decimals with a dot (`1234.56`), no thousands separators. Percentages
  are fractions (`0.10`, not `10`).
- Dates are `YYYY-MM-DD`. Times (`trade_time`, `known_time`) are ISO 8601 **with an
  offset**, e.g. `2025-01-02T10:15:00+03:00`. A time without an offset is rejected.
  `trade_time` must fall on `trade_date` in Europe/Istanbul time.
- The whole file is one unit: one bad row rejects the file and nothing is written.
- The same file (by SHA-256) cannot be imported twice. `external_ref` is unique per account;
  use the broker's transaction id where you have one.
- Accounts (`qp ledger account add`) and instruments (`qp ref import`) must exist first.
- Every transaction in a non-TRY currency that changes cost or income needs `fx_rate_try`
  (TRY per 1 unit of that currency, at trade time): buy, sell, rights subscription,
  dividend, interest, opening position, delisting with proceeds.
- Mistakes are corrected with `qp ledger reverse <id> --note "..."` plus a corrected row,
  never by editing data (the database rejects updates and deletes).

## Columns

| Column | Meaning |
|---|---|
| `account` | Account code |
| `type` | Transaction type (below) |
| `trade_date` | Trade date; pay date for dividends; ex-date for share-count actions and mergers |
| `trade_time` | Optional execution time with offset (orders same-day trades) |
| `settle_date` | Optional |
| `instrument` | `SYMBOL.VENUE`, e.g. `THYAO.BIST` |
| `quantity` | Positive number of shares/units; for split / bonus / stock dividend / merger, the shares received (negative for a reverse split) |
| `price` | Per-unit price in the instrument currency |
| `currency` | ISO 4217; defaults to the instrument currency, and must match it |
| `fee` | Total fees and commissions (incl. BSMV), >= 0 |
| `fee_currency` | Defaults to `currency`; may be TRY for a foreign trade |
| `amount` | Cash amount (deposits, withdrawals, fees, tax, interest gross, opening cash, opening position total cost, delisting proceeds) |
| `fx_rate_try` | TRY per 1 unit of the transaction currency, at trade time |
| `to_currency`, `to_amount` | FX conversion: currency and amount received |
| `ratio` | Corporate-action ratio (see below) |
| `ex_date` | Corporate-action ex-date (defaults to `trade_date`) |
| `amount_per_share` | Gross dividend per share |
| `gross`, `withholding`, `net` | Dividend amounts as on the statement (optional; computed if absent) |
| `withholding_rate` | Fraction, used when `withholding` is absent |
| `new_instrument` | Merger: the instrument received |
| `transfer_id` | Pairs a `transfer_out` with its `transfer_in` |
| `known_time` | When a corporate action became known (defaults to import time) |
| `external_ref` | Broker reference, unique per account |
| `note` | Free text |

## Types

| `type` | Required columns | Notes |
|---|---|---|
| `opening_balance` | `instrument`, `quantity`, `amount` (total cost), `fx_rate_try` if not TRY — or `currency`, `amount` for cash | Positions/cash held before the ledger starts. Not an external flow. |
| `deposit`, `withdrawal` | `currency`, `amount` | External flows (used for TWR/XIRR). |
| `buy`, `sell` | `instrument`, `quantity`, `price` | Optional `fee`, `fee_currency`, `fx_rate_try`. Overselling is an error. |
| `fee`, `tax` | `currency`, `amount` | Stand-alone charges (custody fee, tax payment). |
| `interest` | `currency`, `amount` (gross) | Optional `withholding` or `withholding_rate`. |
| `fx_conversion` | `currency`, `amount` (sold), `to_currency`, `to_amount` | A conversion fee is a separate `fee` row. |
| `transfer_out`, `transfer_in` | `transfer_id` + (`instrument`, `quantity`) or (`currency`, `amount`) | Between your own accounts; cost basis and lot dates travel with the shares. |
| `split` | `instrument`, `ratio` | New shares per old share (2-for-1: `2`; 1-for-10: `0.1`). Booked on the ex-date. |
| `bonus_issue` | `instrument`, `ratio` | Bedelsiz: ratio *k* adds *k* shares per share (100%: `1`). Booked on the ex-date. |
| `stock_dividend` | `instrument`, `ratio` | As `bonus_issue`. |
| `rights_subscription` | `instrument`, `quantity`, `price` (subscription price), `ratio`, `ex_date` | Bedelli: shares subscribed and paid for. |
| `cash_dividend` | `instrument`, `ex_date`, `amount_per_share`, and `withholding` or `withholding_rate` | `trade_date` is the pay date. Eligible shares = holding at the end of the day before the ex-date. A fee is a separate `fee` row. |
| `merger` | `instrument`, `new_instrument`, `ratio` | Stock-for-stock, same currency; whole position exchanged, cost carried over. Record any cash part as a separate `sell`. |
| `delisting` | `instrument` | Optional `amount` (cash received) and `fx_rate_try` (needed only with proceeds). Removes the holding at the start of `trade_date`, like every corporate action. Realizes the result. |

For split / bonus / stock dividend / merger, `quantity` (shares received) is optional; it is
computed from the holding and the ratio. Give it when the broker rounded fractions; a
difference of less than one share is accepted with a warning.

Shares between a `transfer_out` and its `transfer_in` belong to no account, so a split,
bonus issue or dividend with an ex-date in that window does not see them; the import warns
when this happens. Book the transfer so it completes before the ex-date where you can.

## Corporate actions

Each corporate-action row also records the instrument-level action in
`ref.corporate_actions` (once per instrument, type and ex-date). A later row for the same
action must use the same terms, otherwise the import fails.

## Manual prices and FX (`data/manual/`)

`prices.csv`: `valid_date,instrument,price,currency,source,known_time`
`fx.csv`: `valid_date,currency,rate_try,source,known_time`

`known_time` is optional (default: 23:59:59 Istanbul on `valid_date`). Add a later row with
a later `known_time` to revise a value; history views (`--known-at`) see the old value.

## Broker statement snapshot (`qp ledger reconcile`)

`account,instrument,quantity,currency,amount` — one row per position
(`account,instrument,quantity`) or cash balance (`account,currency,amount`).
