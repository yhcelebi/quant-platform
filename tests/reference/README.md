# tests/reference — OWNER-OWNED

The files in this directory are owned by the platform owner. They define what "correct"
means for the ledger, portfolio and performance calculations.

**Agents and contributors must never edit, delete, rename or regenerate anything in this
directory.** This includes "fixing" a number, reformatting, or adding cases.

If a case looks wrong:

1. Stop.
2. Show your own step-by-step calculation, with the inputs and conventions from
   `docs/DOMAIN.md` that you used.
3. Wait for the owner to decide. Only the owner changes these files.

Tests that load these cases live elsewhere (for example `tests/unit/` or a loader in
`tests/conftest.py`); they read this directory and never write to it.

## Files

- `ledger_cases.yaml` — cases a–f: weighted-average cost basis, bonus issue, cash dividend
  with withholding, FX/price P&L split, TWR and XIRR, real return.
