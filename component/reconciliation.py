"""Balance reconciliation API.

Kept separate from UI and file-format concerns.  The extractor owns the
ordering and layout context needed to call this reliably.
"""

from .extractor import _reconcile


def reconcile(rows: list, opening_hint: float | None = None) -> dict:
    """Reconcile extracted rows and annotate their debit/credit direction."""
    return _reconcile(rows, opening_hint)


__all__ = ["reconcile"]
