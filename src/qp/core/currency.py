"""ISO 4217 currencies.

Only codes in ``_MINOR_UNITS`` are accepted. Add codes here (with their ISO 4217 minor units)
when a new currency is actually needed; do not accept arbitrary three-letter strings.
Crypto assets are instruments, not currencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from qp.core.errors import UnknownCurrencyError

# ISO 4217 code -> number of minor-unit digits.
_MINOR_UNITS: dict[str, int] = {
    "AED": 2,
    "AUD": 2,
    "BHD": 3,
    "CAD": 2,
    "CHF": 2,
    "CNY": 2,
    "CZK": 2,
    "DKK": 2,
    "EUR": 2,
    "GBP": 2,
    "HKD": 2,
    "HUF": 2,
    "JPY": 0,
    "KRW": 0,
    "KWD": 3,
    "NOK": 2,
    "NZD": 2,
    "PLN": 2,
    "QAR": 2,
    "RUB": 2,
    "SAR": 2,
    "SEK": 2,
    "SGD": 2,
    "TRY": 2,
    "USD": 2,
    "ZAR": 2,
}


@dataclass(frozen=True, slots=True)
class Currency:
    """An ISO 4217 currency, e.g. ``Currency("TRY")``."""

    code: str

    known_codes: ClassVar[frozenset[str]] = frozenset(_MINOR_UNITS)

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or self.code not in _MINOR_UNITS:
            raise UnknownCurrencyError(f"unknown ISO 4217 currency code: {self.code!r}")

    @property
    def minor_units(self) -> int:
        """Number of decimal places of the currency's minor unit (TRY: 2, JPY: 0)."""
        return _MINOR_UNITS[self.code]

    def __str__(self) -> str:
        return self.code


TRY = Currency("TRY")
USD = Currency("USD")
EUR = Currency("EUR")
