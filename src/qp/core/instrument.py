"""InstrumentId: canonical ``SYMBOL.VENUE`` identifiers (THYAO.BIST, AAPL.NASDAQ).

Provider symbols such as ``THYAO.IS`` are aliases stored in ref.instrument_aliases; they are
never InstrumentIds. Parsing splits on the *last* dot, so symbols may contain dots
(``BRK.B.NYSE`` -> symbol ``BRK.B``, venue ``NYSE``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from qp.core.errors import QPValidationError

_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._\-]*$")
_VENUE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


@dataclass(frozen=True, slots=True, order=True)
class InstrumentId:
    symbol: str
    venue: str

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not _SYMBOL_RE.fullmatch(self.symbol):
            raise QPValidationError(f"invalid instrument symbol: {self.symbol!r}")
        if self.symbol.endswith("."):
            raise QPValidationError(f"invalid instrument symbol: {self.symbol!r}")
        if not isinstance(self.venue, str) or not _VENUE_RE.fullmatch(self.venue):
            raise QPValidationError(f"invalid venue: {self.venue!r}")

    @classmethod
    def parse(cls, value: str) -> InstrumentId:
        """Parse ``SYMBOL.VENUE``. Case is not normalised: canonical IDs are upper case."""
        if not isinstance(value, str):
            raise TypeError("InstrumentId.parse expects a str")
        symbol, sep, venue = value.rpartition(".")
        if not sep:
            raise QPValidationError(f"instrument id must be SYMBOL.VENUE: {value!r}")
        return cls(symbol, venue)

    def __str__(self) -> str:
        return f"{self.symbol}.{self.venue}"
