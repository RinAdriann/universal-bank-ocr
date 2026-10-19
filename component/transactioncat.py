from .constants import CATEGORY_RES

def category(description: str, debit: float | None, credit: float | None) -> str:
    if not description:
        return "Uncategorized"
    upper = description.upper()
    for name, pattern in CATEGORY_RES:
        if pattern.search(upper):
            return name
    return "Transfer Out / Debit" if (debit or 0) > 0 else "Transfer In / Credit" if (credit or 0) > 0 else "Uncategorized"

def flow(debit: float | None, credit: float | None) -> str:
    has_d, has_c = bool(debit and debit > 0), bool(credit and credit > 0)
    return "Both" if has_d and has_c else "Money in" if has_c else "Money out" if has_d else "Unknown"

__all__ = ["category", "flow"]