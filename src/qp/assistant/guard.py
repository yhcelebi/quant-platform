"""Rule 12 guard (ADR 0016, 12): every number in an answer must come from the tool results
or the question. A number matches when it equals a source number to the precision it is
written with (display rounding), with or without its sign, as a percentage of a fraction,
or scaled by a written unit (thousand, million, billion). Small whole numbers (0-10) are
exempt: counts and ordinals ("2 holdings", "1-day")."""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

_SCALES = {
    "k": 3,
    "thousand": 3,
    "bin": 3,
    "m": 6,
    "mn": 6,
    "million": 6,
    "milyon": 6,
    "bn": 9,
    "billion": 9,
    "milyar": 9,
}
_NUMBER = re.compile(
    r"(?P<pre>%\s?)?"
    r"(?P<sign>[-+\u2212])?"
    r"(?P<num>\d{1,3}(?:[,.]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"(?P<post>\s?%)?"
    r"(?:\s?(?P<scale>k|thousand|bin|mn|m|million|milyon|bn|billion|milyar)\b)?",
    re.IGNORECASE,
)
_SOURCE_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass(frozen=True, slots=True)
class Written:
    text: str
    candidates: tuple[tuple[Decimal, Decimal], ...]  # (value, tolerance) readings
    exempt: bool


def _readings(num: str) -> list[tuple[Decimal, int]]:
    """(value, decimals shown) for each way of reading separators: 1,234.5 / 1.234,5 /
    1.234 (thousands or decimals) / 1,5 (Turkish decimal)."""
    out: list[tuple[Decimal, int]] = []
    seps = [c for c in num if c in ",."]
    if not seps:
        return [(Decimal(num), 0)]
    last = max(num.rfind(","), num.rfind("."))
    tail = len(num) - last - 1
    # last separator as the decimal point, the others as thousands separators
    try:
        whole = re.sub(r"[,.]", "", num[:last])
        out.append((Decimal(f"{whole}.{num[last + 1 :]}"), tail))
    except InvalidOperation:
        pass
    # every separator a thousands separator (only if groups are 3 digits)
    if re.fullmatch(r"\d{1,3}(?:[,.]\d{3})+", num):
        out.append((Decimal(re.sub(r"[,.]", "", num)), 0))
    return out


def written_numbers(text: str) -> list[Written]:
    found: list[Written] = []
    for m in _NUMBER.finditer(text):
        start = m.start("num")
        if start > 0 and (text[start - 1].isalpha() or text[start - 1] == "_"):
            continue  # part of an identifier: XU100, DGS10, TP.FG.J0
        percent = bool(m.group("pre") or m.group("post"))
        scale_word = (m.group("scale") or "").lower()
        exp = _SCALES.get(scale_word, 0)
        cands: list[tuple[Decimal, Decimal]] = []
        readings = _readings(m.group("num"))
        for value, places in readings:
            tol = Decimal(5) * Decimal(10) ** (-(places + 1)) + Decimal("1e-12")
            scaled = value.scaleb(exp)
            cands.append((scaled, tol.scaleb(exp)))
            if scale_word == "m":  # "m" may be a unit (minutes, metres), not million
                cands.append((value, tol))
            if percent:
                cands.append((value / 100, tol / 100))
        exempt = (
            not percent
            and not exp
            and len(readings) == 1
            and readings[0][1] == 0
            and readings[0][0] <= 10
        )
        found.append(Written(m.group(0).strip(), tuple(cands), exempt))
    return found


def source_numbers(data: Any) -> set[Decimal]:
    """Every number in tool results (values and digits inside strings)."""
    out: set[Decimal] = set()

    def walk(x: Any) -> None:
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float, Decimal)):
            with suppress(InvalidOperation):
                out.add(abs(Decimal(str(x))))
        elif isinstance(x, str):
            for n in _SOURCE_NUMBER.findall(x):
                out.add(abs(Decimal(n)))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    walk(data)
    return out


def unverified(answer: str, sources: Iterable[Any]) -> list[str]:
    """Numbers written in ``answer`` that no source supports, in order of appearance."""
    pool: set[Decimal] = set()
    for s in sources:
        pool |= source_numbers(s)
    ordered = sorted(pool)
    out: list[str] = []
    for w in written_numbers(answer):
        if w.exempt:
            continue
        if not any(_near(abs(v), tol, ordered) for v, tol in w.candidates):
            out.append(w.text)
    return out


def _near(value: Decimal, tol: Decimal, ordered: list[Decimal]) -> bool:
    i = bisect_left(ordered, value - tol)
    return i < len(ordered) and ordered[i] <= value + tol
