from typing import Any
from .constants import OPENING_RE, TOL
from .parser import explicit_direction, hint_direction
from .structures import ExtractedRow

PENALTIES = {
    "balance_mismatch": 0.5,
    "amount_derived": 0.35,
    "saldo_calculated": 0.2,
    "direction_guessed": 0.3,
    "direction_corrected": 0.05,
    "date_invalid": 0.3,
    "multiple_amounts": 0.3,
    "multiple_balances": 0.3,
    "pending_transaction": 0.1,
}


def narrative(row: ExtractedRow) -> str:
    return " ".join(p for p in (row.description, row.reference, row.extra) if p)


def delta_direction(prev: float | None, bal: float | None, amt: float | None) -> str | None:
    if prev is None or bal is None or amt is None:
        return None
    diff = bal - prev
    if abs(diff - amt) < TOL:
        return "credit"
    if abs(diff + amt) < TOL:
        return "debit"
    return None


def choose_direction(prev: float | None, bal: float | None, amt: float, explicit: str | None, desc: str) -> tuple[str | None, str]:
    by_bal = delta_direction(prev, bal, amt)
    by_hint = hint_direction(desc)
    if explicit:
        return explicit, "marker"
    if by_hint:
        return by_hint, "description"
    if by_bal:
        return by_bal, "balance_delta"
    return None, "unknown"


def signed_amount(row: ExtractedRow) -> float | None:
    if not (row.debit or row.credit):
        return None
    return (row.credit or 0.0) - (row.debit or 0.0)


def magnitude(row: ExtractedRow) -> float:
    signed = signed_amount(row)
    return abs(signed) if signed else (row.pending_mutasi or 0.0)


def chain_matches(seq: list[ExtractedRow]) -> tuple[int, int, int]:
    mag_ok = ok = inv = 0
    for a, b in zip(seq, seq[1:]):
        mag = magnitude(b)
        if a.balance is None or b.balance is None or not mag:
            continue
        diff = b.balance - a.balance
        if abs(abs(diff) - mag) < TOL:
            mag_ok += 1
            signed = signed_amount(b)
            if signed is not None:
                ok += abs(diff - signed) < TOL
                inv += abs(diff + signed) < TOL
    return mag_ok, ok, inv


def reconcile(rows: list[ExtractedRow], opening_hint: float | None = None) -> dict[str, Any]:
    info: dict[str, Any] = {"order": "as_printed", "debit_credit_swapped": False}
    for row in rows:
        if not row.is_opening and OPENING_RE.match(row.description or "") and not (row.debit or row.credit or row.pending_mutasi) and row.balance is not None:
            row.is_opening = True
    openings = [r for r in rows if r.is_opening]
    body = [r for r in rows if not r.is_opening]

    if chain_matches(openings[:1] + body[::-1])[0] > chain_matches(openings[:1] + body)[0]:
        body = body[::-1]
        rows[:] = openings[:1] + body
        info["order"] = "newest_first"

    _, ok, inv = chain_matches(openings[:1] + body)
    if inv >= 2 and inv > 2 * ok:
        for row in body:
            row.debit, row.credit = row.credit, row.debit
        info["debit_credit_swapped"] = True

    prev: float | None = openings[0].balance if openings else opening_hint
    if prev is None and body:
        first = body[0]
        first_signed = signed_amount(first)
        if first_signed is None and first.pending_mutasi is not None:
            dir_hint = first.pending_dir or hint_direction(narrative(first))
            if dir_hint == "credit":
                first_signed = first.pending_mutasi
            elif dir_hint == "debit":
                first_signed = -first.pending_mutasi
        if first.balance is not None and first_signed is not None:
            prev = opening_hint = first.balance - first_signed
            info["inferred_opening_balance"] = prev

    for idx, row in enumerate(body):
        nar = narrative(row)
        explicit_hint = explicit_direction(row.raw_text)
        dir_hint = explicit_hint or row.pending_dir or hint_direction(nar)
        if dir_hint == "credit" and row.debit and not row.credit:
            row.credit, row.debit = row.debit, None
            row.validation.append("direction_corrected")
        elif dir_hint == "debit" and row.credit and not row.debit:
            row.debit, row.credit = row.credit, None
            row.validation.append("direction_corrected")
        elif dir_hint and row.balance is not None and not row.debit and not row.credit and not row.pending_mutasi and prev is not None:
            delta = row.balance - prev
            if abs(delta) >= TOL:
                if dir_hint == "credit" and delta > 0:
                    row.credit = delta
                    row.validation.append("amount_derived")
                elif dir_hint == "debit" and delta < 0:
                    row.debit = -delta
                    row.validation.append("amount_derived")

        signed = signed_amount(row)
        if row.pending_mutasi is not None and signed is None:
            mag = row.pending_mutasi
            direction, evidence = choose_direction(prev, row.balance, mag, dir_hint, nar)
            if direction is None:
                direction = "debit"
                row.validation.extend(["direction_guessed", "direction_unknown"])
            elif dir_hint and evidence == "balance_delta" and dir_hint != direction:
                row.validation.append("direction_corrected")
            setattr(row, "credit" if direction == "credit" else "debit", mag)
        elif signed is not None and row.balance is not None and prev is not None:
            diff = row.balance - prev
            nxt = body[idx + 1] if idx + 1 < len(body) else None
            nxt_s = signed_amount(nxt) if nxt else None
            if abs(diff - signed) >= TOL:
                scaled = signed / 1000.0
                bridges_scaled = (
                    nxt is not None
                    and nxt.balance is not None
                    and nxt_s is not None
                    and (
                        abs(prev + scaled + nxt_s - nxt.balance) < TOL
                        or abs(prev + scaled + nxt_s / 1000.0 - nxt.balance) < TOL
                    )
                )
                if abs(diff - scaled) < TOL or bridges_scaled:
                    if row.debit:
                        row.debit /= 1000.0
                    if row.credit:
                        row.credit /= 1000.0
                    signed = scaled

            if abs(diff - signed) >= TOL:
                bridges_cur = (
                    nxt is not None
                    and nxt.balance is not None
                    and nxt_s is not None
                    and (
                        abs(prev + signed + nxt_s - nxt.balance) < TOL
                        or abs(prev + signed + nxt_s / 1000.0 - nxt.balance) < TOL
                    )
                )
                if bridges_cur:
                    row.balance = prev + signed
                elif explicit_hint is None and abs(diff + signed) < TOL:
                    row.debit, row.credit = row.credit, row.debit
                    row.validation.append("direction_corrected")
                else:
                    row.validation.append("balance_mismatch")
        elif signed is None and row.pending_mutasi is None and row.balance is not None and prev is not None:
            diff = row.balance - prev
            if abs(diff) >= TOL:
                setattr(row, "credit" if diff > 0 else "debit", abs(diff))
                row.validation.append("amount_derived")

        if row.balance is None and prev is not None:
            row.balance = prev + (row.credit or 0.0) - (row.debit or 0.0)
            row.validation.append("saldo_calculated")
        if row.balance is not None:
            prev = row.balance

    for row in rows:
        row.confidence = max(0.0, 1.0 - sum(PENALTIES.get(v, 0.2) for v in row.validation))
    if opening_hint is not None and not openings:
        info["opening_inferred"] = True
    return info


__all__ = ["chain_matches", "reconcile"]
