from typing import Any
from .structures import ExtractedRow
from .constants import OPENING_RE, _TOL
from .parser import _explicit_direction, _hint_direction


def _narrative(row: ExtractedRow) -> str:
    return " ".join(
        part for part in (row.description, row.reference, row.extra)
        if part
    )

def _delta_direction(previous: float | None, balance: float | None, amount: float | None) -> str | None:
    if previous is None or balance is None or amount is None:
        return None
    delta = balance - previous
    if abs(delta - amount) < _TOL:
        return "credit"
    if abs(delta + amount) < _TOL:
        return "debit"
    return None

def _choose_direction(
    previous: float | None, balance: float | None, amount: float, explicit: str | None, description: str
) -> tuple[str | None, str]:
    by_balance = _delta_direction(previous, balance, amount)
    by_hint = _hint_direction(description)
    if explicit:
        return explicit, "marker"
    if by_hint:
        return by_hint, "description"
    if by_balance:
        return by_balance, "balance_delta"
    return None, "unknown"

def _signed(row: ExtractedRow) -> float | None:
    if row.debit or row.credit:
        return (row.credit or 0.0) - (row.debit or 0.0)
    return None

def _magnitude(row: ExtractedRow) -> float:
    s = _signed(row)
    return abs(s) if s else (row.pending_mutasi or 0.0)

def _chain_matches(seq: list[ExtractedRow]) -> tuple[int, int, int]:
    mag_ok = ok = inv = 0
    for a, b in zip(seq, seq[1:]):
        if a.balance is None or b.balance is None:
            continue
        mag = _magnitude(b)
        if not mag:
            continue
        d = b.balance - a.balance
        if abs(abs(d) - mag) < _TOL:
            mag_ok += 1
            s = _signed(b)
            if s is not None:
                if abs(d - s) < _TOL:
                    ok += 1
                elif abs(d + s) < _TOL:
                    inv += 1
    return mag_ok, ok, inv

def reconcile(rows: list[ExtractedRow], opening_hint: float | None = None) -> dict[str, Any]:
    info: dict[str, Any] = {"order": "as_printed", "debit_credit_swapped": False}
    for r in rows:
        if not r.is_opening and OPENING_RE.match(r.description or "") and not (r.debit or r.credit or r.pending_mutasi) and r.balance is not None:
            r.is_opening = True
    openings = [r for r in rows if r.is_opening]
    body = [r for r in rows if not r.is_opening]

    fwd = _chain_matches(openings[:1] + body)
    rev = _chain_matches(openings[:1] + body[::-1])
    seq_body = body
    if rev[0] > fwd[0]:
        seq_body = body[::-1]
        info["order"] = "newest_first"
    seq = openings[:1] + seq_body

    _, ok, inv = _chain_matches(seq)
    if inv >= 2 and inv > 2 * ok:
        for r in body:
            r.debit, r.credit = r.credit, r.debit
        info["debit_credit_swapped"] = True

    prev: float | None = openings[0].balance if openings else opening_hint
    if prev is None and seq_body:
        first = seq_body[0]
        first_signed = _signed(first)
        if first_signed is None and first.pending_mutasi is not None:
            direction = first.pending_dir or _hint_direction(_narrative(first))
            if direction == "credit":
                first_signed = first.pending_mutasi
            elif direction == "debit":
                first_signed = -first.pending_mutasi
        if first.balance is not None and first_signed is not None:
            prev = first.balance - first_signed
            opening_hint = prev
            info["inferred_opening_balance"] = prev

    for row in seq_body:
        explicit_hint = _explicit_direction(row.raw_text)
        narrative_hint = explicit_hint or row.pending_dir or _hint_direction(_narrative(row))
        if narrative_hint == "credit" and row.debit and not row.credit:
            row.credit, row.debit = row.debit, None
            row.validation.append("direction_corrected")
        elif narrative_hint == "debit" and row.credit and not row.debit:
            row.debit, row.credit = row.credit, None
            row.validation.append("direction_corrected")
        elif (
            narrative_hint
            and row.balance is not None
            and not row.debit
            and not row.credit
            and not row.pending_mutasi
            and prev is not None
        ):
            delta = row.balance - prev
            if abs(delta) >= _TOL:
                if narrative_hint == "credit" and delta > 0:
                    row.credit = delta
                    row.validation.append("amount_derived")
                elif narrative_hint == "debit" and delta < 0:
                    row.debit = -delta
                    row.validation.append("amount_derived")
        signed = _signed(row)
        if row.pending_mutasi is not None and signed is None:
            mag = row.pending_mutasi
            hinted = narrative_hint
            direction, evidence = _choose_direction(
                prev, row.balance, mag, hinted, _narrative(row)
            )
            if direction is None:
                direction = "debit"
                row.validation.extend(["direction_guessed", "direction_unknown"])
            elif hinted and evidence == "balance_delta" and hinted != direction:
                row.validation.append("direction_corrected")
            if direction == "credit":
                row.credit = mag
            else:
                row.debit = mag
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
                if d > 0:
                    row.credit = d
                else:
                    row.debit = -d
                row.validation.append("amount_derived")
        if row.balance is None and prev is not None:
            row.balance = prev + (row.credit or 0.0) - (row.debit or 0.0)
            row.validation.append("saldo_calculated")
        if row.balance is not None:
            prev = row.balance

    penalties = {"balance_mismatch": 0.5, "amount_derived": 0.35, "saldo_calculated": 0.2, "direction_guessed": 0.3,
                "direction_corrected": 0.05, "date_invalid": 0.3, "multiple_amounts": 0.3, "multiple_balances": 0.3,
                "pending_transaction": 0.1}
    for r in rows:
        r.confidence = max(0.0, 1.0 - sum(penalties.get(v, 0.2) for v in r.validation))
    if opening_hint is not None and not openings:
        info["opening_inferred"] = True
    return info

__all__ = ["reconcile"]
