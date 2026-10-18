# Runbook: setting up and importing your ledger

All of this runs locally; your files stay in `data/` (gitignored) or wherever you keep them.

```bash
make db-up && make migrate && make doctor          # PostgreSQL 16 on 127.0.0.1:5433

# 1. Instruments you hold (id = SYMBOL.VENUE; venues: uv run qp ref venues)
uv run qp ref import my/instruments.csv --dry-run
uv run qp ref import my/instruments.csv

# 2. Accounts (long_term by default; trading accounts with --purpose trading)
uv run qp ledger account add ISY-MAIN --name "Broker main" --currency TRY --broker "..."

# 3. Transactions in the canonical CSV (docs/ledger-csv-format.md)
uv run qp ledger import my/2025.csv --dry-run      # read errors and the change table
uv run qp ledger import my/2025.csv

# 4. Prices and FX for valuation: data/manual/prices.csv and data/manual/fx.csv
uv run qp portfolio show --as-of 2025-12-31
uv run qp portfolio show --as-of 2025-12-31 --currency USD --method fifo

# 5. Check against the broker statement (exit code 1 on any difference)
uv run qp ledger reconcile my/statement-2025-12-31.csv --as-of 2025-12-31
```

## Fixing a mistake

```bash
uv run qp ledger list --account ISY-MAIN            # find the transaction id
uv run qp ledger reverse 123 --note "wrong price; re-entered as ref X"
uv run qp ledger import my/correction.csv           # the corrected row
```

The original stays in the database; history views (`--known-at`) still show what was known
before the correction.

If the corrected trade shares its date with other trades, give it (and ideally all trades)
a `trade_time`. Untimed same-day trades are ordered by when they were recorded, so a
re-entered buy would otherwise sort after a sale imported earlier and change that sale's
average cost.

## Tips

- Start with `opening_balance` rows (positions with their total cost, and cash) on the day
  before your first imported transaction, unless you import full history.
- Use the broker's transaction id as `external_ref`: re-importing an overlapping file then
  fails loudly instead of double-counting.
- Corporate actions: book bonus issues and splits on the ex-date; dividends on the pay date
  with `ex_date` set.
