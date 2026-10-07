"""Transaction categorization API."""

from .constants import _CATEGORY_RES

def category(description: str, debit: float | None, credit: float | None) -> str:
    if not description:
        return "Uncategorized"
    for name, pattern in _CATEGORY_RES:
        if pattern.search(description.upper()):
            return name
    return "Transfer Out / Debit" if (debit or 0) > 0 else "Transfer In / Credit" if (credit or 0) > 0 else "Uncategorized"

def flow(debit: float | None, credit: float | None) -> str:
    if credit is not None and credit > 0 and not (debit and debit > 0):
        return "Money in"
    if debit is not None and debit > 0 and not (credit and credit > 0):
        return "Money out"
    if (debit or 0) > 0 and (credit or 0) > 0:
        return "Both"
    return "Unknown"

__all__ = ["category", "flow"]
