import argparse
from collections import Counter
from datetime import date, datetime
from pathlib import Path
import re
from typing import Any, Sequence

from .audit import AUDIT_TOL, PENALTIES, audit_ledger, chain_matches, format_issue_date, row_month_pair
from .constants import DEFAULT_DPI, SUPPORTED_INPUT_SUFFIXES
from .export import excel_bytes, rows_df
from .forensics import detect_overlaid_streams, run_forensic_audit
from .layout import apply_dates, printed_totals, process_page
from .ocr import (
    extract_header_info,
    get_pdf_sanitize_report,
    iter_pages,
    load_and_sanitize_pdf,
    ocr_fallback_single_page,
)
from .structures import AuditIssue, ExtractedRow, StatementAuditResult, StatementSession, State


def has_internal_bucket_discrepancy(
    page_rows: Sequence[ExtractedRow],
    prev_closing_balance: float | None = None,
) -> bool:
    if not page_rows:
        return False

    def _check_sequence(seq: Sequence[ExtractedRow], start_bal: float | None) -> bool:
        curr_bal = start_bal
        net_fwd = 0.0
        net_rev = 0.0
        for r in seq:
            if r.is_opening:
                if r.printed_balance is not None:
                    curr_bal = r.printed_balance
                    net_fwd = 0.0
                    net_rev = 0.0
                continue
            cr = r.credit or 0.0
            db = r.debit or 0.0
            if curr_bal is None:
                if r.printed_balance is not None:
                    curr_bal = r.printed_balance
                continue
            net_fwd += cr - db
            net_rev += db - cr
            if r.printed_balance is not None:
                ok_fwd = abs((curr_bal + net_fwd) - r.printed_balance) <= AUDIT_TOL
                ok_rev = abs((curr_bal + net_rev) - r.printed_balance) <= AUDIT_TOL
                if not (ok_fwd or ok_rev):
                    return True
                curr_bal = r.printed_balance
                net_fwd = 0.0
                net_rev = 0.0
        return False

    return _check_sequence(page_rows, prev_closing_balance)


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


def group_key_label(bank: str, acc: str) -> str:
    clean_acc = "" if acc == "UNKNOWN_ACCOUNT" else acc
    if bank and clean_acc:
        return f"{bank} - {clean_acc}"
    return bank or clean_acc or "Default"


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


def slice_into_sessions(
    page_bundles: Sequence[tuple[int, dict, list[ExtractedRow]]],
    default_account: str = "UNKNOWN_ACCOUNT",
) -> list[StatementSession]:
    """Phase 4: In-Stream Session Slicing into StatementSession containers.

    Spawns a new StatementSession on any of 3 Hard Seam Breaks:
    1. Previous Session Sealed (current_session.is_sealed == True).
    2. Opening Balance Reset (new SALDO AWAL / OPENING BALANCE row, or metadata_rect declares
       a new Account Number or Statement Period).
    3. Unaccounted Running Balance Jump at a repeating-header page boundary
       (Prev Balance + Credit - Debit != Current Balance, tolerance > 0.05).
    """
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


