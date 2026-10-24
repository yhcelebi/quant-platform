"""Database access for reference data. Converts between ORM rows and domain types."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from qp.core import Currency, InstrumentId
from qp.reference.domain import (
    AssetClass,
    CorporateAction,
    CorporateActionType,
    Instrument,
    Venue,
)
from qp.reference.models import (
    CorporateActionRow,
    InstrumentAliasRow,
    InstrumentRow,
    VenueRow,
)


def _instrument(row: InstrumentRow) -> Instrument:
    return Instrument(
        id=InstrumentId.parse(row.id),
        currency=Currency(row.currency),
        asset_class=AssetClass(row.asset_class),
        name=row.name,
        isin=row.isin,
    )


def corporate_action_from_row(row: CorporateActionRow) -> CorporateAction:
    return CorporateAction(
        id=row.id,
        instrument=InstrumentId.parse(row.instrument_id),
        action_type=CorporateActionType(row.action_type),
        ex_date=row.ex_date,
        record_date=row.record_date,
        pay_date=row.pay_date,
        ratio=row.ratio,
        amount_per_share=row.amount_per_share,
        currency=Currency(row.currency) if row.currency else None,
        subscription_price=row.subscription_price,
        new_instrument=InstrumentId.parse(row.new_instrument_id) if row.new_instrument_id else None,
        source=row.source,
        known_time=row.known_time,
        note=row.note,
    )


class ReferenceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------- venues
    def venues(self) -> list[Venue]:
        rows = self.session.scalars(select(VenueRow).order_by(VenueRow.code))
        return [
            Venue(
                r.code, r.name, r.timezone, r.country, Currency(r.currency) if r.currency else None
            )
            for r in rows
        ]

    # ------------------------------------------------------------- instruments
    def instrument(self, instrument_id: InstrumentId) -> Instrument | None:
        row = self.session.get(InstrumentRow, str(instrument_id))
        return _instrument(row) if row is not None else None

    def instruments(self) -> list[Instrument]:
        rows = self.session.scalars(select(InstrumentRow).order_by(InstrumentRow.id))
        return [_instrument(r) for r in rows]

    def aliases(self) -> dict[InstrumentId, list[str]]:
        """Provider symbols per instrument (all providers, all validity ranges)."""
        out: dict[InstrumentId, list[str]] = {}
        rows = self.session.scalars(select(InstrumentAliasRow).order_by(InstrumentAliasRow.id))
        for r in rows:
            out.setdefault(InstrumentId.parse(r.instrument_id), []).append(r.provider_symbol)
        return out

    def add_instrument(self, inst: Instrument) -> None:
        self.session.add(
            InstrumentRow(
                id=str(inst.id),
                symbol=inst.id.symbol,
                venue_code=inst.id.venue,
                asset_class=inst.asset_class.value,
                currency=inst.currency.code,
                name=inst.name,
                isin=inst.isin,
            )
        )
        self.session.flush()

    # ------------------------------------------------------------- corporate actions
    def corporate_action(
        self, instrument: InstrumentId, action_type: CorporateActionType, ex_date: date
    ) -> CorporateAction | None:
        row = self.session.scalars(
            select(CorporateActionRow).where(
                CorporateActionRow.instrument_id == str(instrument),
                CorporateActionRow.action_type == action_type.value,
                CorporateActionRow.ex_date == ex_date,
            )
        ).one_or_none()
        return corporate_action_from_row(row) if row is not None else None

    def corporate_actions(self, instrument: InstrumentId | None = None) -> list[CorporateAction]:
        stmt = select(CorporateActionRow).order_by(
            CorporateActionRow.ex_date, CorporateActionRow.id
        )
        if instrument is not None:
            stmt = stmt.where(CorporateActionRow.instrument_id == str(instrument))
        return [corporate_action_from_row(r) for r in self.session.scalars(stmt)]

    def add_corporate_action(self, ca: CorporateAction) -> CorporateAction:
        """Insert and return the action with its id (append-only table)."""
        row = CorporateActionRow(
            instrument_id=str(ca.instrument),
            action_type=ca.action_type.value,
            ex_date=ca.ex_date,
            record_date=ca.record_date,
            pay_date=ca.pay_date,
            ratio=ca.ratio,
            amount_per_share=ca.amount_per_share,
            currency=ca.currency.code if ca.currency else None,
            subscription_price=ca.subscription_price,
            new_instrument_id=str(ca.new_instrument) if ca.new_instrument else None,
            source=ca.source,
            known_time=ca.known_time,
            note=ca.note,
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return corporate_action_from_row(row)
