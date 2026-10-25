"""Statement formats (``sources.yaml``) and categorization rules (``rules.yaml``) of the
household books (ADR 0017, 3-4)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from qp.core import QPValidationError

_TURKISH = str.maketrans(
    {
        "ı": "i",
        "ş": "s",
        "ğ": "g",
        "ü": "u",
        "ö": "o",
        "ç": "c",
        "â": "a",
        "î": "i",
        "û": "u",
        "Ş": "s",
        "Ğ": "g",
        "Ü": "u",
        "Ö": "o",
        "Ç": "c",
        "Â": "a",
        "Î": "i",
        "Û": "u",
    }
)


def fold(text: str) -> str:
    """Case- and Turkish-letter-insensitive form: MİGROS, Migros, MIGROS -> migros;
    Şok -> sok. Whitespace collapsed."""
    t = text.replace("İ", "i").replace("I", "i").translate(_TURKISH).lower()
    t = t.replace("i̇", "i")  # a dotted i left by lower() of combining forms
    return " ".join(t.split())


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    account: str
    currency: str
    verified: bool = False
    decimal: str = ","
    header_contains: tuple[str, ...] = ()
    sheet: str | int | None = None
    date: str | None = None
    date_format: str = "%d.%m.%Y"
    description: tuple[str, ...] = ()
    amount: str | None = None
    debit: str | None = None
    credit: str | None = None
    fee: str | None = None
    balance: str | None = None
    skip_if: str | None = None
    state_column: str | None = None
    state_ok: tuple[str, ...] = ()

    @property
    def configured(self) -> bool:
        return bool(
            self.date and self.description and (self.amount or (self.debit and self.credit))
        )

    def columns(self) -> list[str]:
        cols = [self.date or "", *self.description]
        cols += [c for c in (self.amount, self.debit, self.credit, self.fee, self.balance) if c]
        if self.state_column:
            cols.append(self.state_column)
        return [c for c in cols if c]


def _tuple(x: Any) -> tuple[str, ...]:
    if x is None:
        return ()
    if isinstance(x, str):
        return (x,)
    return tuple(str(v) for v in x)


def load_sources(path: Path) -> dict[str, Source]:
    if not path.is_file():
        raise QPValidationError(f"{path} not found: run `qp books init` first")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, Source] = {}
    for name, raw in (data.get("sources") or {}).items():
        if not isinstance(raw, dict) or "account" not in raw or "currency" not in raw:
            raise QPValidationError(f"source {name}: needs account and currency")
        decimal = str(raw.get("decimal", ","))
        if decimal not in (",", "."):
            raise QPValidationError(f"source {name}: decimal must be ',' or '.'")
        out[str(name)] = Source(
            name=str(name),
            account=str(raw["account"]),
            currency=str(raw["currency"]).upper(),
            verified=bool(raw.get("verified", False)),
            decimal=decimal,
            header_contains=_tuple(raw.get("header_contains")),
            sheet=raw.get("sheet"),
            date=raw.get("date"),
            date_format=str(raw.get("date_format", "%d.%m.%Y")),
            description=_tuple(raw.get("description")),
            amount=raw.get("amount"),
            debit=raw.get("debit"),
            credit=raw.get("credit"),
            fee=raw.get("fee"),
            balance=raw.get("balance"),
            skip_if=raw.get("skip_if"),
            state_column=raw.get("state_column"),
            state_ok=_tuple(raw.get("state_ok")),
        )
    return out


@dataclass(frozen=True, slots=True)
class Rule:
    pattern: re.Pattern[str]
    account: str
    payee: str | None = None
    tags: tuple[str, ...] = ()
    source: str | None = None
    sign: str | None = None  # "in" | "out"
    min: Decimal | None = None
    max: Decimal | None = None
    text: str = ""

    def matches(self, source: str, folded: str, amount: Decimal) -> bool:
        if self.source is not None and self.source != source:
            return False
        if self.sign == "out" and amount >= 0:
            return False
        if self.sign == "in" and amount <= 0:
            return False
        if self.min is not None and abs(amount) < self.min:
            return False
        if self.max is not None and abs(amount) > self.max:
            return False
        return self.pattern.search(folded) is not None


@dataclass
class Rules:
    rules: list[Rule] = field(default_factory=list)

    def match(self, source: str, description: str, amount: Decimal) -> Rule | None:
        folded = fold(description)
        return next((r for r in self.rules if r.matches(source, folded, amount)), None)


def load_rules(path: Path) -> Rules:
    if not path.is_file():
        return Rules()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[Rule] = []
    for i, raw in enumerate(data.get("rules") or [], start=1):
        if not isinstance(raw, dict) or "match" not in raw or "account" not in raw:
            raise QPValidationError(f"rule {i}: needs match and account")
        text = str(raw["match"])
        try:
            pattern = re.compile(text.translate(_TURKISH))
        except re.error as exc:
            raise QPValidationError(f"rule {i} ({text!r}): {exc}") from None
        sign = raw.get("sign")
        if sign not in (None, "in", "out"):
            raise QPValidationError(f"rule {i}: sign must be in or out")
        out.append(
            Rule(
                pattern,
                str(raw["account"]),
                raw.get("payee"),
                _tuple(raw.get("tags")),
                raw.get("source"),
                sign,
                Decimal(str(raw["min"])) if raw.get("min") is not None else None,
                Decimal(str(raw["max"])) if raw.get("max") is not None else None,
                text,
            )
        )
    return Rules(out)


def rule_pattern(description: str, words: int = 2) -> str:
    """A rule pattern from a bank description: its first meaningful words, folded, escaped
    ('MIGROS SANAL MAGAZA 1234 ISTANBUL' -> 'migros sanal')."""
    tokens = [w for w in re.split(r"[^a-z]+", fold(description)) if len(w) > 1]
    if not tokens:
        raise QPValidationError(f"no words to learn a rule from in {description!r}")
    return " ".join(tokens[:words])  # letters only: nothing to escape


def add_rule(path: Path, pattern: str, account: str, payee: str | None = None) -> None:
    """Put a learned rule first, so it wins over the general ones below it."""
    text = path.read_text(encoding="utf-8")
    marker = "rules:\n"
    if marker not in text:
        raise QPValidationError(f"{path}: no 'rules:' list")

    def q(x: str) -> str:  # YAML single quotes: no escape sequences, '' for a quote
        return "'" + x.replace("'", "''") + "'"

    fields = f"match: {q(pattern)}, account: {q(account)}" + (
        f", payee: {q(payee)}" if payee else ""
    )
    line = f"  - {{{fields}}}  # learned\n"
    path.write_text(text.replace(marker, marker + line, 1), encoding="utf-8")
    load_rules(path)  # still valid
