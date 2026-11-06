from datetime import date, datetime
from typing import Any

from .constants import (
    AUDIT_TOL,
    EXTRACTION_CONFIDENCE_CONFLICT,
    EXTRACTION_CONFIDENCE_OVERLAID,
    OPENING_RE,
)
from .structures import AuditIssue, ExtractedRow

PENALTIES = {
    "MATH_DISCREPANCY_ALERT": 0.55,
    "MISSING_PERIOD_OR_CONTINUITY_GAP": 0.55,
    "SESSION_CONTINUITY_GAP": 0.55,
    "TOTAL_MISMATCH": 0.55,
    "EXTRACTION_CONFLICT": 0.5,
    "OVERLAID_TEXT": 0.6,
    "FONT_ANOMALY": 0.45,
    "balance_mismatch": 0.5,
    "direction_unknown": 0.35,
    "date_invalid": 0.3,
    "multiple_amounts": 0.3,
    "multiple_balances": 0.3,
    "pending_transaction": 0.1,
    "amount_column_inferred": 0.15,
}


def format_issue_date(dt: Any) -> str:
    if dt is None:
        return ""
    if isinstance(dt, (date, datetime)):
        return dt.isoformat()
    return str(dt)


def row_month_pair(row: ExtractedRow) -> tuple[int, int] | None:
    if isinstance(row.date, (date, datetime)):
        return (row.date.year, row.date.month)
    if row.statement_year and row.statement_month:
        return (row.statement_year, row.statement_month)
    return None


def signed_amount(row: ExtractedRow) -> float | None:
    if not (row.debit or row.credit):
        return None
    return (row.credit or 0.0) - (row.debit or 0.0)


def magnitude(row: ExtractedRow) -> float:
    signed = signed_amount(row)
    return abs(signed) if signed else (row.pending_mutasi or 0.0)


def chain_matches(seq: list[ExtractedRow]) -> tuple[int, int, int]:
    mag_ok = ok = inv = 0
    prev_bal: float | None = None
    acc_signed = 0.0
    acc_mag = 0.0
    for r in seq:
        bal = r.printed_balance if r.printed_balance is not None else r.balance
        if r.is_opening and bal is not None:
            prev_bal = bal
            acc_signed = 0.0
            acc_mag = 0.0
            continue
        mag = magnitude(r)
        signed = signed_amount(r)
        if signed is not None:
            acc_signed += signed
        acc_mag += mag
        if bal is not None and prev_bal is not None and acc_mag > 0:
            diff = bal - prev_bal
            if abs(abs(diff) - acc_mag) <= AUDIT_TOL:
                mag_ok += 1
            if abs(diff - acc_signed) <= AUDIT_TOL:
                ok += 1
            if abs(diff + acc_signed) <= AUDIT_TOL:
                inv += 1
            prev_bal = bal
            acc_signed = 0.0
            acc_mag = 0.0
        elif bal is not None and prev_bal is None:
            prev_bal = bal
            acc_signed = 0.0
            acc_mag = 0.0
    return mag_ok, ok, inv


