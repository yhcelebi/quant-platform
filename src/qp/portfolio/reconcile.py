"""Reconcile ledger holdings against a broker statement snapshot.

Statement CSV columns: ``account,instrument,quantity`` for positions and
``account,currency,amount`` for cash (one file may contain both; leave unused columns
empty). Every difference is reported; nothing is adjusted automatically.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import Decimal

from qp.core import Currency, InstrumentId, QPError, to_decimal
from qp.ledger.engine import LedgerState

COLUMNS = ("account", "instrument", "quantity", "currency", "amount")


@dataclass(frozen=True, slots=True)
class Mismatch:
    account: str
    item: str  # instrument id or currency code
    ledger: Decimal
    statement: Decimal

    @property
    def difference(self) -> Decimal:
        return self.ledger - self.statement


def parse_statement(
    text: str,
) -> tuple[dict[tuple[str, InstrumentId], Decimal], dict[tuple[str, Currency], Decimal]]:
    positions: dict[tuple[str, InstrumentId], Decimal] = {}
    cash: dict[tuple[str, Currency], Decimal] = {}
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = set(reader.fieldnames or [])
    if not header <= set(COLUMNS) or "account" not in header:
        raise QPError(f"statement header must use {list(COLUMNS)}")
    for line, raw in enumerate(reader, start=2):
        if None in raw or None in raw.values():
            raise QPError(f"statement line {line}: field count differs from the header")
        row = {k: v.strip() for k, v in raw.items()}
        try:
            if row.get("instrument"):
                key = (row["account"], InstrumentId.parse(row["instrument"]))
                positions[key] = positions.get(key, Decimal(0)) + to_decimal(row["quantity"])
            elif row.get("currency"):
                ckey = (row["account"], Currency(row["currency"]))
                cash[ckey] = cash.get(ckey, Decimal(0)) + to_decimal(row["amount"])
            else:
                raise QPError("row needs instrument+quantity or currency+amount")
        except (QPError, ValueError, KeyError) as exc:
            raise QPError(f"statement line {line}: {exc}") from exc
    return positions, cash


def reconcile(
    state: LedgerState, statement_text: str, *, include_cash: bool = True
) -> list[Mismatch]:
    """Compare ledger state with the statement for the accounts the statement mentions."""
    positions, cash = parse_statement(statement_text)
    accounts = {a for a, _ in positions} | {a for a, _ in cash}
    ledger_pos = {
        (p.account, p.instrument): p.quantity
        for p in state.open_positions()
        if p.account in accounts
    }
    out: list[Mismatch] = []
    for key in sorted(set(ledger_pos) | set(positions), key=lambda k: (k[0], str(k[1]))):
        mine, theirs = ledger_pos.get(key, Decimal(0)), positions.get(key, Decimal(0))
        if mine != theirs:
            out.append(Mismatch(key[0], str(key[1]), mine, theirs))
    if include_cash and cash:
        ledger_cash = {
            k: v for k, v in state.cash_balances().items() if k[0] in {a for a, _ in cash}
        }
        for ckey in sorted(set(ledger_cash) | set(cash), key=lambda k: (k[0], k[1].code)):
            mine, theirs = ledger_cash.get(ckey, Decimal(0)), cash.get(ckey, Decimal(0))
            if mine != theirs:
                out.append(Mismatch(ckey[0], ckey[1].code, mine, theirs))
    return out
