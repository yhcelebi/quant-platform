# ADR 0017 — Household books: Beancount ledger, bank imports, statements and budgets

- Status: accepted by the owner ("go with this module", 2026-10-25). Changes the layer map:
  adds `qp.books` beside `qp.ledger`. *Default* items can change.
- Date: 2026-10-25

## Context

The owner's first goal is to track spending, budgets and cash flow, and to see a balance
sheet, income statement and cash flow statement for the household. `qp.ledger` models
investment accounts (trades, dividends, FX) and has no income/expense categories,
liabilities or budgets. Institutions: Kuveyt Türk, Vakıf Katılım, Akbank, Papara, Revolut
(US); brokers Midas and IBKR. Start: October 2026.

Options considered: Beancount (plain-text double entry, Python library), Firefly III
(separate server and database), Actual Budget (budgets only, no statements), Ghostfolio
(investments only). Beancount is a Python library qp can read
directly. Fava, its web UI, was tried by the owner and rejected (wants a shadcn/ui look and
phone use).

## Decision

1. **Beancount 3 is the household book of record** (storage and accounting engine; the
   owner never has to edit its files). The books live in
   `data/books/` (gitignored: personal data never enters the qp repository).
   `qp books init` creates them and a *separate local git repository* there, so every
   change, including edits made in the app, has history (the audit trail the investment
   ledger gets from append-only tables).
2. **Chart of accounts** (Beancount's five roots): `Assets` (banks, wallets, cash,
   `Assets:Investments:*` for brokers, property you add by hand), `Liabilities` (cards,
   loans), `Income`, `Expenses`, `Equity`. Expense *categories* are accounts in a two-level
   household hierarchy (e.g. `Expenses:Food:Groceries`); every transaction has exactly one
   category, so totals add up. *Tags* (`#trip-izmir`) are optional and cross-cutting.
3. **Imports** (`qp books import FILE --source NAME`): CSV, XLSX and XLS statements read
   through a per-source column mapping in `data/books/sources.yaml` (date, description,
   amount or debit/credit, decimal comma, header row). Formats are confirmed from a sample
   of each institution before a preset is marked verified. Each imported transaction carries
   `import_id` (hash of source, date, amount, description, balance and order); importing an
   overlapping statement adds nothing twice. qp writes them to `journal/YYYY-MM.beancount` (qp-managed files).
4. **Categorization rules** in `data/books/rules.yaml`: first match on description (Turkish
   case-folded) and optional amount range sets the category, payee and tags. Unmatched
   spending goes to `Expenses:Uncategorized` (income to `Income:Uncategorized`) and appears
   in `qp books review`. Transfers between own accounts are matched across sources (same
   amount, opposite sign, within 3 days) and recorded once.
5. **Statements in TRY** *(default)*: amounts in other currencies are converted at the TCMB
   rate of the transaction date (income statement, cash flow) or of the report date
   (balance sheet), from qp's market data, point in time. Missing rates are reported, never
   guessed.
   - Income statement: income and expenses by category, savings and savings rate.
   - Balance sheet: assets and liabilities by account, net worth. Brokers are shown at
     the net amount transferred; valuing them with qp's portfolio (linked by `qp_account`
     metadata) once their ledgers are imported is *not built yet*.
   - Cash flow statement (direct method) on cash accounts (`Assets:Bank`, `Assets:Wallet`,
     `Assets:Cash`): operating (income, expenses, credit-card payments), investing
     (`Assets:Investments`, property), financing (loans), transfers between own cash
     accounts net to zero.
6. **Budgets** as `custom "budget"` entries (Fava's format, so the files stay standard) in
   `data/books/budgets.beancount`, per category or category group, with a period of
   `daily`, `weekly`, `monthly`, `quarterly` or `yearly`. A budget is pro-rated to any
   report window by days (200 TRY daily = 6,200 TRY in a 31-day month); a newer entry for
   the same account replaces the older one from its date. Alerts at 80% and 100%
   *(default)*, Telegram if configured.
7. **Layers**: `qp.books` sits beside `qp.ledger` (may import core, platform, reference,
   data); combining books with portfolio values (net worth) happens in `qp.reporting`.
8. **User interface**: a web app in `apps/web` (Next.js, React, Tailwind, shadcn/ui), an
   installable PWA for the phone with a quick "add expense" form, over a small JSON API in
   `apps/api` (FastAPI) that calls `qp.books` only. All money logic stays in tested
   Python; the web app displays and submits forms. The API listens on localhost by default;
   phone access goes through a private network (Tailscale), never the public internet.
   Fava is not used.

## Consequences

- The web app is the place to add, review and browse; the Streamlit terminal keeps the
  investment pages.
- Beancount is GPL-2.0: fine for a personal, undistributed tool.
- Bank formats change without notice; a changed header is an import error with the
  expected and found columns, never a silent misread.