def audit_ledger(
    rows: list[ExtractedRow],
    opening_hint: float | None = None,
    printed: dict[str, float] | None = None,
) -> dict[str, Any]:
    printed = printed or {}
    info: dict[str, Any] = {
        "order": "as_printed",
        "debit_credit_swapped": False,
        "math_discrepancy_alerts": [],
        "audit_issues": [],
    }

    for row in rows:
        if row.printed_balance is None and row.balance is not None and (row.has_printed_balance or row.computed_balance is None):
            row.printed_balance = row.balance
        elif row.balance is None and row.printed_balance is not None:
            row.balance = row.printed_balance
        row.has_printed_balance = row.printed_balance is not None
        if (
            not row.is_opening
            and OPENING_RE.match(row.description or "")
            and not (row.debit or row.credit or row.pending_mutasi)
            and row.printed_balance is not None
        ):
            row.is_opening = True

    openings = [r for r in rows if r.is_opening]
    body = [r for r in rows if not r.is_opening]

    if len({r.pdf_page_index for r in body}) <= 1 and chain_matches(openings[:1] + body[::-1])[1] > chain_matches(openings[:1] + body)[1]:
        body = body[::-1]
        rows[:] = openings[:1] + body
        info["order"] = "newest_first"

    for idx, row in enumerate(rows):
        row.transaction_index = idx

    checkpoint_balance: float | None = None
    if openings and openings[0].printed_balance is not None:
        checkpoint_balance = openings[0].printed_balance
    elif opening_hint is not None:
        checkpoint_balance = opening_hint
    elif printed.get("opening") is not None:
        checkpoint_balance = printed.get("opening")
    elif body:
        # Infer opening balance from the first checkpoint row in the stream
        acc_pre_checkpoint = 0.0
        for cand in body:
            s = signed_amount(cand)
            if s is None and cand.pending_mutasi is not None:
                break
            acc_pre_checkpoint += s or 0.0
            if cand.printed_balance is not None:
                checkpoint_balance = round(cand.printed_balance - acc_pre_checkpoint, 2)
                info["inferred_opening_balance"] = checkpoint_balance
                break

    if (
        not openings
        and body
        and body[0].printed_balance is not None
        and checkpoint_balance is not None
        and abs(checkpoint_balance - body[0].printed_balance) <= AUDIT_TOL
        and (signed_amount(body[0]) or 0.0) != 0.0
    ):
        checkpoint_balance = round(body[0].printed_balance - (signed_amount(body[0]) or 0.0), 2)
        info["inferred_opening_balance"] = checkpoint_balance

    initial_opening = checkpoint_balance
    previous_computed_balance: float | None = checkpoint_balance
    pending_bucket: list[ExtractedRow] = []
    discrepancy_alerts: list[dict[str, Any]] = []
    audit_issues: list[AuditIssue] = []

    prev_ym: tuple[int, int] | None = None
    body_ordinal = -1

    for row in rows:
        tx_idx = row.transaction_index
        cur_ym = row_month_pair(row)

        if row.is_opening:
            printed_bal = row.printed_balance
            if (
                tx_idx > 0
                and previous_computed_balance is not None
                and printed_bal is not None
                and abs(previous_computed_balance - printed_bal) > AUDIT_TOL
            ):
                gap = round(previous_computed_balance - printed_bal, 2)
                if "MISSING_PERIOD_OR_CONTINUITY_GAP" not in row.validation:
                    row.validation.append("MISSING_PERIOD_OR_CONTINUITY_GAP")
                row.is_reconciled = False
                row.reconciliation_status = "DISCREPANCY"
                row.computed_balance = previous_computed_balance
                row.balance_discrepancy = gap
                audit_issues.append(
                    AuditIssue(
                        transaction_index=tx_idx,
                        pdf_page=row.pdf_page_index,
                        date=format_issue_date(row.date),
                        issue_type="MISSING_PERIOD_OR_CONTINUITY_GAP",
                        severity="HIGH",
                        printed_value=printed_bal,
                        expected_value=previous_computed_balance,
                        discrepancy=gap,
                        description=(
                            f"Cross-month/page continuity gap at transaction_index={tx_idx}: "
                            f"previous closing balance {previous_computed_balance:,.2f} != "
                            f"new opening balance {printed_bal:,.2f}."
                        ),
                    )
                )
            else:
                row.is_reconciled = True
                row.reconciliation_status = "OPENING"
                row.computed_balance = printed_bal
                row.balance_discrepancy = 0.0 if printed_bal is not None else None

            if printed_bal is not None:
                checkpoint_balance = printed_bal
                previous_computed_balance = printed_bal
            row.checkpoint_balance = checkpoint_balance
            if cur_ym:
                prev_ym = cur_ym
            continue

        body_ordinal += 1

        # Cross-month seam check for non-consecutive month jumps
        if prev_ym and cur_ym and cur_ym != prev_ym:
            months_diff = (cur_ym[0] - prev_ym[0]) * 12 + (cur_ym[1] - prev_ym[1])
            if months_diff > 1:
                if "MISSING_PERIOD_OR_CONTINUITY_GAP" not in row.validation:
                    row.validation.append("MISSING_PERIOD_OR_CONTINUITY_GAP")
                audit_issues.append(
                    AuditIssue(
                        transaction_index=tx_idx,
                        pdf_page=row.pdf_page_index,
                        date=format_issue_date(row.date),
                        issue_type="MISSING_PERIOD_OR_CONTINUITY_GAP",
                        severity="MEDIUM",
                        printed_value=row.printed_balance,
                        expected_value=previous_computed_balance,
                        discrepancy=None,
                        description=(
                            f"Statement period jump detected from {prev_ym[0]}-{prev_ym[1]:02d} "
                            f"to {cur_ym[0]}-{cur_ym[1]:02d} at transaction_index={tx_idx}."
                        ),
                    )
                )
        if cur_ym:
            prev_ym = cur_ym

        prev_balance = previous_computed_balance
        if (
            row.pending_mutasi is not None
            and not (row.debit or row.credit)
            and prev_balance is not None
            and row.printed_balance is not None
        ):
            if abs((prev_balance + row.pending_mutasi) - row.printed_balance) <= AUDIT_TOL:
                row.credit = row.pending_mutasi
                row.pending_mutasi = None
                if "direction_unknown" in row.validation:
                    row.validation.remove("direction_unknown")
            elif abs((prev_balance - row.pending_mutasi) - row.printed_balance) <= AUDIT_TOL:
                row.debit = row.pending_mutasi
                row.pending_mutasi = None
                if "direction_unknown" in row.validation:
                    row.validation.remove("direction_unknown")

        credit = row.credit or 0.0
        debit = row.debit or 0.0
        printed_balance = row.printed_balance
        row.has_printed_balance = printed_balance is not None
        row.checkpoint_balance = checkpoint_balance

        if prev_balance is not None:
            expected: float | None = round(prev_balance + credit - debit, 2)
            computed_balance: float | None = expected
        else:
            expected = printed_balance
            computed_balance = printed_balance

        row.computed_balance = computed_balance
        if row.balance is None and computed_balance is not None:
            row.balance = computed_balance

        # Case A: printed_balance is None (Intermediate Row, e.g., BCA intra-day rows)
        if printed_balance is None:
            pending_bucket.append(row)
            row.reconciliation_status = "VERIFIED_BY_BUCKET"
            row.is_reconciled = True
            row.balance_discrepancy = None
            previous_computed_balance = computed_balance
            continue

        # Case B: printed_balance is not None (Checkpoint Row)
        if prev_balance is not None and expected is not None:
            balance_discrepancy = round(expected - printed_balance, 2)
            stated_minus_expected = round(printed_balance - expected, 2)
            row.balance_discrepancy = balance_discrepancy

            if abs(balance_discrepancy) <= AUDIT_TOL and row.pending_mutasi is None:
                row.is_reconciled = True
                row.reconciliation_status = "RECONCILED"
                for b_row in pending_bucket:
                    b_row.is_reconciled = True
                    b_row.reconciliation_status = "VERIFIED_BY_BUCKET"
                    b_row.checkpoint_balance = checkpoint_balance
                checkpoint_balance = printed_balance
                previous_computed_balance = printed_balance
                pending_bucket.clear()
            else:
                row.is_reconciled = False
                row.reconciliation_status = "DISCREPANCY"
                for b_row in pending_bucket:
                    b_row.is_reconciled = False
                    b_row.reconciliation_status = "DISCREPANCY"

                if "MATH_DISCREPANCY_ALERT" not in row.validation:
                    row.validation.append("MATH_DISCREPANCY_ALERT")

                bucket_start = pending_bucket[0].transaction_index if pending_bucket else tx_idx
                bucket_desc = (
                    f" (bucket rows {bucket_start}..{tx_idx})"
                    if pending_bucket
                    else ""
                )
                alert = {
                    "alert": "MATH_DISCREPANCY_ALERT",
                    "scope": "row",
                    "row_index": body_ordinal,
                    "transaction_index": tx_idx,
                    "bucket_start_index": bucket_start,
                    "bucket_end_index": tx_idx,
                    "page": row.pdf_page_index,
                    "stated_balance": printed_balance,
                    "printed_balance": printed_balance,
                    "expected_balance": computed_balance,
                    "computed_balance": computed_balance,
                    "delta": stated_minus_expected,
                    "balance_discrepancy": balance_discrepancy,
                }
                row.math_discrepancy = alert
                discrepancy_alerts.append(alert)
                audit_issues.append(
                    AuditIssue(
                        transaction_index=tx_idx,
                        pdf_page=row.pdf_page_index,
                        date=format_issue_date(row.date),
                        issue_type="BALANCE_MISMATCH",
                        severity="HIGH",
                        printed_value=printed_balance,
                        expected_value=computed_balance,
                        discrepancy=balance_discrepancy,
                        description=(
                            f"Balance mismatch at transaction_index={tx_idx}{bucket_desc}: "
                            f"computed {computed_balance:,.2f} vs printed {printed_balance:,.2f} "
                            f"(discrepancy {balance_discrepancy:+,.2f})."
                        ),
                    )
                )
                checkpoint_balance = printed_balance
                previous_computed_balance = printed_balance
                pending_bucket.clear()
        else:
            checkpoint_balance = printed_balance
            previous_computed_balance = printed_balance
            row.computed_balance = printed_balance
            row.balance_discrepancy = 0.0
            row.is_reconciled = True
            row.reconciliation_status = "RECONCILED"
            pending_bucket.clear()

    # Handle trailing unclosed bucket at end of statement
    if pending_bucket:
        closing_target = printed.get("closing")
        if closing_target is not None and previous_computed_balance is not None:
            closing_disc = round(previous_computed_balance - closing_target, 2)
            if abs(closing_disc) <= AUDIT_TOL and all(r.pending_mutasi is None for r in pending_bucket):
                for b_row in pending_bucket:
                    b_row.is_reconciled = True
                    b_row.reconciliation_status = "VERIFIED_BY_BUCKET"
            else:
                last_b = pending_bucket[-1]
                for b_row in pending_bucket:
                    b_row.is_reconciled = False
                    b_row.reconciliation_status = "DISCREPANCY"
                if "MATH_DISCREPANCY_ALERT" not in last_b.validation:
                    last_b.validation.append("MATH_DISCREPANCY_ALERT")
                alert = {
                    "alert": "MATH_DISCREPANCY_ALERT",
                    "scope": "row",
                    "row_index": body.index(last_b) if last_b in body else last_b.transaction_index,
                    "transaction_index": last_b.transaction_index,
                    "page": last_b.pdf_page_index,
                    "stated_balance": closing_target,
                    "printed_balance": closing_target,
                    "expected_balance": previous_computed_balance,
                    "computed_balance": previous_computed_balance,
                    "delta": round(closing_target - previous_computed_balance, 2),
                    "balance_discrepancy": closing_disc,
                }
                last_b.math_discrepancy = alert
                discrepancy_alerts.append(alert)
                audit_issues.append(
                    AuditIssue(
                        transaction_index=last_b.transaction_index,
                        pdf_page=last_b.pdf_page_index,
                        date=format_issue_date(last_b.date),
                        issue_type="BALANCE_MISMATCH",
                        severity="HIGH",
                        printed_value=closing_target,
                        expected_value=previous_computed_balance,
                        discrepancy=closing_disc,
                        description=(
                            f"Unreconciled trailing bucket ending at transaction_index={last_b.transaction_index}: "
                            f"computed {previous_computed_balance:,.2f} vs printed closing {closing_target:,.2f}."
                        ),
                    )
                )
        else:
            for b_row in pending_bucket:
                if b_row.pending_mutasi is not None:
                    b_row.is_reconciled = False
                    b_row.reconciliation_status = "DISCREPANCY"
                    if "MATH_DISCREPANCY_ALERT" not in b_row.validation:
                        b_row.validation.append("MATH_DISCREPANCY_ALERT")
                    alert = {
                        "alert": "MATH_DISCREPANCY_ALERT",
                        "scope": "row",
                        "row_index": body.index(b_row) if b_row in body else b_row.transaction_index,
                        "transaction_index": b_row.transaction_index,
                        "page": b_row.pdf_page_index,
                        "stated_balance": None,
                        "printed_balance": None,
                        "expected_balance": b_row.computed_balance,
                        "computed_balance": b_row.computed_balance,
                        "delta": None,
                        "balance_discrepancy": None,
                    }
                    b_row.math_discrepancy = alert
                    discrepancy_alerts.append(alert)
                    audit_issues.append(
                        AuditIssue(
                            transaction_index=b_row.transaction_index,
                            pdf_page=b_row.pdf_page_index,
                            date=format_issue_date(b_row.date),
                            issue_type="BALANCE_MISMATCH",
                            severity="HIGH",
                            printed_value=None,
                            expected_value=b_row.computed_balance,
                            discrepancy=None,
                            description=f"Unresolved transaction direction/balance at transaction_index={b_row.transaction_index}.",
                        )
                    )

    balances = [r.printed_balance for r in body if r.printed_balance is not None]
    total_debit = round(sum(r.debit or 0.0 for r in body), 2)
    total_credit = round(sum(r.credit or 0.0 for r in body), 2)
    stated_closing = balances[-1] if balances else printed.get("closing")

    summary_invariant_ok: bool | None = None
    if initial_opening is not None and stated_closing is not None:
        expected_closing = round(initial_opening + total_credit - total_debit, 2)
        summary_delta = round(stated_closing - expected_closing, 2)
        summary_discrepancy = round(expected_closing - stated_closing, 2)
        summary_invariant_ok = abs(summary_delta) <= AUDIT_TOL
        if not summary_invariant_ok:
            last_tx_idx = body[-1].transaction_index if body else 0
            last_page = body[-1].pdf_page_index if body else 1
            summary_alert = {
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
                "balance_discrepancy": summary_discrepancy,
            }
            info["summary_discrepancy"] = summary_alert
            discrepancy_alerts.append(summary_alert)
            if not any(i.transaction_index == last_tx_idx and i.issue_type == "BALANCE_MISMATCH" for i in audit_issues):
                audit_issues.append(
                    AuditIssue(
                        transaction_index=last_tx_idx,
                        pdf_page=last_page,
                        date=format_issue_date(body[-1].date) if body else "",
                        issue_type="BALANCE_MISMATCH",
                        severity="HIGH",
                        printed_value=stated_closing,
                        expected_value=expected_closing,
                        discrepancy=summary_discrepancy,
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
            if "summary_discrepancy" not in info:
                info["summary_discrepancy"] = printed_alert
            discrepancy_alerts.append(printed_alert)
            summary_invariant_ok = False

    # Phase 5.1.2: Summary Verification against quarantined TOTAL line (debit / credit)
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
                    AuditIssue(
                        transaction_index=last_tx_idx,
                        pdf_page=last_page,
                        date=format_issue_date(body[-1].date) if body else "",
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

    for row in rows:
        row.confidence = max(0.0, round(1.0 - sum(PENALTIES.get(v, 0.2) for v in row.validation), 3))
        if EXTRACTION_CONFIDENCE_OVERLAID in row.validation:
            row.extraction_confidence = EXTRACTION_CONFIDENCE_OVERLAID
        elif EXTRACTION_CONFIDENCE_CONFLICT in row.validation:
            row.extraction_confidence = EXTRACTION_CONFIDENCE_CONFLICT
            audit_issues.append(
                AuditIssue(
                    transaction_index=row.transaction_index,
                    pdf_page=row.pdf_page_index,
                    date=format_issue_date(row.date),
                    issue_type="EXTRACTION_CONFLICT",
                    severity="HIGH",
                    printed_value=row.printed_balance,
                    expected_value=row.computed_balance,
                    discrepancy=row.balance_discrepancy,
                    description=f"Lexical/alpha contamination or extraction conflict at transaction_index={row.transaction_index}.",
                )
            )

    info["math_discrepancy_alerts"] = discrepancy_alerts
    info["audit_issues"] = audit_issues
    info["summary_invariant_ok"] = summary_invariant_ok
    if initial_opening is not None and not openings and opening_hint is None and "opening" not in printed:
        info["opening_inferred"] = True
    return info


reconcile = audit_ledger

__all__ = ["AUDIT_TOL", "PENALTIES", "audit_ledger", "chain_matches", "reconcile"]
