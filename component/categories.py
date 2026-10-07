"""Transaction categorization API."""

from .extractor import _category


def category(description: str, debit: float | None, credit: float | None) -> str:
    """Return the normalized category for one extracted transaction."""
    return _category(description, debit, credit)


def flow(debit: float | None, credit: float | None) -> str:
    """Return a human-readable cash-flow direction for the UI."""
    if credit is not None and credit > 0 and not (debit and debit > 0):
        return "Money in"
    if debit is not None and debit > 0 and not (credit and credit > 0):
        return "Money out"
    if (debit or 0) > 0 and (credit or 0) > 0:
        return "Both"
    return "Unknown"


__all__ = ["category", "flow"]
