from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

from component.core import (
    AUDIT_TOL,
    AuditIssue,
    EXTRACTION_CONFIDENCE_CONFLICT,
    EXTRACTION_CONFIDENCE_OVERLAID,
    ExtractedRow,
    OPENING_RE,
    StatementSession,
    add_flag,
    apply_confidence,
    drop_flag,
    iso_date,
    make_issue,
)
from component.parsing import hint_direction, indonesia_date_key, months_between, row_month_pair


# ---------------------------------------------------------------------------
# Balance Math
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def signed_amount(row: ExtractedRow) -> float | None:
    if not (row.debit or row.credit):
        return None
    return (row.credit or 0.0) - (row.debit or 0.0)


def magnitude(row: ExtractedRow) -> float:
    signed = signed_amount(row)
    return abs(signed) if signed else (row.pending_mutasi or 0.0)


def delta_direction(prev: float | None, bal: float | None, amt: float | None) -> str | None:
    if prev is None or bal is None or amt is None:
        return None
    diff = bal - prev
    if abs(diff - amt) <= AUDIT_TOL:
        return "credit"
    if abs(diff + amt) <= AUDIT_TOL:
        return "debit"
    return None


def choose_direction(
    prev: float | None,
    bal: float | None,
    amt: float,
    explicit: str | None,
    desc: str,
) -> tuple[str | None, str]:
    by_bal = delta_direction(prev, bal, amt)
    by_hint = hint_direction(desc)
    if explicit:
        return explicit, "marker"
    if by_hint:
        return by_hint, "description"
    if by_bal:
        return by_bal, "balance_delta"
    return None, "unknown"


def infer_seed_balance(
    openings: list[ExtractedRow],
    body: list[ExtractedRow],
    hint: float | None = None,
    printed: dict[str, float] | None = None,
) -> float | None:
    """Infers initial/seed balance from opening rows, hints, printed totals, or first body checkpoint."""
    printed = printed or {}
    seed_bal: float | None = (
        openings[0].printed_balance
        if openings and openings[0].printed_balance is not None
        else (hint if hint is not None else printed.get("opening"))
    )
    if seed_bal is None and body:
        acc_pre = 0.0
        for cand in body:
            s = signed_amount(cand)
            if s is None and cand.pending_mutasi is not None:
                break
            acc_pre += s or 0.0
            if cand.printed_balance is not None:
                seed_bal = round(cand.printed_balance - acc_pre, 2)
                break
    return seed_bal


def resolve_pending(
    row: ExtractedRow,
    prev: float | None,
    tol: float = AUDIT_TOL,
    prefer: str = "debit",
) -> str | None:
    """Resolve remaining pending_mutasi via running balance delta when available."""
    if (
        row.pending_mutasi is None
        or row.debit
        or row.credit
        or prev is None
        or row.printed_balance is None
    ):
        return None

    amt = row.pending_mutasi
    bal = row.printed_balance

    check_debit = abs((prev - amt) - bal) <= tol
    check_credit = abs((prev + amt) - bal) <= tol

    first, second = ("debit", "credit") if prefer == "debit" else ("credit", "debit")
    first_match = check_debit if first == "debit" else check_credit
    second_match = check_credit if first == "debit" else check_debit

    matched = None
    if first_match:
        matched = first
    elif second_match:
        matched = second

    if matched == "debit":
        row.debit = amt
        row.pending_mutasi = None
        drop_flag(row, "direction_unknown")
        return "debit"
    elif matched == "credit":
        row.credit = amt
        row.pending_mutasi = None
        drop_flag(row, "direction_unknown")
        return "credit"

    return None


# ---------------------------------------------------------------------------
# Chain Walking & Scoring
# ---------------------------------------------------------------------------


def walk_balance_chain(
    seq: Sequence[ExtractedRow],
    seed_bal: float | None = None,
    only_printed_balance: bool = False,
    allow_zero_mag_checkpoint: bool = False,
) -> Iterator[tuple[float, float, float, bool]]:
    """Walk a sequence of transaction rows and yield (diff, acc_signed, acc_mag, has_pending) per checkpoint."""
    prev_bal = seed_bal
    acc_signed = 0.0
    acc_mag = 0.0
    has_pending = False
    for r in seq:
        bal = r.printed_balance if only_printed_balance else (
            r.printed_balance if r.printed_balance is not None else r.balance
        )
        if r.is_opening and bal is not None:
            prev_bal = bal
            acc_signed = 0.0
            acc_mag = 0.0
            has_pending = False
            continue
        mag = magnitude(r)
        signed = signed_amount(r)
        if signed is not None:
            acc_signed += signed
        elif r.pending_mutasi is not None:
            has_pending = True
        acc_mag += mag

        checkpoint_ready = (
            bal is not None
            and prev_bal is not None
            and (acc_mag > 0 or (allow_zero_mag_checkpoint and signed is not None))
        )
        if checkpoint_ready:
            diff = bal - prev_bal
            yield diff, acc_signed, acc_mag, has_pending
            prev_bal = bal
            acc_signed = 0.0
            acc_mag = 0.0
            has_pending = False
        elif bal is not None and prev_bal is None:
            prev_bal = bal
            acc_signed = 0.0
            acc_mag = 0.0
            has_pending = False


def chain_matches(seq: list[ExtractedRow]) -> tuple[int, int, int]:
    mag_ok = ok = inv = 0
    for diff, acc_signed, acc_mag, _ in walk_balance_chain(seq, seed_bal=None, allow_zero_mag_checkpoint=False):
        if abs(abs(diff) - acc_mag) <= AUDIT_TOL:
            mag_ok += 1
        if abs(diff - acc_signed) <= AUDIT_TOL:
            ok += 1
        if abs(diff + acc_signed) <= AUDIT_TOL:
            inv += 1
    return mag_ok, ok, inv


def _chain_score(seq: list[ExtractedRow], seed_bal: float | None = None) -> tuple[int, int, int]:
    """Score (signed_matches, mag_matches, transitions_checked) from an optional seed balance."""
    ok = mag_ok = checked = 0
    for diff, acc_signed, acc_mag, has_pending in walk_balance_chain(
        seq, seed_bal=seed_bal, allow_zero_mag_checkpoint=True
    ):
        checked += 1
        if abs(abs(diff) - acc_mag) <= AUDIT_TOL:
            mag_ok += 1
        if not has_pending and abs(diff - acc_signed) <= AUDIT_TOL:
            ok += 1
    return ok, mag_ok, checked


