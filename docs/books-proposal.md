# Proposal — household books (budget, cash flow, statements)

Status: accepted by the owner 2026-10-25 ("go with this module"); ADR 0017. Built before the
remaining phase work because it is the owner's first goal.

## Goal

Track every lira in and out of Kuveyt Türk, Vakıf Katılım, Akbank, Papara and Revolut with
little effort, against a monthly budget, and read a balance sheet, income statement and cash
flow statement for the household. Start: October 2026.

## Exit criteria

1. `qp books init` creates `data/books/` (accounts, categories, budgets, rules, sources)
   with its own local git history.
2. Imports from CSV/XLSX/XLS by column mapping; duplicates skipped; transfers between own
   accounts recorded once; rules categorize, the rest goes to a review list.
3. Income statement, balance sheet and cash flow statement in TRY that reconcile
   (opening cash + net cash flow = closing cash; assets - liabilities = equity + retained
   income), tested on a hand-calculated example month.
4. Budgets per category per month, with actual vs budget and alerts.
5. `qp books ...` commands; JSON API (`apps/api`) and a shadcn/ui web app (`apps/web`, PWA
   for the phone); runbook.
6. Each institution's preset verified against a real export (needs the owner's samples).
