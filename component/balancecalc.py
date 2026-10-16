from typing import Any
from .structures import ExtractedRow
from .constants import OPENING_RE, _TOL
from .parser import _explicit_direction, _hint_direction

def _narrative(row: ExtractedRow) -> str:
    return " ".join(p for p in (row.description, row.reference, row.extra) if p)

def _delta_direction(prev: float | None, bal: float | None, amt: float | None) -> str | None:
    if prev is None or bal is None or amt is None:
        return None
    d = bal - prev
    return "credit" if abs(d - amt) < _TOL else "debit" if abs(d + amt) < _TOL else None

def _choose_direction(prev: float | None, bal: float | None, amt: float, explicit: str | None, desc: str) -> tuple[str | None, str]:
    by_bal = _delta_direction(prev, bal, amt)
    by_hint = _hint_direction(desc)
    if explicit:
        return explicit, "marker"
    if by_hint:
        return by_hint, "description"
    if by_bal:
        return by_bal, "balance_delta"
    return None, "unknown"

def _signed(row: ExtractedRow) -> float | None:
    return ((row.credit or 0.0) - (row.debit or 0.0)) if (row.debit or row.credit) else None

def _magnitude(row: ExtractedRow) -> float:
    s = _signed(row)
    return abs(s) if s else (row.pending_mutasi or 0.0)

def _chain_matches(seq: list[ExtractedRow]) -> tuple[int, int, int]:
    mag_ok = ok = inv = 0
    for a, b in zip(seq, seq[1:]):
        if a.balance is None or b.balance is None or not (mag := _magnitude(b)):
            continue
        d = b.balance - a.balance
        if abs(abs(d) - mag) < _TOL:
            mag_ok += 1
            if (s := _signed(b)) is not None:
                ok += abs(d - s) < _TOL
                inv += abs(d + s) < _TOL
    return mag_ok, ok, inv

def reconcile(rows: list[ExtractedRow], opening_hint: float | None = None) -> dict[str, Any]:
    info: dict[str, Any] = {"order": "as_printed", "debit_credit_swapped": False}
    for r in rows:
        if not r.is_opening and OPENING_RE.match(r.description or "") and not (r.debit or r.credit or r.pending_mutasi) and r.balance is not None:
            r.is_opening = True
    openings = [r for r in rows if r.is_opening]
    body = [r for r in rows if not r.is_opening]

    if _chain_matches(openings[:1] + body[::-1])[0] > _chain_matches(openings[:1] + body)[0]:
        body = body[::-1]
        rows[:] = openings[:1] + body
        info["order"] = "newest_first"

    _, ok, inv = _chain_matches(openings[:1] + body)
    if inv >= 2 and inv > 2 * ok:
        for r in body:
            r.debit, r.credit = r.credit, r.debit
        info["debit_credit_swapped"] = True

    prev: float | None = openings[0].balance if openings else opening_hint
    if prev is None and body:
        first = body[0]
        fs = _signed(first)
        if fs is None and first.pending_mutasi is not None:
            d_hint = first.pending_dir or _hint_direction(_narrative(first))
            fs = first.pending_mutasi if d_hint == "credit" else -first.pending_mutasi if d_hint == "debit" else None
        if first.balance is not None and fs is not None:
            prev = opening_hint = first.balance - fs
            info["inferred_opening_balance"] = prev

    for row in body:
        nar = _narrative(row)
        explicit_hint = _explicit_direction(row.raw_text)
        nhint = explicit_hint or row.pending_dir or _hint_direction(nar)
        if nhint == "credit" and row.debit and not row.credit:
            row.credit, row.debit = row.debit, None
            row.validation.append("direction_corrected")
        elif nhint == "debit" and row.credit and not row.debit:
            row.debit, row.credit = row.credit, None
            row.validation.append("direction_corrected")
        elif nhint and row.balance is not None and not row.debit and not row.credit and not row.pending_mutasi and prev is not None:
            delta = row.balance - prev
            if abs(delta) >= _TOL:
                if nhint == "credit" and delta > 0:
                    row.credit = delta
                    row.validation.append("amount_derived")
                elif nhint == "debit" and delta < 0:
                    row.debit = -delta
                    row.validation.append("amount_derived")

        signed = _signed(row)
        if row.pending_mutasi is not None and signed is None:
            mag = row.pending_mutasi
            direction, evidence = _choose_direction(prev, row.balance, mag, nhint, nar)
            if direction is None:
                direction = "debit"
                row.validation.extend(["direction_guessed", "direction_unknown"])
            elif nhint and evidence == "balance_delta" and nhint != direction:
                row.validation.append("direction_corrected")
            setattr(row, "credit" if direction == "credit" else "debit", mag)
        elif signed is not None and row.balance is not None and prev is not None:
            d = row.balance - prev
            if abs(d - signed) >= _TOL:
                if explicit_hint is None and abs(d + signed) < _TOL:
                    row.debit, row.credit = row.credit, row.debit
                    row.validation.append("direction_corrected")
                else:
                    row.validation.append("balance_mismatch")
        elif signed is None and row.pending_mutasi is None and row.balance is not None and prev is not None:
            d = row.balance - prev
            if abs(d) >= _TOL:
                setattr(row, "credit" if d > 0 else "debit", abs(d))
                row.validation.append("amount_derived")

        if row.balance is None and prev is not None:
            row.balance = prev + (row.credit or 0.0) - (row.debit or 0.0)
            row.validation.append("saldo_calculated")
        if row.balance is not None:
            prev = row.balance

    penalties = {
        "balance_mismatch": 0.5, "amount_derived": 0.35, "saldo_calculated": 0.2,
        "direction_guessed": 0.3, "direction_corrected": 0.05, "date_invalid": 0.3,
        "multiple_amounts": 0.3, "multiple_balances": 0.3, "pending_transaction": 0.1,
    }
    for r in rows:
        r.confidence = max(0.0, 1.0 - sum(penalties.get(v, 0.2) for v in r.validation))
    if opening_hint is not None and not openings:
        info["opening_inferred"] = True
    return info

__all__ = ["reconcile"]