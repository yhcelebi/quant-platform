# Runbook: household books (budget, cash flow, statements)

Conventions: ADR 0017. Your books live in `data/books/` (never committed to this repository;
they have their own local git history, so every change can be undone).

## First time

```bash
uv run qp books init          # creates data/books/ from configs/books/
make web-install && make web-build
make app                      # open http://127.0.0.1:8765
```

Then, once:

1. **Opening balances** for 1 October 2026: in `data/books/manual.beancount`, one entry per
   account from your statements (the file has examples). Add credit cards and loans to
   `accounts.beancount` first (`open Liabilities:CreditCard:Akbank TRY`).
2. **Budgets**: in the app (Budgets page) or `budgets.beancount`. Per category or group,
   `daily`, `weekly`, `monthly`, `quarterly` or `yearly`.
3. **Each bank's statement format** (once per bank): export a statement, then

   ```bash
   uv run qp books inspect ~/Downloads/akbank.xlsx
   ```

   and copy the header names into `data/books/sources.yaml` (date, description, amount or
   debit/credit, balance). `uv run qp books sources` shows which banks are ready.

## Every day (phone or computer)

- **Add** (the + button): amount, category chip, done. Cash defaults to the wallet; the
  last account you used is remembered.
- **Overview**: today / week / month / year spending, savings rate, budgets that are close
  or over.

## Every week or month

1. Export statements from Kuveyt Türk, Vakıf Katılım, Akbank, Papara and Revolut.
2. **Import** each one (Import page: Preview, then Import). Re-importing an overlapping
   period adds nothing; a transfer between your own accounts is recorded once.
3. **Review**: categorize what no rule recognised; keep "remember" ticked so the same
   merchant is automatic next time.
4. **Statements**: income statement, balance sheet, cash flow for the month.

The same from the terminal:

```bash
uv run qp books import FILE --source akbank [--dry-run]
uv run qp books add 120 groceries Migros [--from akbank] [--date 2026-10-20]
uv run qp books review
uv run qp books categorize akbank:88a1edb4 home --learn
uv run qp books budget --period week
uv run qp books income --month 2026-10 [--detail]
uv run qp books balance [--as-of 2026-10-31]
uv run qp books cashflow --month 2026-10
uv run qp books check
```

## Using it on your phone

Recommended: **Tailscale** (free private network between your own devices; nothing is
exposed to the internet).

1. Install Tailscale on the Mac and the phone, same account.
2. On the Mac, keep `make app` running and share it inside your tailnet with HTTPS, e.g.
   `tailscale serve --bg 8765` (check `tailscale serve --help` for your version). The app
   stays bound to localhost; Tailscale forwards to it.
3. On the phone open the `https://<mac-name>.<tailnet>.ts.net` address it prints, then
   "Add to Home Screen". It opens like an app.

Alternative without `tailscale serve`: bind to the Mac's Tailscale IP (`QP_API__HOST`) with
a token (`QP_API__TOKEN` in `.env`; the app refuses other addresses without one) and enter
the token once on the phone's Settings page.

## Notes

- Amounts in USD (Revolut) are converted at the TCMB rate of the day; a missing rate is
  shown, never guessed (`uv run qp data fetch tcmb` keeps rates current; `make workers`
  fetches them every weekday).
- Midas and IBKR show the net amount transferred until their broker ledgers are imported
  into qp; then the balance sheet can value them (planned).
- Undo anything: `git -C data/books log`, then `git -C data/books revert <commit>`.
- Categories and rules: `data/books/accounts.beancount` and `data/books/rules.yaml`.