def summarize_account(
    rows: list[ExtractedRow],
    printed: dict[str, float],
    paths: Sequence[Path] | None = None,
    overlaid_findings: Sequence[dict[str, Any]] | None = None,
    duplicate_issues: Sequence[AuditIssue] | None = None,
    stream_issues: Sequence[AuditIssue] | None = None,
    account_number: str = "UNKNOWN_ACCOUNT",
    bank_name: str = "",
    total_pages: int = 1,
    sessions: list[StatementSession] | None = None,
) -> tuple[dict[str, Any], StatementAuditResult]:
    """Phase 5: Two-Tier Post-Extraction Math Audit (Local Session Audit -> Global Continuity)."""
    if not sessions:
        single_session = StatementSession(
            account_number=account_number or "UNKNOWN_ACCOUNT",
            opening_balance=next((r.printed_balance for r in rows if r.is_opening and r.printed_balance is not None), printed.get("opening")),
            closing_balance=None,
            transactions=list(rows),
            reported_totals=dict(printed),
            is_sealed=bool(printed),
            session_index=0,
        )
        sessions = [single_session] if (rows or printed) else []

    all_session_issues: list[AuditIssue] = []
    all_discrepancy_alerts: list[dict[str, Any]] = []
    session_summaries: list[dict[str, Any]] = []
    master_rows: list[ExtractedRow] = []

    # Tier 1: Local Session Audit (Per StatementSession)
    for sess_idx, sess in enumerate(sessions):
        sess.session_index = sess_idx
        sess_printed: dict[str, float] = {}
        for k, v in sess.reported_totals.items():
            if v is None:
                continue
            if k == "closing_balance":
                sess_printed["closing"] = v
            elif k in ("debit", "credit", "opening", "closing"):
                sess_printed[k] = v

        if len(sessions) == 1 and printed:
            for k, v in printed.items():
                if k not in sess_printed and v is not None:
                    sess_printed[k] = v

        s_info = audit_ledger(
            sess.transactions,
            opening_hint=None if any(r.is_opening for r in sess.transactions) else (sess.opening_balance or sess_printed.get("opening")),
            printed=sess_printed,
        )
        eff_open = next(
            (r.printed_balance for r in sess.transactions if r.is_opening and r.printed_balance is not None),
            s_info.get("inferred_opening_balance", sess_printed.get("opening", sess.opening_balance)),
        )
        forward_fill_session_balances(sess, opening_hint=eff_open)

        s_body = [r for r in sess.transactions if not r.is_opening]
        s_open = eff_open
        s_bals = [
            r.printed_balance if r.printed_balance is not None else r.balance
            for r in s_body
            if (r.printed_balance is not None or r.balance is not None)
        ]
        s_close = s_bals[-1] if s_bals else sess_printed.get("closing", s_open)
        sess.opening_balance = s_open
        sess.closing_balance = s_close

        base_offset = len(master_rows)
        master_rows.extend(sess.transactions)
        for local_i, r in enumerate(sess.transactions):
            r.transaction_index = base_offset + local_i

        for issue in s_info.get("audit_issues", []):
            issue.transaction_index = base_offset + max(0, issue.transaction_index)
            all_session_issues.append(issue)

        for alert in s_info.get("math_discrepancy_alerts", []):
            if alert.get("transaction_index") is not None:
                alert["transaction_index"] = base_offset + max(0, int(alert["transaction_index"]))
            alert["session_index"] = sess_idx
            all_discrepancy_alerts.append(alert)

        session_summaries.append({
            "session_index": sess_idx,
            "account_number": sess.account_number,
            "period_start": sess.period_start.isoformat() if sess.period_start else None,
            "period_end": sess.period_end.isoformat() if sess.period_end else None,
            "opening_balance": sess.opening_balance,
            "closing_balance": sess.closing_balance,
            "total_debit": round(sum(r.debit or 0.0 for r in s_body), 2),
            "total_credit": round(sum(r.credit or 0.0 for r in s_body), 2),
            "reported_totals": dict(sess.reported_totals),
            "is_sealed": sess.is_sealed,
            "transaction_count": len(sess.transactions),
            "pages": list(sess.pages),
        })

    rows[:] = master_rows
    for global_idx, r in enumerate(rows):
        r.transaction_index = global_idx

    # Tier 2: Global Concatenation & Cross-Session Continuity
    for idx in range(len(sessions) - 1):
        s_prev = sessions[idx]
        s_next = sessions[idx + 1]
        if not s_next.transactions:
            continue
        seam_row = s_next.transactions[0]
        seam_tx_idx = seam_row.transaction_index

        # 1) Cross-session balance bridge check: Session[i].closing_balance == Session[i+1].opening_balance
        if (
            s_prev.closing_balance is not None
            and s_next.opening_balance is not None
            and abs(s_prev.closing_balance - s_next.opening_balance) > AUDIT_TOL
        ):
            gap = round(s_prev.closing_balance - s_next.opening_balance, 2)
            if "SESSION_CONTINUITY_GAP" not in seam_row.validation:
                seam_row.validation.append("SESSION_CONTINUITY_GAP")
            if "MISSING_PERIOD_OR_CONTINUITY_GAP" not in seam_row.validation:
                seam_row.validation.append("MISSING_PERIOD_OR_CONTINUITY_GAP")
            if seam_row.is_opening:
                seam_row.is_reconciled = False
                seam_row.reconciliation_status = "DISCREPANCY"
                seam_row.computed_balance = s_prev.closing_balance
                seam_row.balance_discrepancy = gap

            desc_msg = (
                f"Cross-session continuity gap between Session {s_prev.session_index} and Session {s_next.session_index} "
                f"at transaction_index={seam_tx_idx}: previous closing balance {s_prev.closing_balance:,.2f} != "
                f"next opening balance {s_next.opening_balance:,.2f} (gap {gap:+,.2f})."
            )
            all_session_issues.append(
                AuditIssue(
                    transaction_index=seam_tx_idx,
                    pdf_page=seam_row.pdf_page_index,
                    date=format_issue_date(seam_row.date),
                    issue_type="SESSION_CONTINUITY_GAP",
                    severity="HIGH",
                    printed_value=s_next.opening_balance,
                    expected_value=s_prev.closing_balance,
                    discrepancy=gap,
                    description=desc_msg,
                )
            )
            all_session_issues.append(
                AuditIssue(
                    transaction_index=seam_tx_idx,
                    pdf_page=seam_row.pdf_page_index,
                    date=format_issue_date(seam_row.date),
                    issue_type="MISSING_PERIOD_OR_CONTINUITY_GAP",
                    severity="HIGH",
                    printed_value=s_next.opening_balance,
                    expected_value=s_prev.closing_balance,
                    discrepancy=gap,
                    description=desc_msg,
                )
            )
            continue

        # 2) Non-consecutive month jump across sessions (e.g., Jan -> Mar skipping Feb)
        prev_last_ym = next((row_month_pair(r) for r in reversed(s_prev.transactions) if row_month_pair(r)), None)
        next_first_ym = next((row_month_pair(r) for r in s_next.transactions if row_month_pair(r)), None)
        if prev_last_ym and next_first_ym and prev_last_ym != next_first_ym:
            months_diff = (next_first_ym[0] - prev_last_ym[0]) * 12 + (next_first_ym[1] - prev_last_ym[1])
            if months_diff > 1:
                if "SESSION_CONTINUITY_GAP" not in seam_row.validation:
                    seam_row.validation.append("SESSION_CONTINUITY_GAP")
                if "MISSING_PERIOD_OR_CONTINUITY_GAP" not in seam_row.validation:
                    seam_row.validation.append("MISSING_PERIOD_OR_CONTINUITY_GAP")
                p_msg = (
                    f"Statement period jump between Session {s_prev.session_index} ({prev_last_ym[0]}-{prev_last_ym[1]:02d}) "
                    f"and Session {s_next.session_index} ({next_first_ym[0]}-{next_first_ym[1]:02d}) at transaction_index={seam_tx_idx}."
                )
                all_session_issues.append(
                    AuditIssue(
                        transaction_index=seam_tx_idx,
                        pdf_page=seam_row.pdf_page_index,
                        date=format_issue_date(seam_row.date),
                        issue_type="SESSION_CONTINUITY_GAP",
                        severity="MEDIUM",
                        printed_value=s_next.opening_balance,
                        expected_value=s_prev.closing_balance,
                        discrepancy=None,
                        description=p_msg,
                    )
                )
                all_session_issues.append(
                    AuditIssue(
                        transaction_index=seam_tx_idx,
                        pdf_page=seam_row.pdf_page_index,
                        date=format_issue_date(seam_row.date),
                        issue_type="MISSING_PERIOD_OR_CONTINUITY_GAP",
                        severity="MEDIUM",
                        printed_value=s_next.opening_balance,
                        expected_value=s_prev.closing_balance,
                        discrepancy=None,
                        description=p_msg,
                    )
                )

    forensics = run_forensic_audit(paths or [], rows, page_overlaid=overlaid_findings)

    for row in rows:
        row.confidence = max(0.0, round(1.0 - sum(PENALTIES.get(v, 0.2) for v in row.validation), 3))

    combined_issues: list[AuditIssue] = []
    combined_issues.extend(stream_issues or [])
    combined_issues.extend(duplicate_issues or [])
    combined_issues.extend(all_session_issues)
    combined_issues.extend(forensics.get("audit_issues", []))
    combined_issues.sort(key=lambda issue: (issue.transaction_index, issue.pdf_page, issue.issue_type))

    body = [r for r in rows if not r.is_opening]
    opening = (
        sessions[0].opening_balance
        if sessions and sessions[0].opening_balance is not None
        else next((r.printed_balance for r in rows if r.is_opening), printed.get("opening"))
    )
    balances = [
        r.printed_balance if r.printed_balance is not None else r.balance
        for r in body
        if (r.printed_balance is not None or r.balance is not None)
    ]
    total_debit = round(sum(r.debit or 0.0 for r in body), 2)
    total_credit = round(sum(r.credit or 0.0 for r in body), 2)
    closing = (
        sessions[-1].closing_balance
        if sessions and sessions[-1].closing_balance is not None
        else (balances[-1] if balances else printed.get("closing"))
    )

    has_continuity_gap = any(
        i.issue_type in ("SESSION_CONTINUITY_GAP", "MISSING_PERIOD_OR_CONTINUITY_GAP", "TOTAL_MISMATCH")
        for i in all_session_issues
    )
    if len(sessions) == 1 and opening is not None and closing is not None:
        summary_matches = abs((opening + total_credit - total_debit) - closing) <= AUDIT_TOL
        reconciles: bool | None = summary_matches and len(all_discrepancy_alerts) == 0 and not has_continuity_gap
    elif len(sessions) > 1:
        reconciles = (len(all_discrepancy_alerts) == 0) and not has_continuity_gap
    elif all_discrepancy_alerts or has_continuity_gap:
        reconciles = False
    else:
        reconciles = None

    tx_df = rows_df(rows)
    blocking_issues = [
        i for i in combined_issues
        if i.issue_type not in ("CORRUPT_STREAM_REPAIRED",)
    ]
    is_clean = (len(blocking_issues) == 0) and (reconciles is not False)
    audit_result = StatementAuditResult(
        account_number=account_number or "UNKNOWN_ACCOUNT",
        transactions_df=tx_df,
        issues=combined_issues,
        is_clean=is_clean,
        total_pages=max(total_pages, 1),
        bank_name=bank_name,
    )

    reconciled_count = sum(1 for r in rows if r.is_reconciled)
    reconciliation_pct = round(100.0 * reconciled_count / len(rows), 1) if rows else 100.0

    summary: dict[str, Any] = {
        "order": "as_printed",
        "debit_credit_swapped": False,
        "math_discrepancy_alerts": all_discrepancy_alerts,
        "opening_balance": opening,
        "closing_balance": closing,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "rows_flagged": sum(bool(r.validation) for r in rows),
        "reconciled_pct": reconciliation_pct,
        "chain_reconciles": reconciles,
        "sessions": session_summaries,
        "forensics": {
            k: v for k, v in forensics.items() if k != "audit_issues"
        },
        "issues": [vars(i) for i in combined_issues],
        "summary_line": audit_result.summary_line(),
    }

    if printed or any(s.reported_totals for s in sessions):
        if len(sessions) == 1:
            eff_printed = dict(printed)
            for k, v in sessions[0].reported_totals.items():
                if k == "closing_balance":
                    eff_printed["closing"] = v
                elif k in ("debit", "credit", "opening", "closing"):
                    eff_printed[k] = v
            summary["printed_totals"] = eff_printed
            computed = {"debit": total_debit, "credit": total_credit, "closing": closing, "opening": opening}
            summary["printed_totals_match"] = all(
                computed[k] is not None and abs(eff_printed[k] - computed[k]) <= AUDIT_TOL
                for k in eff_printed
                if k in computed
            )
        else:
            agg_printed: dict[str, float] = {}
            if all("debit" in s.reported_totals for s in sessions):
                agg_printed["debit"] = round(sum(s.reported_totals["debit"] for s in sessions), 2)
            if all("credit" in s.reported_totals for s in sessions):
                agg_printed["credit"] = round(sum(s.reported_totals["credit"] for s in sessions), 2)
            if sessions[-1].reported_totals.get("closing_balance") is not None:
                agg_printed["closing"] = sessions[-1].reported_totals["closing_balance"]
            summary["printed_totals"] = agg_printed or printed
            summary["printed_totals_match"] = not any(i.issue_type == "TOTAL_MISMATCH" for i in all_session_issues)
    return summary, audit_result


