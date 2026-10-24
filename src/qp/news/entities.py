"""Entity linking by dictionary (ADR 0012, 10). Every link keeps the matched text.

- Instruments: symbol and registered aliases as whole upper-case words (``THYAO``,
  ``THYAO.IS``), names as whole words after Turkish-aware folding.
- Currencies: ISO codes (whole upper-case words) and common names.
- Institutions: TCMB, Fed, ECB and their names.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from qp.news.text import fold, variants


@dataclass(frozen=True, slots=True)
class Entity:
    kind: str  # instrument | currency | institution
    key: str  # InstrumentId, ISO code, or institution code
    matched: str


@dataclass(frozen=True, slots=True)
class Pattern:
    kind: str
    key: str
    regex: re.Pattern[str]
    folded: bool  # match on folded text (names) or raw text (codes)


CURRENCY_NAMES: dict[str, tuple[str, ...]] = {
    "USD": ("dolar", "dollar"),
    "EUR": ("euro", "avro"),
    "GBP": ("sterlin", "sterling"),
    "TRY": ("türk lirası", "turkish lira"),
    "XAU": ("altın", "gold"),
}
INSTITUTIONS: dict[str, tuple[str, ...]] = {
    "TCMB": ("tcmb", "merkez bankası", "central bank of the republic of türkiye", "cbrt"),
    "FED": ("federal reserve", "fomc", "fed"),
    "ECB": ("ecb", "european central bank", "avrupa merkez bankası"),
}


def _word(text: str) -> str:
    return rf"(?<!\w){re.escape(text)}(?!\w)"


def build_patterns(
    instruments: Iterable[tuple[str, str, Sequence[str]]],
) -> list[Pattern]:
    """``instruments``: (InstrumentId string, name, aliases)."""
    out: list[Pattern] = []
    for inst, name, aliases in instruments:
        symbol = inst.rpartition(".")[0]
        codes = {symbol, *aliases}
        for code in sorted(c for c in codes if len(c) >= 3):
            out.append(Pattern("instrument", inst, re.compile(_word(code)), folded=False))
        if name and len(name) >= 4:
            out.append(Pattern("instrument", inst, re.compile(_word(fold(name))), folded=True))
    for code, names in CURRENCY_NAMES.items():
        out.append(Pattern("currency", code, re.compile(_word(code)), folded=False))
        out += [Pattern("currency", code, re.compile(_word(n)), folded=True) for n in names]
    for code, names in INSTITUTIONS.items():
        out += [Pattern("institution", code, re.compile(_word(n)), folded=True) for n in names]
    return out


def link(text: str, patterns: Sequence[Pattern]) -> list[Entity]:
    """Entities found in ``text``; one per (kind, key), with the first matched text."""
    forms = variants(text)
    found: dict[tuple[str, str], Entity] = {}
    for p in patterns:
        if (p.kind, p.key) in found:
            continue
        for candidate in forms if p.folded else (text,):
            m = p.regex.search(candidate)
            if m:
                found[(p.kind, p.key)] = Entity(p.kind, p.key, m.group(0))
                break
    return list(found.values())
