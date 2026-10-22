from .constants import CATEGORY_RES


def category(description: str, debit: float | None, credit: float | None) -> str:
    if not description:
        return "Uncategorized"

    text = description.upper()
    for name, pattern in CATEGORY_RES:
        if pattern.search(text):
            return name

    if debit and debit > 0:
        return "Transfer Out / Debit"
    if credit and credit > 0:
        return "Transfer In / Credit"
    return "Uncategorized"


def flow(debit: float | None, credit: float | None) -> str:
    has_debit = bool(debit and debit > 0)
    has_credit = bool(credit and credit > 0)
    if has_debit and has_credit:
        return "Both"
    if has_credit:
        return "Money in"
    if has_debit:
        return "Money out"
    return "Unknown"


__all__ = ["category", "flow"]
