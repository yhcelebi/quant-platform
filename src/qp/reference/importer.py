"""Instrument master import from CSV: ``id,asset_class,currency,name,isin``.

Existing identical instruments are skipped; an existing instrument with different
attributes is an error (reference data is not silently overwritten).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from qp.core import Currency, InstrumentId, QPError
from qp.reference.domain import AssetClass, Instrument

COLUMNS = ("id", "asset_class", "currency", "name", "isin")
REQUIRED = ("id", "asset_class", "currency")


@dataclass
class InstrumentImportPlan:
    new: list[Instrument] = field(default_factory=list)
    unchanged: list[Instrument] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def plan_instrument_import(
    text: str, existing: dict[InstrumentId, Instrument], venues: set[str]
) -> InstrumentImportPlan:
    plan = InstrumentImportPlan()
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = set(reader.fieldnames or [])
    unknown = header - set(COLUMNS)
    missing = set(REQUIRED) - header
    if unknown or missing:
        plan.errors.append(f"header: unknown columns {sorted(unknown)}, missing {sorted(missing)}")
        return plan
    seen: set[InstrumentId] = set()
    for line, raw in enumerate(reader, start=2):
        try:
            row = {k: (v or "").strip() for k, v in raw.items()}
            iid = InstrumentId.parse(row["id"])
            inst = Instrument(
                id=iid,
                currency=Currency(row["currency"]),
                asset_class=AssetClass(row["asset_class"]),
                name=row.get("name", ""),
                isin=row.get("isin") or None,
            )
        except (QPError, ValueError, KeyError) as exc:
            plan.errors.append(f"line {line}: {exc}")
            continue
        if iid.venue not in venues:
            plan.errors.append(f"line {line}: unknown venue {iid.venue}")
        elif iid in seen:
            plan.errors.append(f"line {line}: {iid} appears twice")
        elif iid in existing:
            if existing[iid] == inst:
                plan.unchanged.append(inst)
            else:
                plan.errors.append(f"line {line}: {iid} exists with different attributes")
        else:
            plan.new.append(inst)
        seen.add(iid)
    return plan