def has_internal_bucket_discrepancy(
    page_rows: Sequence[ExtractedRow],
    prev_closing_balance: float | None = None,
) -> bool:
    if not page_rows:
        return False
    for diff, acc_signed, _, _ in walk_balance_chain(
        page_rows,
        seed_bal=prev_closing_balance,
        only_printed_balance=True,
        allow_zero_mag_checkpoint=True,
    ):
        ok_fwd = abs(diff - acc_signed) <= AUDIT_TOL
        ok_rev = abs(diff + acc_signed) <= AUDIT_TOL
        if not (ok_fwd or ok_rev):
            return True
    return False


# ---------------------------------------------------------------------------
# Chronological Ordering & Permutations
# ---------------------------------------------------------------------------


def _transition_matches(prev_bal: float, row: ExtractedRow) -> bool:
    bal = row.printed_balance
    if bal is None:
        return False
    signed = signed_amount(row)
    if signed is not None:
        return abs((prev_bal + signed) - bal) <= AUDIT_TOL
    if row.pending_mutasi is not None:
        return abs(abs(bal - prev_bal) - row.pending_mutasi) <= AUDIT_TOL
    return False


def _advance_balance(prev_bal: float | None, rows: list[ExtractedRow]) -> float | None:
    bal = prev_bal
    for r in rows:
        if r.printed_balance is not None:
            bal = r.printed_balance
        elif bal is not None:
            signed = signed_amount(r)
            if signed is not None:
                bal = round(bal + signed, 2)
    return bal


def _greedy_balance_chain(rows: list[ExtractedRow], seed_bal: float) -> list[ExtractedRow]:
    """Reorder ambiguous rows via balance continuity only when strictly improving chain matches."""
    if len(rows) < 2 or len(rows) > 50 or sum(1 for r in rows if r.printed_balance is not None) < 2:
        return rows
    remaining = list(rows)
    out: list[ExtractedRow] = []
    cur_bal: float | None = seed_bal
    while remaining:
        head = remaining[0]
        if cur_bal is not None and _transition_matches(cur_bal, head):
            chosen_idx = 0
        elif cur_bal is not None:
            chosen_idx = next(
                (i for i, r in enumerate(remaining) if _transition_matches(cur_bal, r) and magnitude(r) > 0),
                0,
            )
            if chosen_idx == 0 and not _transition_matches(cur_bal, head):
                chosen_idx = next((i for i, r in enumerate(remaining) if _transition_matches(cur_bal, r)), 0)
        else:
            chosen_idx = 0
        picked = remaining.pop(chosen_idx)
        out.append(picked)
        cur_bal = _advance_balance(cur_bal, [picked])
    return out


def _order_same_day_rows(
    day_rows: list[ExtractedRow],
    seq_map: dict[int, int],
    seed_bal: float | None,
    reverse_untimed: bool,
) -> list[ExtractedRow]:
    if len(day_rows) <= 1:
        return list(day_rows)

    sign = -1 if reverse_untimed else 1
    det_sorted = sorted(day_rows, key=lambda r: sign * seq_map[id(r)])
    timed = [r for r in det_sorted if getattr(r, "time", None) is not None]
    untimed = [r for r in det_sorted if getattr(r, "time", None) is None]

    det_ok, det_mag, det_chk = _chain_score(det_sorted, seed_bal)
    if det_chk > 0 and det_ok >= det_chk:
        return det_sorted

    if not timed:
        best = det_sorted
        best_score = (det_ok, det_mag)
        rev = list(reversed(det_sorted))
        r_ok, r_mag, _ = _chain_score(rev, seed_bal)
        if (r_ok, r_mag) > best_score:
            best = rev
            best_score = (r_ok, r_mag)
        if seed_bal is not None:
            chained = _greedy_balance_chain(det_sorted, seed_bal)
            c_ok, c_mag, _ = _chain_score(chained, seed_bal)
            if (c_ok, c_mag) > best_score:
                best = chained
        return best

    timed_ordered: list[ExtractedRow] = []
    time_sorted = sorted(timed, key=lambda r: (r.time, sign * seq_map[id(r)]))
    run_bal = seed_bal if not untimed else None
    idx = 0
    while idx < len(time_sorted):
        t_val = time_sorted[idx].time
        j = idx + 1
        while j < len(time_sorted) and time_sorted[j].time == t_val:
            j += 1
        grp = time_sorted[idx:j]
        if len(grp) > 1 and run_bal is not None and not untimed:
            g_ok, g_mag, g_chk = _chain_score(grp, run_bal)
            if g_ok < g_chk:
                cand_grp = _greedy_balance_chain(grp, run_bal)
                c_ok, c_mag, _ = _chain_score(cand_grp, run_bal)
                if (c_ok, c_mag) > (g_ok, g_mag):
                    grp = cand_grp
        timed_ordered.extend(grp)
        if run_bal is not None:
            run_bal = _advance_balance(run_bal, grp)
        idx = j

    if not untimed:
        best = timed_ordered
    else:
        anchored: list[tuple[tuple, ExtractedRow]] = []
        anchor_t = (-1, -1, -1)
        for r in det_sorted:
            if r.time is not None:
                anchor_t = r.time
                anchored.append(((r.time, 0, sign * seq_map[id(r)]), r))
            else:
                anchored.append(((anchor_t, 1, sign * seq_map[id(r)]), r))
        cand_a = [r for _, r in sorted(anchored, key=lambda item: item[0])]
        cand_b = timed_ordered + untimed

        best = cand_a
        a_ok, a_mag, a_chk = _chain_score(cand_a, seed_bal)
        best_score = (a_ok, a_mag)
        b_ok, b_mag, _ = _chain_score(cand_b, seed_bal)
        if (b_ok, b_mag) > best_score:
            best = cand_b
            best_score = (b_ok, b_mag)

        if best_score[0] < a_chk and len(untimed) <= 4 and len(day_rows) <= 30:
            cur_seq = list(timed_ordered)
            for u in untimed:
                cur_ok, cur_mag, _ = _chain_score(best, seed_bal)
                u_best = None
                u_score = (cur_ok, cur_mag)
                for pos in range(len(cur_seq) + 1):
                    trial = cur_seq[:pos] + [u] + cur_seq[pos:]
                    t_ok, t_mag, _ = _chain_score(trial, seed_bal)
                    if (t_ok, t_mag) > u_score:
                        u_score = (t_ok, t_mag)
                        u_best = trial
                if u_best is not None:
                    cur_seq = u_best
                    best = cur_seq
                else:
                    break
            if len(cur_seq) < len(day_rows):
                seen_ids = {id(r) for r in cur_seq}
                rem = [r for r in best if id(r) not in seen_ids]
                c_seq = cur_seq + rem
                if _chain_score(c_seq, seed_bal)[:2] > _chain_score(best, seed_bal)[:2]:
                    best = c_seq

    b_ok, b_mag, _ = _chain_score(best, seed_bal)
    if (b_ok, b_mag) > (det_ok, det_mag):
        return best
    if det_chk > 0 and (det_ok, det_mag) >= (b_ok, b_mag):
        return det_sorted
    return best


