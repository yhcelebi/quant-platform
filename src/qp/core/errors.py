"""Exception hierarchy for qp. Every qp error derives from QPError."""


class QPError(Exception):
    """Base class for all qp errors."""


class QPValidationError(QPError, ValueError):
    """A value failed domain validation."""


class UnknownCurrencyError(QPValidationError):
    """A currency code is not a known ISO 4217 code."""


class CurrencyMismatchError(QPError, ValueError):
    """An operation mixed two currencies without an explicit FX conversion."""


class NaiveDatetimeError(QPError, ValueError):
    """A datetime without tzinfo was passed where an aware one is required (rule 4)."""


class FloatNotAllowedError(QPError, TypeError):
    """A float was passed where an exact Decimal is required (rule 3)."""


class ConfigError(QPError):
    """Configuration is missing or invalid."""