def extract_statement(pdf_path: str | Path | Sequence[str | Path], dpi: int = DEFAULT_DPI) -> dict[str, Any]:
    raw_paths = [pdf_path] if isinstance(pdf_path, (str, Path)) else list(pdf_path)
    if not raw_paths:
        raise ValueError("At least one input file is required.")
    input_paths = [Path(p) for p in raw_paths]
    for path in input_paths:
        if path.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
            raise ValueError(f"Unsupported file format: {path.suffix}. Needs: {', '.join(sorted(SUPPORTED_INPUT_SUFFIXES))}")

    pages: list[dict] = []
    all_years: list[int] = []
    account_page_bundles: dict[tuple[str, str], list[tuple[int, dict, list[ExtractedRow]]]] = {}
    account_printed: dict[tuple[str, str], dict[str, float]] = {}
    account_years: dict[tuple[str, str], list[int]] = {}
    account_paths: dict[tuple[str, str], list[Path]] = {}
    account_overlaid: dict[tuple[str, str], list[dict[str, Any]]] = {}

    global_pdf_page_counter = 0

    for path in input_paths:
        state = State()
        doc_page_bundles: list[tuple[int, dict, list[ExtractedRow]]] = []
        doc_texts: list[str] = []
        doc_overlaid: list[dict[str, Any]] = []

        hdr_bank, hdr_acc = extract_header_info(path)
        if hdr_bank:
            state.bank = hdr_bank
        if hdr_acc:
            state.account = hdr_acc

        sanitize_report = get_pdf_sanitize_report(path)
        flagged_pages_for_doc: set[int] = sanitize_report.get("flagged_pages", set())
        prev_page_closing: float | None = None

        for page_no, words, page_w, source, spans in iter_pages(path, dpi, include_spans=True):
            global_pdf_page_counter += 1
            eff_page_idx = global_pdf_page_counter if len(input_paths) > 1 else page_no
            page_rows, diag, page_text = process_page(words, page_w, page_no, state, spans=spans)

            # Step 3: Targeted RapidOCR Fallback if a stream-flagged page has missing rows / bucket balance gap
            if (
                source == "text"
                and (page_no in flagged_pages_for_doc or sanitize_report.get("is_corrupted"))
                and (
                    len(page_rows) == 0
                    or has_internal_bucket_discrepancy(page_rows, prev_page_closing)
                )
            ):
                fb_words, fb_w, fb_spans = ocr_fallback_single_page(path, page_no, dpi=max(dpi, 200))
                if fb_words:
                    fb_rows, fb_diag, fb_text = process_page(fb_words, fb_w, page_no, state, spans=fb_spans)
                    if len(fb_rows) >= len(page_rows):
                        page_rows, diag, page_text = fb_rows, fb_diag, fb_text
                        words, spans = fb_words, fb_spans
                source = "ocr_fallback"
                sanitize_report.setdefault("ocr_fallback_pages", set()).add(page_no)

            for r in page_rows:
                r.pdf_page_index = eff_page_idx
                if r.printed_balance is not None:
                    prev_page_closing = r.printed_balance

            overlaid_on_page = detect_overlaid_streams(eff_page_idx, spans, page_rows)
            doc_overlaid.extend(overlaid_on_page)
            diag["source"] = source
            diag["words"] = len(words)
            diag["pdf_page_index"] = eff_page_idx
            diag["local_page_no"] = page_no
            diag["file_path"] = str(path)
            if overlaid_on_page:
                diag["overlaid_findings"] = overlaid_on_page
            if len(input_paths) > 1:
                diag["file"] = path.name
            doc_page_bundles.append((eff_page_idx, diag, page_rows))
            doc_texts.append(page_text)

        doc_acc = hdr_acc or state.account or ""
        doc_bank = hdr_bank or state.bank or ""

        all_doc_rows = [r for _, _, p_rows in doc_page_bundles for r in p_rows]
        full_text = "\n".join(doc_texts)
        explicit = [r.date_parts[2] for r in all_doc_rows if r.date_parts and len(r.date_parts) >= 3 and r.date_parts[2]]
        if not explicit:
            explicit = [int(y) for y in re.findall(r"(?<![\d:])(20[0-3]\d|199\d)(?![\d:])", full_text)]
        explicit = [y for y in explicit if 1990 <= y <= 2100]
        doc_year = Counter(explicit).most_common(1)[0][0] if explicit else None
        if doc_year:
            all_years.append(doc_year)

        for _, diag, p_rows in doc_page_bundles:
            p_acc = diag.get("account") if (diag.get("account") and diag.get("account") != "UNKNOWN_ACCOUNT") else doc_acc
            p_bank = diag.get("bank") or doc_bank
            for row in p_rows:
                row.account = p_acc
                row.bank = p_bank
            diag["account"] = p_acc
            diag["account_number"] = p_acc or "UNKNOWN_ACCOUNT"
            diag["bank"] = p_bank
            pages.append(diag)

        apply_dates(all_doc_rows, doc_year)
        doc_printed = {**printed_totals(full_text), **state.printed}

        group_key = (doc_bank, doc_acc)
        account_page_bundles.setdefault(group_key, []).extend(doc_page_bundles)
        account_paths.setdefault(group_key, []).append(path)
        account_overlaid.setdefault(group_key, []).extend(doc_overlaid)
        if doc_printed:
            account_printed.setdefault(group_key, {}).update(doc_printed)
        if doc_year:
            account_years.setdefault(group_key, []).append(doc_year)

    for bnk, acc in list(account_page_bundles.keys()):
        if not bnk and acc:
            match_key = next(((b, a) for b, a in account_page_bundles if b and a == acc), None)
            if match_key:
                bundles_to_move = account_page_bundles.pop((bnk, acc))
                for _, diag, p_rows in bundles_to_move:
                    diag["bank"] = match_key[0]
                    for row in p_rows:
                        row.bank = match_key[0]
                account_page_bundles[match_key].extend(bundles_to_move)
                if (bnk, acc) in account_printed:
                    account_printed.setdefault(match_key, {}).update(account_printed.pop((bnk, acc)))
                if (bnk, acc) in account_years:
                    account_years.setdefault(match_key, []).extend(account_years.pop((bnk, acc)))
                if (bnk, acc) in account_paths:
                    account_paths.setdefault(match_key, []).extend(account_paths.pop((bnk, acc)))
                if (bnk, acc) in account_overlaid:
                    account_overlaid.setdefault(match_key, []).extend(account_overlaid.pop((bnk, acc)))

    if ("", "") in account_page_bundles and len(account_page_bundles) > 1:
        unknown_bundles = account_page_bundles.pop(("", ""))
        for _, diag, p_rows in unknown_bundles:
            diag["account"] = "Unknown"
            diag["account_number"] = "UNKNOWN_ACCOUNT"
            for row in p_rows:
                row.account = "Unknown"
        account_page_bundles[("", "Unknown")] = unknown_bundles
        if ("", "") in account_printed:
            account_printed[("", "Unknown")] = account_printed.pop(("", ""))
        if ("", "") in account_years:
            account_years[("", "Unknown")] = account_years.pop(("", ""))
        if ("", "") in account_paths:
            account_paths[("", "Unknown")] = account_paths.pop(("", ""))
        if ("", "") in account_overlaid:
            account_overlaid[("", "Unknown")] = account_overlaid.pop(("", ""))

    overall_year = Counter(all_years).most_common(1)[0][0] if all_years else None
    accounts_out: dict[str, dict[str, Any]] = {}
    audit_results_map: dict[str, StatementAuditResult] = {}
    combined_rows: list[ExtractedRow] = []

    for group_key, page_bundles in account_page_bundles.items():
        bank_name, acc = group_key
        years = account_years.get(group_key, [])
        acc_year = Counter(years).most_common(1)[0][0] if years else overall_year

        # Phase 2.2: Page Deduplication
        seen_hashes: set[int] = set()
        deduped_bundles: list[tuple[int, dict, list[ExtractedRow]]] = []
        duplicate_issues: list[AuditIssue] = []

        for eff_page_idx, diag, p_rows in page_bundles:
            sig_hash = page_signature_hash(p_rows)
            if sig_hash is not None and sig_hash in seen_hashes:
                diag["duplicate_dropped"] = True
                duplicate_issues.append(
                    AuditIssue(
                        transaction_index=0,
                        pdf_page=eff_page_idx,
                        date="",
                        issue_type="DUPLICATE_PAGE_DROPPED",
                        severity="LOW",
                        printed_value=None,
                        expected_value=None,
                        discrepancy=None,
                        description=f"Duplicate page (pdf_page_index={eff_page_idx}) dropped.",
                    )
                )
                continue
            if sig_hash is not None:
                seen_hashes.add(sig_hash)
            deduped_bundles.append((eff_page_idx, diag, p_rows))

        ordered_bundles = order_page_bundles(deduped_bundles, acc_year)
        deduped_rows = [r for _, _, p_rows in ordered_bundles for r in p_rows]
        apply_dates(deduped_rows, acc_year)

        # Phase 4: In-Stream Session Slicing into StatementSession containers
        sessions = slice_into_sessions(ordered_bundles, default_account=acc or "UNKNOWN_ACCOUNT")
        acc_open_hint = account_printed.get(group_key, {}).get("opening") if len(sessions) == 1 else None
        for sess in sessions:
            sess.transactions = dedupe_same_month_openings(sess.transactions)
            apply_dates(sess.transactions, acc_year)
            forward_fill_session_balances(sess, opening_hint=acc_open_hint)

        rows_list = [r for sess in sessions for r in sess.transactions]
        for idx, row in enumerate(rows_list):
            row.transaction_index = idx

        if not rows_list and not account_printed.get(group_key):
            continue

        stream_issues: list[AuditIssue] = []
        seen_repaired_paths: set[str] = set()
        for doc_path in account_paths.get(group_key, input_paths):
            resolved_str = str(Path(doc_path).resolve())
            if resolved_str in seen_repaired_paths:
                continue
            seen_repaired_paths.add(resolved_str)
            rep = get_pdf_sanitize_report(doc_path)
            if rep.get("corrupt_stream_repaired"):
                stream_issues.append(
                    AuditIssue(
                        transaction_index=0,
                        pdf_page=1,
                        date="",
                        issue_type="CORRUPT_STREAM_REPAIRED",
                        severity="LOW",
                        printed_value=None,
                        expected_value=None,
                        discrepancy=None,
                        description=f"Corrupted PDF object/zlib stream repaired in-memory ({Path(doc_path).name}).",
                    )
                )

        seen_fallback_pages: set[int] = set()
        for eff_page_idx, diag, _ in page_bundles:
            if diag.get("source") == "ocr_fallback" and eff_page_idx not in seen_fallback_pages:
                seen_fallback_pages.add(eff_page_idx)
                first_row_on_page = next((r for r in rows_list if r.pdf_page_index == eff_page_idx), None)
                tx_idx = first_row_on_page.transaction_index if first_row_on_page is not None else 0
                dt_str = (
                    first_row_on_page.date.isoformat()
                    if first_row_on_page is not None and isinstance(first_row_on_page.date, (date, datetime))
                    else str(first_row_on_page.date or "") if first_row_on_page is not None else ""
                )
                stream_issues.append(
                    AuditIssue(
                        transaction_index=tx_idx,
                        pdf_page=eff_page_idx,
                        date=dt_str,
                        issue_type="DIGITAL_STREAM_FAILED_OCR_FALLBACK",
                        severity="MEDIUM",
                        printed_value=None,
                        expected_value=None,
                        discrepancy=None,
                        description=f"Page {eff_page_idx} digital stream unrecoverable; recovered via RapidOCR pixel fallback.",
                    )
                )

        bank_name = bank_name or next((r.bank for r in rows_list if r.bank), "")
        acc_summary, audit_result = summarize_account(
            rows_list,
            account_printed.get(group_key, {}),
            paths=account_paths.get(group_key, input_paths),
            overlaid_findings=account_overlaid.get(group_key, []),
            duplicate_issues=duplicate_issues,
            stream_issues=stream_issues,
            account_number=acc or "UNKNOWN_ACCOUNT",
            bank_name=bank_name,
            total_pages=len(page_bundles),
            sessions=sessions,
        )
        print(audit_result.summary_line())

        acc_summary["bank"] = bank_name
        acc_summary["account"] = acc
        out_key = group_key_label(bank_name, acc)
        accounts_out[out_key] = {
            "account": acc,
            "bank": bank_name,
            "rows": rows_list,
            "sessions": sessions,
            "document_year": acc_year,
            "summary": acc_summary,
            "audit_result": audit_result,
        }
        audit_results_map[out_key] = audit_result
        combined_rows.extend(rows_list)

    if not accounts_out:
        empty_summary, empty_audit = summarize_account([], {}, paths=input_paths, total_pages=len(pages))
        print(empty_audit.summary_line())
        return {
            "pages": pages,
            "rows": [],
            "accounts": {},
            "document_year": overall_year,
            "summary": empty_summary,
            "audit_result": empty_audit,
            "audit_results": {},
        }

    if len(accounts_out) == 1:
        only = next(iter(accounts_out.values()))
        primary_audit = only["audit_result"]
        summary = {
            **only["summary"],
            "account": only["account"],
            "bank": only["bank"],
            "accounts": {k: v["summary"] for k, v in accounts_out.items()},
        }
    else:
        for global_idx, row in enumerate(combined_rows):
            row.transaction_index = global_idx
        reconciles = [v["summary"]["chain_reconciles"] for v in accounts_out.values() if v["summary"]["chain_reconciles"] is not None]
        totals_match = [v["summary"]["printed_totals_match"] for v in accounts_out.values() if "printed_totals_match" in v["summary"]]
        all_discrepancies = [
            alert
            for v in accounts_out.values()
            for alert in v["summary"].get("math_discrepancy_alerts", [])
        ]
        all_forensic_flags = sorted({
            fl
            for v in accounts_out.values()
            for fl in v["summary"].get("forensics", {}).get("flags", [])
        })
        all_issues = [
            issue
            for ar in audit_results_map.values()
            for issue in ar.issues
        ]
        primary_audit = StatementAuditResult(
            account_number="MULTI_ACCOUNT",
            transactions_df=rows_df(combined_rows),
            issues=all_issues,
            is_clean=len(all_issues) == 0,
            total_pages=len(pages),
        )
        summary = {
            "total_debit": round(sum(v["summary"]["total_debit"] for v in accounts_out.values()), 2),
            "total_credit": round(sum(v["summary"]["total_credit"] for v in accounts_out.values()), 2),
            "rows_flagged": sum(v["summary"]["rows_flagged"] for v in accounts_out.values()),
            "chain_reconciles": all(reconciles) if reconciles else None,
            "math_discrepancy_alerts": all_discrepancies,
            "issues": [vars(i) for i in all_issues],
            "summary_line": primary_audit.summary_line(),
            "forensics": {
                "is_suspicious": bool(all_forensic_flags),
                "flags": all_forensic_flags,
            },
            "accounts": {k: v["summary"] for k, v in accounts_out.items()},
        }
        if totals_match:
            summary["printed_totals_match"] = all(totals_match)

    return {
        "pages": pages,
        "rows": combined_rows,
        "accounts": accounts_out,
        "document_year": overall_year,
        "summary": summary,
        "audit_result": primary_audit,
        "audit_results": audit_results_map,
    }


extract_statements = extract_statement


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Indonesian bank statements to Excel.")
    parser.add_argument("file", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, default=Path("output"))
    args = parser.parse_args()
    result = extract_statement(args.file)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.xlsx").write_bytes(excel_bytes(result))


if __name__ == "__main__":
    main()