def sort_transactions_chronologically(
    rows: list[ExtractedRow],
    opening_hint: float | None = None,
    printed: dict[str, float] | None = None,
) -> tuple[list[ExtractedRow], str]:
    """Sort rows by Indonesia date (DD/MM/YYYY), optional local 24h time, balance fit, then detection order."""
    if not rows:
        return rows, "as_printed"

    printed = printed or {}
    openings = [r for r in rows if r.is_opening]
    body = [r for r in rows if not r.is_opening]
    if len(body) <= 1:
        return openings + body, "as_printed"

    seq_map = {id(r): idx for idx, r in enumerate(body)}
    raw_date_keys: dict[int, tuple[int, int, int]] = {}
    last_dk = (0, 0, 0)
    for r in rows:
        dk = indonesia_date_key(r)
        if dk == (0, 0, 0) and not r.is_opening and last_dk != (0, 0, 0):
            dk = last_dk
        if dk != (0, 0, 0):
            last_dk = dk
        raw_date_keys[id(r)] = dk

    seed_bal = infer_seed_balance(openings, body, opening_hint, printed)

    fwd_probe = sorted(
        body,
        key=lambda r: (
            raw_date_keys[id(r)],
            r.time if getattr(r, "time", None) is not None else (99, 99, 99),
            seq_map[id(r)],
        ),
    )
    rev_probe = sorted(
        body,
        key=lambda r: (
            raw_date_keys[id(r)],
            r.time if getattr(r, "time", None) is not None else (-1, -1, -1),
            -seq_map[id(r)],
        ),
    )
    fwd_score = _chain_score(fwd_probe, seed_bal)[:2]
    rev_score = _chain_score(rev_probe, seed_bal)[:2]
    raw_fwd = _chain_score(body, seed_bal)[:2]
    raw_rev = _chain_score(list(reversed(body)), seed_bal)[:2]
    reverse_untimed = rev_score > fwd_score or (raw_rev > raw_fwd and rev_score >= fwd_score)

    distinct_dates = sorted({raw_date_keys[id(r)] for r in body})
    openings_by_date: dict[tuple[int, int, int], list[ExtractedRow]] = {}
    extra_openings: list[ExtractedRow] = []
    for op in openings:
        dk = raw_date_keys[id(op)]
        if dk in distinct_dates:
            openings_by_date.setdefault(dk, []).append(op)
        elif not distinct_dates or dk <= distinct_dates[0]:
            openings_by_date.setdefault(distinct_dates[0], []).append(op)
        else:
            extra_openings.append(op)

    ordered_out: list[ExtractedRow] = []
    cur_anchor_bal = seed_bal
    for dk in distinct_dates:
        day_ops = openings_by_date.get(dk, [])
        for op in day_ops:
            ordered_out.append(op)
            if op.printed_balance is not None:
                cur_anchor_bal = op.printed_balance
        day_body = [r for r in body if raw_date_keys[id(r)] == dk]
        ordered_day = _order_same_day_rows(day_body, seq_map, cur_anchor_bal, reverse_untimed)
        ordered_out.extend(ordered_day)
        cur_anchor_bal = _advance_balance(cur_anchor_bal, ordered_day)

    if extra_openings:
        ordered_out = extra_openings + ordered_out

    if [id(r) for r in ordered_out] == [id(r) for r in rows]:
        order_label = "as_printed"
    elif reverse_untimed:
        order_label = "newest_first"
    else:
        order_label = "chronological"
    return ordered_out, order_label


# ---------------------------------------------------------------------------
# Running Balances & Reconciliation
# ---------------------------------------------------------------------------


def compute_running_balances(
    rows: list[ExtractedRow],
    initial_balance: float | None = None,
) -> list[float | None]:
    """Calculate running balances purely from numeric transaction flows."""
    current = initial_balance
    results: list[float | None] = []
    for r in rows:
        if r.is_opening:
            if r.printed_balance is not None:
                current = r.printed_balance
            elif r.balance is not None:
                current = r.balance
            results.append(current)
            continue
        c = r.credit or 0.0
        d = r.debit or 0.0
        if current is not None:
            current = round(current + c - d, 2)
            results.append(current)
        elif r.printed_balance is not None:
            current = r.printed_balance
            results.append(current)
        elif r.balance is not None:
            current = r.balance
            results.append(current)
        else:
            results.append(None)
    return results


def reconcile_balances(
    rows: list[ExtractedRow],
    opening_hint: float | None = None,
    printed: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Pure numeric balance progression, pending direction resolution, and math delta calculations."""
    printed = printed or {}
    for r in rows:
        if r.printed_balance is None and r.balance is not None and (r.has_printed_balance or r.computed_balance is None):
            r.printed_balance = r.balance
        elif r.balance is None and r.printed_balance is not None:
            r.balance = r.printed_balance
        elif r.printed_balance is None and not r.has_printed_balance:
            r.balance = None
            r.computed_balance = None
        r.has_printed_balance = r.printed_balance is not None
        if (
            not r.is_opening
            and OPENING_RE.match(r.description or "")
            and not (r.debit or r.credit or r.pending_mutasi)
            and r.printed_balance is not None
        ):
            r.is_opening = True

    sorted_rows, sort_order = sort_transactions_chronologically(
        rows,
        opening_hint=opening_hint,
        printed=printed,
    )
    rows[:] = sorted_rows
    for idx, r in enumerate(rows):
        r.transaction_index = idx

    openings = [r for r in rows if r.is_opening]
    body = [r for r in rows if not r.is_opening]

    checkpoint = infer_seed_balance(openings, body, opening_hint, printed)

    opening_inferred = False
    if (
        not openings
        and body
        and body[0].printed_balance is not None
        and checkpoint is not None
        and abs(checkpoint - body[0].printed_balance) <= AUDIT_TOL
        and (signed_amount(body[0]) or 0.0) != 0.0
    ):
        checkpoint = round(body[0].printed_balance - (signed_amount(body[0]) or 0.0), 2)
        opening_inferred = True

    initial_opening = checkpoint
    current_bal: float | None = checkpoint
    pending_bucket: list[ExtractedRow] = []

    for r in rows:
        if r.is_opening:
            if r.printed_balance is not None:
                checkpoint = r.printed_balance
                current_bal = r.printed_balance
            r.checkpoint_balance = checkpoint
            r.computed_balance = current_bal
            continue

        prev_bal = current_bal
        resolve_pending(r, prev_bal, tol=AUDIT_TOL, prefer="credit")

        if (
            getattr(r, "single_amount_col", False)
            and prev_bal is not None
            and r.printed_balance is not None
            and EXTRACTION_CONFIDENCE_OVERLAID not in r.validation
            and bool(r.debit) != bool(r.credit)
        ):
            amt_val = r.credit if r.credit else r.debit
            assert amt_val is not None
            cur_c = r.credit or 0.0
            cur_d = r.debit or 0.0
            cur_err = abs((prev_bal + cur_c - cur_d) - r.printed_balance)
            flip_err = abs((prev_bal - cur_c + cur_d) - r.printed_balance)
            if cur_err > AUDIT_TOL and flip_err <= AUDIT_TOL and amt_val > AUDIT_TOL:
                r.debit, r.credit = r.credit, r.debit

        c = r.credit or 0.0
        d = r.debit or 0.0
        p_bal = r.printed_balance
        r.has_printed_balance = p_bal is not None
        r.checkpoint_balance = checkpoint

        computed = round(prev_bal + c - d, 2) if prev_bal is not None else p_bal
        r.computed_balance = computed
        if r.balance is None and computed is not None:
            r.balance = computed

        if p_bal is None:
            pending_bucket.append(r)
            r.reconciliation_status = "VERIFIED_BY_BUCKET"
            r.is_reconciled = True
            r.balance_discrepancy = None
            current_bal = computed
            continue

        if prev_bal is not None and computed is not None:
            disc = round(computed - p_bal, 2)
            r.balance_discrepancy = disc
            is_match = abs(disc) <= AUDIT_TOL and r.pending_mutasi is None
            r.is_reconciled = is_match
            r.reconciliation_status = "RECONCILED" if is_match else "DISCREPANCY"
            for b in pending_bucket:
                b.is_reconciled = is_match
                b.reconciliation_status = "VERIFIED_BY_BUCKET" if is_match else "DISCREPANCY"
                b.checkpoint_balance = checkpoint
            pending_bucket.clear()
            checkpoint = p_bal
            current_bal = p_bal
        else:
            checkpoint = p_bal
            current_bal = p_bal
            r.balance_discrepancy = 0.0
            r.is_reconciled = True
            r.reconciliation_status = "RECONCILED"
            pending_bucket.clear()

    # Trailing unclosed bucket
    if pending_bucket:
        closing_target = printed.get("closing")
        if closing_target is not None and current_bal is not None:
            c_disc = round(current_bal - closing_target, 2)
            matched = abs(c_disc) <= AUDIT_TOL and all(b.pending_mutasi is None for b in pending_bucket)
            for b in pending_bucket:
                b.is_reconciled = matched
                b.reconciliation_status = "VERIFIED_BY_BUCKET" if matched else "DISCREPANCY"
        else:
            for b in pending_bucket:
                if b.pending_mutasi is not None:
                    b.is_reconciled = False
                    b.reconciliation_status = "DISCREPANCY"

    balances = [r.printed_balance for r in body if r.printed_balance is not None]
    tot_debit = round(sum(r.debit or 0.0 for r in body), 2)
    tot_credit = round(sum(r.credit or 0.0 for r in body), 2)
    stated_closing = balances[-1] if balances else printed.get("closing")

    return {
        "order": sort_order,
        "debit_credit_swapped": False,
        "initial_opening": initial_opening,
        "opening_inferred": opening_inferred or (initial_opening is not None and not openings and opening_hint is None and "opening" not in printed),
        "total_debit": tot_debit,
        "total_credit": tot_credit,
        "stated_closing": stated_closing,
        "closing_computed": current_bal,
    }


# ---------------------------------------------------------------------------
# Ledger Audit
# ---------------------------------------------------------------------------


def audit_ledger(
    rows: list[ExtractedRow],
    opening_hint: float | None = None,
    printed: dict[str, float] | None = None,
) -> dict[str, Any]:
    printed = printed or {}
    calc = reconcile_balances(rows, opening_hint=opening_hint, printed=printed)

    body = [r for r in rows if not r.is_opening]
    discrepancy_alerts: list[dict[str, Any]] = []
    audit_issues: list[AuditIssue] = []

    prev_ym: tuple[int, int] | None = None
    prev_computed: float | None = calc["initial_opening"]
    body_ordinal = -1

    for row in rows:
        tx_idx = row.transaction_index
        cur_ym = row_month_pair(row)

        if row.is_opening:
            printed_bal = row.printed_balance
            if (
                tx_idx > 0
                and prev_computed is not None
                and printed_bal is not None
                and abs(prev_computed - printed_bal) > AUDIT_TOL
            ):
                gap = round(prev_computed - printed_bal, 2)
                add_flag(row, "MISSING_PERIOD_OR_CONTINUITY_GAP")
                audit_issues.append(
                    make_issue(
                        transaction_index=tx_idx,
                        pdf_page=row.pdf_page_index,
                        date=iso_date(row.date),
                        issue_type="MISSING_PERIOD_OR_CONTINUITY_GAP",
                        severity="HIGH",
                        printed_value=printed_bal,
                        expected_value=prev_computed,
                        discrepancy=gap,
                        description=(
                            f"Cross-month/page continuity gap at transaction_index={tx_idx}: "
                            f"previous closing balance {prev_computed:,.2f} != "
                            f"new opening balance {printed_bal:,.2f}."
                        ),
                    )
                )
            if printed_bal is not None:
                prev_computed = printed_bal
            if cur_ym:
                prev_ym = cur_ym
            continue

        body_ordinal += 1

        # Cross-month seam jump check
        if prev_ym and cur_ym and cur_ym != prev_ym:
            months_diff = months_between(prev_ym, cur_ym)
            if months_diff > 1:
                add_flag(row, "MISSING_PERIOD_OR_CONTINUITY_GAP")
                audit_issues.append(
                    make_issue(
                        transaction_index=tx_idx,
                        pdf_page=row.pdf_page_index,
                        date=iso_date(row.date),
                        issue_type="MISSING_PERIOD_OR_CONTINUITY_GAP",
                        severity="MEDIUM",
                        printed_value=row.printed_balance,
                        expected_value=prev_computed,
                        discrepancy=None,
                        description=(
                            f"Statement period jump detected from {prev_ym[0]}-{prev_ym[1]:02d} "
                            f"to {cur_ym[0]}-{cur_ym[1]:02d} at transaction_index={tx_idx}."
                        ),
                    )
                )
        if cur_ym:
            prev_ym = cur_ym

        # Discrepancy inspection from balance calculation
        if not row.is_reconciled and row.reconciliation_status == "DISCREPANCY":
            add_flag(row, "MATH_DISCREPANCY_ALERT")

            disc = row.balance_discrepancy
            p_bal = row.printed_balance
            c_bal = row.computed_balance

            alert = {
                "alert": "MATH_DISCREPANCY_ALERT",
                "scope": "row",
                "row_index": body_ordinal,
                "transaction_index": tx_idx,
                "bucket_start_index": tx_idx,
                "bucket_end_index": tx_idx,
                "page": row.pdf_page_index,
                "stated_balance": p_bal,
                "printed_balance": p_bal,
                "expected_balance": c_bal,
                "computed_balance": c_bal,
                "delta": -disc if disc is not None else None,
                "balance_discrepancy": disc,
            }
            row.math_discrepancy = alert
            discrepancy_alerts.append(alert)

            desc = (
                f"Balance mismatch at transaction_index={tx_idx}: computed {c_bal:,.2f} vs printed {p_bal:,.2f} "
                f"(discrepancy {disc:+,.2f})."
                if (c_bal is not None and p_bal is not None and disc is not None)
                else f"Unresolved transaction direction/balance at transaction_index={tx_idx}."
            )
            audit_issues.append(
                make_issue(
                    transaction_index=tx_idx,
                    pdf_page=row.pdf_page_index,
                    date=iso_date(row.date),
                    issue_type="BALANCE_MISMATCH",
                    severity="HIGH",
                    printed_value=p_bal,
                    expected_value=c_bal,
                    discrepancy=disc,
                    description=desc,
                )
            )

        if row.computed_balance is not None:
            prev_computed = row.computed_balance
        elif row.printed_balance is not None:
            prev_computed = row.printed_balance

    # Summary Invariant Verification
    initial_opening = calc["initial_opening"]
    stated_closing = calc["stated_closing"]
    total_debit = calc["total_debit"]
    total_credit = calc["total_credit"]

    summary_invariant_ok: bool | None = None
    summary_discrepancy: dict[str, Any] | None = None

    if initial_opening is not None and stated_closing is not None:
        expected_closing = round(initial_opening + total_credit - total_debit, 2)
        summary_delta = round(stated_closing - expected_closing, 2)
        summary_disc = round(expected_closing - stated_closing, 2)
        summary_invariant_ok = abs(summary_delta) <= AUDIT_TOL
        if not summary_invariant_ok:
            last_tx_idx = body[-1].transaction_index if body else 0
            last_page = body[-1].pdf_page_index if body else 1
            summary_discrepancy = {
                "alert": "MATH_DISCREPANCY_ALERT",
                "scope": "summary",
                "row_index": None,
                "transaction_index": last_tx_idx,
                "page": last_page,
                "opening_balance": initial_opening,
                "total_credit": total_credit,
                "total_debit": total_debit,
                "stated_balance": stated_closing,
                "printed_balance": stated_closing,
                "expected_balance": expected_closing,
                "computed_balance": expected_closing,
                "delta": summary_delta,
                "balance_discrepancy": summary_disc,
            }
            discrepancy_alerts.append(summary_discrepancy)
            if not any(i.transaction_index == last_tx_idx and i.issue_type == "BALANCE_MISMATCH" for i in audit_issues):
                audit_issues.append(
                    make_issue(
                        transaction_index=last_tx_idx,
                        pdf_page=last_page,
                        date=iso_date(body[-1].date) if body else "",
                        issue_type="BALANCE_MISMATCH",
                        severity="HIGH",
                        printed_value=stated_closing,
                        expected_value=expected_closing,
                        discrepancy=summary_disc,
                        description=(
                            f"Statement summary invariant mismatch: opening ({initial_opening:,.2f}) "
                            f"+ credit ({total_credit:,.2f}) - debit ({total_debit:,.2f}) = "
                            f"{expected_closing:,.2f} != closing ({stated_closing:,.2f})."
                        ),
                    )
                )

    if printed.get("closing") is not None and initial_opening is not None:
        expected_closing = round(initial_opening + total_credit - total_debit, 2)
        printed_delta = round(printed["closing"] - expected_closing, 2)
        printed_disc = round(expected_closing - printed["closing"], 2)
        if abs(printed_delta) > AUDIT_TOL:
            last_tx_idx = body[-1].transaction_index if body else 0
            last_page = body[-1].pdf_page_index if body else 1
            printed_alert = {
                "alert": "MATH_DISCREPANCY_ALERT",
                "scope": "printed_closing",
                "row_index": None,
                "transaction_index": last_tx_idx,
                "page": last_page,
                "stated_balance": printed["closing"],
                "printed_balance": printed["closing"],
                "expected_balance": expected_closing,
                "computed_balance": expected_closing,
                "delta": printed_delta,
                "balance_discrepancy": printed_disc,
            }
            if not summary_discrepancy:
                summary_discrepancy = printed_alert
            discrepancy_alerts.append(printed_alert)
            summary_invariant_ok = False

    # Quarantined reported total verification (debit / credit)
    for tot_role, computed_tot in (("debit", total_debit), ("credit", total_credit)):
        reported_val = printed.get(tot_role)
        if reported_val is not None:
            tot_diff = round(computed_tot - reported_val, 2)
            if abs(tot_diff) > AUDIT_TOL:
                last_tx_idx = body[-1].transaction_index if body else 0
                last_page = body[-1].pdf_page_index if body else 1
                tot_alert = {
                    "alert": "TOTAL_MISMATCH",
                    "scope": f"reported_total_{tot_role}",
                    "row_index": None,
                    "transaction_index": last_tx_idx,
                    "page": last_page,
                    "stated_balance": reported_val,
                    "printed_balance": reported_val,
                    "expected_balance": computed_tot,
                    "computed_balance": computed_tot,
                    "delta": round(reported_val - computed_tot, 2),
                    "balance_discrepancy": tot_diff,
                }
                discrepancy_alerts.append(tot_alert)
                audit_issues.append(
                    make_issue(
                        transaction_index=last_tx_idx,
                        pdf_page=last_page,
                        date=iso_date(body[-1].date) if body else "",
                        issue_type="TOTAL_MISMATCH",
                        severity="HIGH",
                        printed_value=reported_val,
                        expected_value=computed_tot,
                        discrepancy=tot_diff,
                        description=(
                            f"Quarantined TOTAL {tot_role} mismatch: computed sum {computed_tot:,.2f} "
                            f"vs reported total {reported_val:,.2f} (discrepancy {tot_diff:+,.2f})."
                        ),
                    )
                )
                summary_invariant_ok = False

    # Row confidence penalties and extraction confidence checks
    for row in rows:
        apply_confidence(row)
        if EXTRACTION_CONFIDENCE_OVERLAID in row.validation:
            row.extraction_confidence = EXTRACTION_CONFIDENCE_OVERLAID
        elif EXTRACTION_CONFIDENCE_CONFLICT in row.validation:
            row.extraction_confidence = EXTRACTION_CONFIDENCE_CONFLICT
            audit_issues.append(
                make_issue(
                    transaction_index=row.transaction_index,
                    pdf_page=row.pdf_page_index,
                    date=iso_date(row.date),
                    issue_type="EXTRACTION_CONFLICT",
                    severity="HIGH",
                    printed_value=row.printed_balance,
                    expected_value=row.computed_balance,
                    discrepancy=row.balance_discrepancy,
                    description=f"Lexical/alpha contamination or extraction conflict at transaction_index={row.transaction_index}.",
                )
            )

    info: dict[str, Any] = {
        "order": calc["order"],
        "debit_credit_swapped": calc["debit_credit_swapped"],
        "math_discrepancy_alerts": discrepancy_alerts,
        "audit_issues": audit_issues,
        "summary_invariant_ok": summary_invariant_ok,
    }
    if summary_discrepancy:
        info["summary_discrepancy"] = summary_discrepancy
    if calc.get("opening_inferred"):
        info["opening_inferred"] = True

    return info


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@dataclass
class AccountBucket:
    page_bundles: list[tuple[int, dict, list[ExtractedRow]]] = field(default_factory=list)
    printed: dict[str, float] = field(default_factory=dict)
    years: list[int] = field(default_factory=list)
    paths: list[Path] = field(default_factory=list)
    overlaid: list[dict[str, Any]] = field(default_factory=list)

    def merge_into(self, other: AccountBucket, new_bank: str | None = None) -> None:
        if new_bank:
            for _, diag, p_rows in self.page_bundles:
                diag["bank"] = new_bank
                for row in p_rows:
                    row.bank = new_bank
        other.page_bundles.extend(self.page_bundles)
        other.printed.update(self.printed)
        other.years.extend(self.years)
        other.paths.extend(self.paths)
        other.overlaid.extend(self.overlaid)


def group_key_label(bank: str, acc: str) -> str:
    clean_acc = "" if acc == "UNKNOWN_ACCOUNT" else acc
    if bank and clean_acc:
        return f"{bank} - {clean_acc}"
    return bank or clean_acc or "Default"


# ---------------------------------------------------------------------------
# Page Signature Hashing & Topological Ordering
# ---------------------------------------------------------------------------


def page_signature_hash(page_rows: Sequence[ExtractedRow]) -> int | None:
    if not page_rows:
        return None
    tuples = tuple(
        (
            r.date.isoformat() if isinstance(r.date, (date, datetime)) else str(r.date or r.date_parts),
            round(r.debit or 0.0, 2),
            round(r.credit or 0.0, 2),
            round((r.printed_balance if r.printed_balance is not None else r.balance) or 0.0, 2),
            r.description,
        )
        for r in page_rows
    )
    return hash(tuples)


def topological_sort_key(row: ExtractedRow, fallback_year: int | None) -> tuple:
    st_year = row.statement_year
    st_month = row.statement_month
    if isinstance(row.date, (date, datetime)):
        st_year = st_year or row.date.year
        st_month = st_month or row.date.month
    elif row.date_parts and len(row.date_parts) >= 2:
        st_month = st_month or row.date_parts[1]
        if len(row.date_parts) >= 3 and row.date_parts[2]:
            st_year = st_year or row.date_parts[2]

    st_year = st_year or fallback_year or 0
    st_month = st_month or 0
    printed_page = row.printed_page_number if row.printed_page_number is not None else row.pdf_page_index
    opening_rank = 0 if (row.is_opening and row.row_top_y <= 0.0) else 1
    return (
        st_year,
        st_month,
        printed_page,
        row.pdf_page_index,
        opening_rank,
        round(row.row_top_y, 2),
    )


def order_page_bundles(
    page_bundles: list[tuple[int, dict, list[ExtractedRow]]],
    fallback_year: int | None,
) -> list[tuple[int, dict, list[ExtractedRow]]]:
    """Order page bundles when explicit printed page numbers indicate out-of-order pages within a statement."""
    if len(page_bundles) <= 1:
        return list(page_bundles)
    has_printed_page_nums = any(diag.get("printed_page_number") is not None for _, diag, _ in page_bundles)
    distinct_periods = {
        (diag.get("statement_year"), diag.get("statement_month"))
        for _, diag, _ in page_bundles
        if diag.get("statement_month") is not None
    }
    if not has_printed_page_nums and len(distinct_periods) <= 1:
        return list(page_bundles)

    keyed: list[tuple[tuple[int, int, int, int], tuple[int, dict, list[ExtractedRow]]]] = []
    last_y = fallback_year or 0
    last_m = 0
    for eff_page_idx, diag, p_rows in page_bundles:
        py = diag.get("statement_year") or last_y
        pm = diag.get("statement_month")
        if pm is None:
            first_r = next((r for r in p_rows if r.statement_month or r.date_parts), None)
            if first_r is not None:
                pm = first_r.statement_month or (first_r.date_parts[1] if first_r.date_parts and len(first_r.date_parts) >= 2 else None)
        pm = pm if pm is not None else last_m
        last_y, last_m = py, pm
        p_num = diag.get("printed_page_number")
        sort_p = p_num if p_num is not None else eff_page_idx
        keyed.append(((py, pm, sort_p, eff_page_idx), (eff_page_idx, diag, p_rows)))

    keyed.sort(key=lambda item: item[0])
    return [item[1] for item in keyed]


# ---------------------------------------------------------------------------
# Session Slicing & Balance Forward-Fill
# ---------------------------------------------------------------------------


def dedupe_same_month_openings(rows: list[ExtractedRow]) -> list[ExtractedRow]:
    kept: list[ExtractedRow] = []
    seen_opening_months: set[tuple[int | None, int | None, float | None]] = set()
    for row in rows:
        if row.is_opening:
            sig = (row.statement_year, row.statement_month, row.printed_balance)
            if sig in seen_opening_months:
                continue
            seen_opening_months.add(sig)
        kept.append(row)
    return kept


def dedupe_repeated_balance_rows(
    rows: list[ExtractedRow],
    opening_hint: float | None = None,
    printed: dict[str, float] | None = None,
) -> list[ExtractedRow]:
    """Remove duplicate zero-mutasi or leaked opening/carry-forward rows sharing a printed balance."""
    if not rows:
        return []

    def _printed_bal(r: ExtractedRow) -> float | None:
        if r.printed_balance is not None:
            return r.printed_balance
        if r.balance is not None and (r.has_printed_balance or r.computed_balance is None):
            return r.balance
        return None

    def _is_zero_mutasi(r: ExtractedRow) -> bool:
        return (
            (r.debit or 0.0) <= AUDIT_TOL
            and (r.credit or 0.0) <= AUDIT_TOL
            and abs(float(getattr(r, "pending_mutasi", 0.0) or 0.0)) <= AUDIT_TOL
        )

    def _has_nonzero_mutasi(r: ExtractedRow) -> bool:
        return (
            (r.debit or 0.0) > AUDIT_TOL
            or (r.credit or 0.0) > AUDIT_TOL
            or abs(float(getattr(r, "pending_mutasi", 0.0) or 0.0)) > AUDIT_TOL
        )

    def _is_opening_or_summary(r: ExtractedRow) -> bool:
        if _has_nonzero_mutasi(r):
            return False
        return bool(getattr(r, "is_opening", False)) or bool(OPENING_RE.match((r.description or "").strip()))

    for r in rows:
        p_bal = _printed_bal(r)
        if r.printed_balance is None and p_bal is not None:
            r.printed_balance = p_bal

    # Step 1: Per-date (date, printed_balance) deduplication (order-independent)
    date_buckets: dict[tuple[int, int, int], list[int]] = {}
    for idx, r in enumerate(rows):
        dk = indonesia_date_key(r)
        if dk == (0, 0, 0):
            continue
        if _printed_bal(r) is not None:
            date_buckets.setdefault(dk, []).append(idx)

    dropped_indices: set[int] = set()
    for dk_indices in date_buckets.values():
        if len(dk_indices) <= 1:
            continue
        clusters: list[tuple[float, list[int]]] = []
        for idx in dk_indices:
            bal = _printed_bal(rows[idx])
            if bal is None:
                continue
            matched_cluster = next(
                (members for rep_bal, members in clusters if abs(bal - rep_bal) <= AUDIT_TOL),
                None,
            )
            if matched_cluster is not None:
                matched_cluster.append(idx)
            else:
                clusters.append((bal, [idx]))

        for _, members in clusters:
            if len(members) <= 1:
                continue
            has_real_tx = any(
                not _is_opening_or_summary(rows[i]) and _has_nonzero_mutasi(rows[i])
                for i in members
            )
            if has_real_tx:
                for i in members:
                    if _is_zero_mutasi(rows[i]) or _is_opening_or_summary(rows[i]):
                        dropped_indices.add(i)
            elif all(_is_zero_mutasi(rows[i]) and not _is_opening_or_summary(rows[i]) for i in members):
                for i in members[1:]:
                    dropped_indices.add(i)

    step1_rows = [r for idx, r in enumerate(rows) if idx not in dropped_indices]

    # Step 2: Chronological sort, 1..N index assignment, and post-sort adjacent balance check
    sorted_rows, _ = sort_transactions_chronologically(
        step1_rows,
        opening_hint=opening_hint,
        printed=printed,
    )
    for idx, r in enumerate(sorted_rows, start=1):
        r.transaction_index = idx

    openings = [r for r in sorted_rows if r.is_opening]
    body = [r for r in sorted_rows if not r.is_opening]
    prev_bal = infer_seed_balance(openings, body, opening_hint, printed)

    kept: list[ExtractedRow] = []
    for i, r in enumerate(sorted_rows):
        bal = _printed_bal(r)
        if i == 0 and r.is_opening:
            kept.append(r)
            if bal is not None:
                prev_bal = bal
            continue

        if bal is not None and prev_bal is not None and abs(bal - prev_bal) <= AUDIT_TOL:
            if _is_opening_or_summary(r) or (
                _is_zero_mutasi(r)
                and len(re.sub(r"[^A-Z0-9]", "", (r.clean_description or r.description or "").upper())) < 4
            ):
                continue

        kept.append(r)
        if bal is not None:
            prev_bal = bal

    for idx, r in enumerate(kept, start=1):
        r.transaction_index = idx

    return kept


def slice_into_sessions(
    page_bundles: Sequence[tuple[int, dict, list[ExtractedRow]]],
    default_account: str = "UNKNOWN_ACCOUNT",
) -> list[StatementSession]:
    sessions: list[StatementSession] = []
    current_session: StatementSession | None = None
    last_printed_balance: float | None = None
    prev_printed_page: int | None = None
    prev_period_sig: tuple[Any, Any] | None = None

    def _parse_iso_date(val: Any) -> date | None:
        if isinstance(val, date):
            return val
        if isinstance(val, str) and val:
            try:
                return date.fromisoformat(val)
            except ValueError:
                return None
        return None

    def _spawn_session(diag: dict, initial_rows: list[ExtractedRow] | None = None) -> StatementSession:
        nonlocal last_printed_balance
        acc_num = diag.get("account_number") or diag.get("account") or default_account or "UNKNOWN_ACCOUNT"
        p_start = _parse_iso_date(diag.get("statement_period_start"))
        p_end = _parse_iso_date(diag.get("statement_period_end"))
        open_bal = next((r.printed_balance for r in (initial_rows or []) if r.is_opening and r.printed_balance is not None), None)
        sess = StatementSession(
            account_number=acc_num,
            period_start=p_start,
            period_end=p_end,
            opening_balance=open_bal,
            closing_balance=None,
            transactions=list(initial_rows or []),
            reported_totals={},
            is_sealed=False,
            session_index=len(sessions),
            pages=[diag.get("pdf_page_index", 1)],
        )
        sessions.append(sess)
        last_printed_balance = None
        for r in sess.transactions:
            if r.printed_balance is not None:
                last_printed_balance = r.printed_balance
        return sess

    for eff_page_idx, diag, p_rows in page_bundles:
        rep_totals = dict(diag.get("reported_totals") or {})
        page_sealed = bool(diag.get("is_sealed"))
        has_header = bool(diag.get("header"))
        cur_printed_page = diag.get("printed_page_number")
        cur_period_sig = (diag.get("statement_year"), diag.get("statement_month"))

        if not p_rows and not rep_totals:
            continue
        # Do not let a 0-transaction leading cover/summary page open a dummy session before real transactions start
        if not p_rows and current_session is None:
            continue

        if current_session is None:
            current_session = _spawn_session(diag)

        # Check Hard Seam Breaks at page boundary when current_session already has data
        if current_session.transactions or current_session.reported_totals:
            seam_break = False

            # Hard Seam Break 1: Previous session was sealed by a quarantined TOTAL / SALDO AKHIR line
            if current_session.is_sealed:
                seam_break = True

            # Hard Seam Break 2: Opening balance reset or new declared Account / Statement Period in metadata_rect
            if not seam_break:
                if any(r.is_opening for r in p_rows[:1]):
                    seam_break = True
                elif (
                    diag.get("page_declared_account")
                    and current_session.account_number not in ("", "UNKNOWN_ACCOUNT")
                    and diag["page_declared_account"] != current_session.account_number
                ):
                    seam_break = True
                elif (
                    has_header
                    and diag.get("statement_period_start")
                    and current_session.period_start is not None
                    and diag.get("statement_period_start") != current_session.period_start.isoformat()
                ):
                    seam_break = True
                elif (
                    has_header
                    and cur_period_sig[1] is not None
                    and prev_period_sig is not None
                    and prev_period_sig[1] is not None
                    and cur_period_sig != prev_period_sig
                ):
                    seam_break = True

            # Hard Seam Break 3: Unaccounted running balance jump at page boundary + repeating header
            if not seam_break and has_header and last_printed_balance is not None and p_rows:
                first_row = p_rows[0]
                if not first_row.is_opening and first_row.printed_balance is not None:
                    expected_bal = round(
                        last_printed_balance + (first_row.credit or 0.0) - (first_row.debit or 0.0),
                        2,
                    )
                    is_numbered_continuation = (
                        prev_printed_page is not None
                        and cur_printed_page is not None
                        and cur_printed_page == prev_printed_page + 1
                        and (cur_period_sig == prev_period_sig or cur_period_sig[1] is None)
                    )
                    if abs(expected_bal - first_row.printed_balance) > AUDIT_TOL and not is_numbered_continuation:
                        seam_break = True

            if seam_break:
                current_session = _spawn_session(diag)

        if eff_page_idx not in current_session.pages:
            current_session.pages.append(eff_page_idx)

        # Append page rows, splitting mid-page if a new SALDO AWAL row appears after transactions
        for r in p_rows:
            if r.is_opening and current_session.transactions:
                current_session = _spawn_session(diag, [r])
                continue
            current_session.transactions.append(r)
            if r.is_opening and current_session.opening_balance is None and r.printed_balance is not None:
                current_session.opening_balance = r.printed_balance
            if r.printed_balance is not None:
                last_printed_balance = r.printed_balance

        if rep_totals:
            current_session.reported_totals.update(rep_totals)
            if "closing_balance" in rep_totals and rep_totals["closing_balance"] is not None:
                last_printed_balance = rep_totals["closing_balance"]
        if page_sealed:
            current_session.is_sealed = True

        prev_printed_page = cur_printed_page
        if cur_period_sig[1] is not None:
            prev_period_sig = cur_period_sig

    return [s for s in sessions if s.transactions or s.reported_totals]


def forward_fill_session_balances(
    session: StatementSession,
    opening_hint: float | None = None,
) -> None:
    """Forward-fill missing/empty balance (saldo) cells sequentially per session
    using running_balance = running_balance + credit - debit seeded from Saldo Awal.
    """
    running_balance: float | None = (
        session.opening_balance
        if session.opening_balance is not None
        else next(
            (
                r.printed_balance if r.printed_balance is not None else r.balance
                for r in session.transactions
                if r.is_opening and (r.printed_balance is not None or r.balance is not None)
            ),
            session.reported_totals.get("opening", opening_hint),
        )
    )

    for row in session.transactions:
        if row.is_opening:
            open_bal = row.printed_balance if row.printed_balance is not None else row.balance
            if open_bal is not None:
                running_balance = open_bal
                row.balance = open_bal
            elif running_balance is not None:
                row.balance = running_balance
            continue

        credit = row.credit or 0.0
        debit = row.debit or 0.0
        if running_balance is not None:
            running_balance = round(running_balance + credit - debit, 2)

        if row.printed_balance is not None:
            row.balance = row.printed_balance
            running_balance = row.printed_balance
        elif row.balance is not None and row.has_printed_balance:
            row.printed_balance = row.balance
            running_balance = row.balance
        elif running_balance is not None:
            row.balance = running_balance
            if row.computed_balance is None:
                row.computed_balance = running_balance
        elif row.balance is not None:
            running_balance = row.balance
