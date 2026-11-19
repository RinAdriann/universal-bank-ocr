from __future__ import annotations

import math
import os
import re
import statistics
from collections import Counter
from datetime import date
from typing import Any, Sequence

from component.core import (
    ALL_ROLE_WORDS,
    Cell,
    ColumnInterval,
    EXTRACTION_CONFIDENCE_CONFLICT,
    EXTRACTION_CONFIDENCE_HIGH,
    EXTRACTION_CONFIDENCE_MEDIUM,
    EXTRACTION_CONFIDENCE_OVERLAID,
    ExtractedRow,
    FOOTER_BOILERPLATE_RE,
    Layout,
    Line,
    MONTH_MAP,
    MONTHS,
    NUMERIC_ROLES,
    OPENING_RE,
    PURE_TIME_RE,
    PageFingerprint,
    ROLE_KEYS,
    RowBand,
    STOP_RE,
    SUMMARY_INTERCEPT_TOKENS,
    Span,
    State,
    TABLE_HEADER_KEYWORDS,
    Word,
    drop_flag,
    normalize_reported_totals,
)
from component.ledger import resolve_pending
from component.parsing import (
    clean_text, description_direction, detect_account, explicit_direction, extract_clean_description,
    extract_counterparty_name, extract_page_fingerprint, has_alpha_contamination, hint_direction,
    is_int_amount, is_money, line_direction, match_date, norm_money, parse_amount, parse_time,
)
from component.table import (
    _FORMATTED_THOUSANDS_RE,
    _UNFORMATTED_ECHO_AMT_RE,
    _extract_cell_time, _extract_leading_time, _parse_four_totals, _stitch_wrapped_numeric_cells,
    best_match, build_date_row_bands, build_dominant_font_profile, cluster, detect_overlaid_spans,
    detect_start, discover_column_intervals, edge, filter_time_only_starts, filter_numeric_column_spans, find_header,
    find_table_header_band, get_text_role, group_lines, intercept_summary_line, learn_numeric,
    median_height, resolve_numeric_consensus, summary_band,
)


# ---------------------------------------------------------------------------
# Row Builder
# ---------------------------------------------------------------------------


def apply_dates(
    rows: Sequence[ExtractedRow],
    year: int | None,
    statement_month: int | None = None,
    period_start: date | None = None,
    state: State | None = None,
) -> None:
    """Phase 2: Two-Stage Contextual Date Resolver.

    Stage 1 (Strict Day-First & Indonesian Month Lexicon):
      Group 1 -> Day, Group 2 -> Month, Group 3 -> Year (optional). Never flips to US MM/DD.
    Stage 2 (Stateful Date Carry-Forward & Monotonic Year Rollover):
      Maintains active_date, active_month, active_year across rows and pages.
      Applies monotonic year rollover (active_month == 12 and month == 1 -> active_year += 1).
      Inherits active_date on blank-date same-day transaction rows.
    """
    if not rows:
        return

    active_year = (state.active_year if state and state.active_year else None) or year
    active_month = (state.active_month if state and state.active_month else None) or statement_month
    active_date = state.active_date if state else None

    for row in rows:
        if row.date_parts and len(row.date_parts) == 4:
            day, month, row_year, _ = row.date_parts
            row.date_parts = (day, month, row_year)

    for idx in range(1, len(rows) - 1):
        prev_p, cur_p, next_p = rows[idx - 1].date_parts, rows[idx].date_parts, rows[idx + 1].date_parts
        if prev_p and cur_p and next_p:
            m_prev, y_prev = prev_p[1], prev_p[2] or rows[idx].statement_year or active_year
            m_next, y_next = next_p[1], next_p[2] or rows[idx].statement_year or active_year
            if (m_prev, y_prev) == (m_next, y_next) and (cur_p[1], cur_p[2] or rows[idx].statement_year or active_year) != (m_prev, y_prev):
                rows[idx].date_parts = (cur_p[0], m_prev, y_prev)

    for row in rows:
        if row.date_parts is None:
            if not row.is_opening and active_date is not None:
                row.date = active_date
                if isinstance(active_date, date):
                    row.date_parts = (active_date.day, active_date.month, active_date.year)
                    row.statement_year = row.statement_year or active_date.year
                    row.statement_month = row.statement_month or active_date.month
            continue

        day, month, row_year = row.date_parts[:3]
        # Statement-period year anchor: a row year more than 1 year away from the
        # header period is a misparsed token (e.g. HH.MM.SS read as DD.MM.YY).
        # Treat it as dateless so the bogus year is never carried forward.
        anchor_year = period_start.year if period_start is not None else year
        if row_year is not None and anchor_year is not None and abs(row_year - anchor_year) > 1:
            if "date_year_out_of_period" not in row.validation:
                row.validation.append("date_year_out_of_period")
            if not row.is_opening and active_date is not None:
                row.date = active_date
                if isinstance(active_date, date):
                    row.date_parts = (active_date.day, active_date.month, active_date.year)
                    row.statement_year = row.statement_year or active_date.year
                    row.statement_month = row.statement_month or active_date.month
                continue
            row_year = None
            row.date_parts = (day, month, None)
        if row_year is not None:
            ry = row_year
            active_year = ry
            active_month = month
        elif active_year is not None:
            if active_month == 12 and month == 1:
                active_year += 1
            elif row.statement_month == 12 and month == 1 and active_month is None:
                active_year += 1
            ry = active_year
            active_month = month
        elif row.statement_year is not None:
            ry = row.statement_year + (1 if (row.statement_month == 12 and month == 1) else 0)
            active_year = ry
            active_month = month
        else:
            ry = None
            active_month = month

        if ry is None:
            row.date = f"{day:02d}/{month:02d}"
            row.statement_month = row.statement_month or month
            active_date = row.date
        else:
            try:
                resolved_dt = date(ry, month, day)
                row.date = resolved_dt
                row.statement_year = row.statement_year or resolved_dt.year
                row.statement_month = row.statement_month or resolved_dt.month
                active_date = resolved_dt
            except ValueError:
                if "date_invalid" not in row.validation:
                    row.validation.append("date_invalid")
                row.date = f"{day:02d}/{month:02d}/{ry}"
                row.statement_year = row.statement_year or ry
                row.statement_month = row.statement_month or month
                active_date = row.date

    # Look-Ahead Borrowing for SALDO AWAL / Dateless Opening Header Rows
    for idx, row in enumerate(rows):
        if row.date is not None:
            continue
        if row.is_opening or idx == 0:
            if period_start is not None:
                row.date = period_start
                row.date_parts = (period_start.day, period_start.month, period_start.year)
                row.statement_year = row.statement_year or period_start.year
                row.statement_month = row.statement_month or period_start.month
                if active_date is None:
                    active_date = period_start
                continue

            next_dated = next(
                (r for r in rows[idx + 1 :] if r.date is not None or (r.date_parts and r.date_parts[1])),
                None,
            )
            if next_dated is not None:
                if isinstance(next_dated.date, date):
                    borrowed = date(next_dated.date.year, next_dated.date.month, 1)
                    row.date = borrowed
                    row.date_parts = (1, borrowed.month, borrowed.year)
                    row.statement_year = row.statement_year or borrowed.year
                    row.statement_month = row.statement_month or borrowed.month
                elif next_dated.date_parts and next_dated.date_parts[1]:
                    m_next = next_dated.date_parts[1]
                    y_next = next_dated.date_parts[2] or row.statement_year or active_year
                    if y_next is not None:
                        borrowed = date(y_next, m_next, 1)
                        row.date = borrowed
                        row.date_parts = (1, m_next, y_next)
                        row.statement_year = row.statement_year or y_next
                        row.statement_month = row.statement_month or m_next
                    else:
                        row.date = f"01/{m_next:02d}"
                        row.date_parts = (1, m_next, None)
                        row.statement_month = row.statement_month or m_next
            elif (row.statement_year or active_year) and (row.statement_month or active_month):
                sy = row.statement_year or active_year
                sm = row.statement_month or active_month
                if sy and sm:
                    borrowed = date(sy, sm, 1)
                    row.date = borrowed
                    row.date_parts = (1, sm, sy)

    # Look-Behind Fallback for any remaining dateless rows
    for idx in range(1, len(rows)):
        row = rows[idx]
        if row.date is None and rows[idx - 1].date is not None:
            prev_row = rows[idx - 1]
            row.date = prev_row.date
            row.date_parts = prev_row.date_parts
            row.statement_year = row.statement_year or prev_row.statement_year
            row.statement_month = row.statement_month or prev_row.statement_month

    if state is not None:
        if active_year is not None:
            state.active_year = active_year
        if active_month is not None:
            state.active_month = active_month
        if active_date is not None:
            state.active_date = active_date


def build_opening_rows(
    lines: Sequence[Line],
    region: Sequence[int],
    first_start: int | None,
    height: float,
    page_no: int,
    fingerprint: PageFingerprint,
    state: State,
) -> tuple[list[ExtractedRow], list[str]]:
    rows: list[ExtractedRow] = []
    pre_desc: list[str] = []
    scan_limit = first_start if first_start is not None else len(lines)
    for i in region:
        if i >= scan_limit:
            break
        line = lines[i]
        text = line.text
        if OPENING_RE.match(text) and not rows:
            bal = [c for c in line.cells if c.money and c.assigned_column == "balance"] or [c for c in line.cells if c.money][-1:]
            if bal:
                amt = parse_amount(bal[-1].text)
                if amt:
                    desc_str = (clean_text(" ".join(c.text for c in line.cells if not c.money)) or "SALDO AWAL").upper()
                    rows.append(
                        ExtractedRow(
                            page=page_no,
                            pdf_page_index=page_no,
                            printed_page_number=fingerprint.printed_page_number,
                            statement_year=fingerprint.statement_year,
                            statement_month=fingerprint.statement_month,
                            row_top_y=line.y0,
                            raw_text=clean_text(text).upper(),
                            description=desc_str,
                            clean_description=extract_clean_description(desc_str),
                            counterparty_name="",
                            balance=amt.value,
                            printed_balance=amt.value,
                            has_printed_balance=True,
                            is_opening=True,
                            account=state.account,
                            bank=state.bank,
                        )
                    )
            continue
        if first_start is not None and lines[first_start].y - line.y <= 2.5 * height and not any(c.money for c in line.cells):
            pre_desc.append(text)
    return rows, pre_desc


_STANDALONE_BAL_NEG_RE = re.compile(r"^(?:(?:IDR|RP\.?)\s*)?-$", re.IGNORECASE)


def _in_gutter_x(
    cell: Cell,
    col: dict[str, Any] | None,
    target_cell: Cell,
    tol: float,
    height: float,
) -> bool:
    if col is not None:
        cx0 = col.get("x0", col["edge"] - 8.5 * height) - 1.5 * height
        cx1 = col.get("x1", col["edge"]) + max(tol, 2.0 * height)
        if cx0 <= cell.xc <= cx1:
            return True
    return (target_cell.x0 - 1.5 * height) <= cell.xc <= (target_cell.x1 + max(tol, 2.0 * height))


def _resolve_band_adjacent_markers(
    band: RowBand,
    numeric: Sequence[dict[str, Any]],
    tol: float,
    height: float,
) -> tuple[dict[int, str], set[int], set[int]]:
    """Bind adjacent-line CR/DB gutter markers to amount cells and above-line '-' signs to balance cells."""
    adj_amount_dirs: dict[int, str] = {}
    neg_balance_ids: set[int] = set()
    consumed_ids: set[int] = set()

    amt_col = next((n for n in numeric if n.get("assigned_column") == "amount"), None)
    bal_col = next((n for n in numeric if n.get("assigned_column") == "balance"), None)
    lines_in_band = band.lines

    for idx, line in enumerate(lines_in_band):
        for cell in line.cells:
            is_amt_cell = (
                (cell.money or cell.contaminated)
                and (cell.assigned_column == "amount" or (cell.assigned_column is None and cell.money))
            )
            if is_amt_cell:
                for adj_idx in (idx - 1, idx + 1):
                    if not (0 <= adj_idx < len(lines_in_band)):
                        continue
                    adj_line = lines_in_band[adj_idx]
                    if any(
                        (ac.money or ac.contaminated) and ac.assigned_column in ("amount", "debit", "credit")
                        for ac in adj_line.cells
                    ):
                        continue
                    for m_cell in adj_line.cells:
                        if m_cell.money or m_cell.assigned_column is not None:
                            continue
                        tok = m_cell.text.strip().upper()
                        if tok in ("CR", "DB") and _in_gutter_x(m_cell, amt_col, cell, tol, height):
                            adj_amount_dirs[id(cell)] = "credit" if tok == "CR" else "debit"
                            consumed_ids.add(id(m_cell))
                            break
                    if id(cell) in adj_amount_dirs:
                        break

            is_bal_cell = (cell.money or cell.contaminated) and cell.assigned_column == "balance"
            if is_bal_cell and idx > 0:
                prev_line = lines_in_band[idx - 1]
                if not any((bc.money or bc.contaminated) and bc.assigned_column == "balance" for bc in prev_line.cells):
                    for m_cell in prev_line.cells:
                        if m_cell.money or m_cell.assigned_column is not None:
                            continue
                        if _STANDALONE_BAL_NEG_RE.fullmatch(m_cell.text.strip()) and _in_gutter_x(
                            m_cell, bal_col, cell, tol, height
                        ):
                            neg_balance_ids.add(id(cell))
                            consumed_ids.add(id(m_cell))
                            break

    return adj_amount_dirs, neg_balance_ids, consumed_ids


def _finalize_band_row(cur: ExtractedRow, band: RowBand) -> None:
    """Apply EOD balance checkpoint, opening-row detection/credit-echo suppression, and pending direction."""
    eod_bal = getattr(band, "eod_balance", None)
    if eod_bal is not None and cur.printed_balance is None:
        cur.printed_balance = eod_bal
        cur.balance = eod_bal
        cur.has_printed_balance = True

    if (
        not cur.is_opening
        and OPENING_RE.match(cur.description or "")
        and cur.printed_balance is not None
        and not cur.debit
        and not cur.pending_mutasi
        and (not cur.credit or abs(cur.credit - cur.printed_balance) <= 0.01)
    ):
        cur.is_opening = True

    if (
        cur.is_opening
        and cur.credit is not None
        and cur.printed_balance is not None
        and abs(cur.credit - cur.printed_balance) <= 0.01
        and not cur.debit
    ):
        cur.credit = None

    if cur.pending_mutasi is not None and not cur.debit and not cur.credit:
        narrative_dir = (
            cur.pending_dir
            or explicit_direction(cur.raw_text)
            or description_direction(cur.description)
            or hint_direction(cur.description)
        )
        if narrative_dir == "credit":
            cur.credit = cur.pending_mutasi
            cur.pending_mutasi = None
        elif narrative_dir == "debit":
            cur.debit = cur.pending_mutasi
            cur.pending_mutasi = None
        else:
            if "direction_unknown" not in cur.validation:
                cur.validation.append("direction_unknown")


def build_row_from_band(
    band: RowBand,
    page_no: int,
    fingerprint: PageFingerprint,
    state: State,
    pre_desc: Sequence[str],
    date_x1: float,
    height: float,
    time_col_ranges: Sequence[tuple[float, float]],
    numeric: Sequence[dict[str, Any]],
    col_by_role: dict[str, dict[str, Any]],
    tol: float,
    numeric_table_spans: Sequence[Span],
    dominant_font_profile: Any,
    overlaid_pairs: Any,
    text_cols: Sequence[dict[str, Any]],
) -> ExtractedRow:
    row_time = band.row_time
    rest_text = band.rest_text
    if row_time is None and rest_text:
        parsed_exact = parse_time(rest_text)
        if parsed_exact is not None:
            row_time = parsed_exact
            rest_text = ""
        else:
            lead_t, stripped_rest = _extract_leading_time(rest_text)
            if lead_t is not None:
                row_time = lead_t
                rest_text = stripped_rest

    cur = ExtractedRow(
        page=page_no,
        pdf_page_index=page_no,
        printed_page_number=fingerprint.printed_page_number,
        statement_year=fingerprint.statement_year,
        statement_month=fingerprint.statement_month,
        row_top_y=band.row_top_y,
        raw_text="",
        date_parts=band.date_parts,
        account=state.account,
        bank=state.bank,
        time=row_time,
        extraction_confidence=EXTRACTION_CONFIDENCE_HIGH,
    )
    if band.date_parts is None and band.date_word is not None:
        cur.validation.append("pending_transaction")

    desc_parts: list[str] = list(pre_desc) if band.index == 0 else []
    if rest_text and parse_time(rest_text) is None:
        desc_parts.append(rest_text)
    ref_parts: list[str] = []
    extra_parts: list[str] = []
    raw_parts: list[str] = list(pre_desc) if band.index == 0 else []
    adj_amount_dirs, neg_balance_ids, consumed_marker_ids = _resolve_band_adjacent_markers(
        band, numeric, tol, height
    )

    for line_offset, line in enumerate(band.lines):
        is_date_line = band.date_word is not None and any(band.date_word in c.words for c in line.cells)
        if is_date_line:
            di = next(
                (idx for idx, c in enumerate(line.cells) if band.date_word in c.words),
                0,
            )
            raw_parts.append(line.cells[di].text)
            cells = line.cells[di + 1 :]
            if cells:
                raw_parts.append(" ".join(c.text for c in cells))
                m_sec = match_date(cells[0].text)
                if m_sec and not cells[0].text[m_sec[1]:].strip():
                    ref_parts.append(cells[0].text)
                    cells = cells[1:]
        else:
            cells = line.cells
            raw_parts.append(line.text)

        line_dir = line_direction(cells)
        for cell in cells:
            if cur.time is None:
                parsed_t = _extract_cell_time(cell, date_x1, height, time_col_ranges)
                if parsed_t is not None:
                    cur.time = parsed_t
                    continue

            if (cell.money or cell.contaminated) and cell.assigned_column:
                is_stitched_multiline = len({round(w.y0, 1) for w in cell.words}) > 1
                stitched_amt = parse_amount(cell.text) if is_stitched_multiline else None
                if stitched_amt is not None:
                    detected_amount = stitched_amt.value
                    assigned_column = cell.assigned_column
                    direction = stitched_amt.direction or line_dir or adj_amount_dirs.get(id(cell))
                    is_overlaid_consensus = False
                    consensus_dir = stitched_amt.direction
                else:
                    matched_col = min(
                        (n for n in numeric if n["assigned_column"] == cell.assigned_column),
                        key=lambda n: abs(n["edge"] - cell.num_x1),
                        default=col_by_role.get(cell.assigned_column),
                    )
                    consensus = resolve_numeric_consensus(
                        cell=cell,
                        matched_col=matched_col,
                        y_start=band.row_top_y,
                        y_end=band.y_end,
                        tol=tol,
                        spans=band.spans or numeric_table_spans,
                        dominant_profile=dominant_font_profile,
                        overlaid_pairs=overlaid_pairs,
                        height=height,
                    )
                    for flag in consensus.flags:
                        if flag not in cur.validation:
                            cur.validation.append(flag)
                    if consensus.extraction_confidence in (EXTRACTION_CONFIDENCE_OVERLAID, EXTRACTION_CONFIDENCE_CONFLICT):
                        cur.extraction_confidence = consensus.extraction_confidence
                    elif (
                        consensus.extraction_confidence == EXTRACTION_CONFIDENCE_MEDIUM
                        and cur.extraction_confidence == EXTRACTION_CONFIDENCE_HIGH
                    ):
                        cur.extraction_confidence = EXTRACTION_CONFIDENCE_MEDIUM

                    if consensus.profile is not None:
                        cur.numeric_profiles.append(consensus.profile)

                    if consensus.detected_amount is None:
                        continue

                    detected_amount = consensus.detected_amount
                    assigned_column = cell.assigned_column
                    direction = consensus.direction or line_dir or adj_amount_dirs.get(id(cell))
                    is_overlaid_consensus = EXTRACTION_CONFIDENCE_OVERLAID in consensus.flags
                    consensus_dir = consensus.direction

                if assigned_column == "balance":
                    norm = norm_money(cell.text)
                    is_neg = (
                        norm.startswith(("-", "("))
                        or norm.endswith(("DB", "DR", "-"))
                        or consensus_dir == "debit"
                        or id(cell) in neg_balance_ids
                    )
                    printed_bal = -abs(detected_amount) if is_neg else detected_amount
                    if cur.printed_balance is not None:
                        if "multiple_balances" not in cur.validation:
                            cur.validation.append("multiple_balances")
                        if is_overlaid_consensus:
                            cur.printed_balance = printed_bal
                            cur.balance = printed_bal
                    else:
                        cur.printed_balance = printed_bal
                        cur.balance = printed_bal
                    cur.has_printed_balance = cur.printed_balance is not None
                elif assigned_column in ("debit", "credit"):
                    if detected_amount != 0:
                        existing_val = getattr(cur, assigned_column)
                        if existing_val is not None and existing_val != 0.0:
                            cur.validation.append("multiple_amounts")
                        else:
                            setattr(cur, assigned_column, abs(detected_amount))
                    elif getattr(cur, assigned_column) is None:
                        setattr(cur, assigned_column, 0.0)
                elif assigned_column == "amount" and detected_amount != 0:
                    if "debit" not in col_by_role and "credit" not in col_by_role:
                        cur.single_amount_col = True
                    if direction == "credit":
                        if cur.credit is not None:
                            cur.validation.append("multiple_amounts")
                        else:
                            cur.credit = abs(detected_amount)
                    elif direction == "debit":
                        if cur.debit is not None:
                            cur.validation.append("multiple_amounts")
                        else:
                            cur.debit = abs(detected_amount)
                    else:
                        if cur.pending_mutasi is not None:
                            cur.validation.append("multiple_amounts")
                        else:
                            cur.pending_mutasi = abs(detected_amount)
            elif cell.money and cell.assigned_column is None and cur.pending_mutasi is None and cur.debit is None and cur.credit is None:
                if _UNFORMATTED_ECHO_AMT_RE.fullmatch(cell.text.strip()) and any(
                    _FORMATTED_THOUSANDS_RE.search(c.text)
                    and (a := parse_amount(c.text)) is not None
                    and (ca := parse_amount(cell.text)) is not None
                    and abs(a.value - ca.value) <= 0.01
                    for ln in band.lines
                    for c in ln.cells
                    if c.assigned_column in ("debit", "credit", "amount")
                ):
                    desc_parts.append(cell.text)
                    continue
                amount = parse_amount(cell.text)
                if amount and amount.value != 0:
                    if "debit" not in col_by_role and "credit" not in col_by_role:
                        cur.single_amount_col = True
                    direction = amount.direction or line_dir or adj_amount_dirs.get(id(cell))
                    if direction == "credit":
                        cur.credit = abs(amount.value)
                    elif direction == "debit":
                        cur.debit = abs(amount.value)
                    else:
                        cur.pending_mutasi = abs(amount.value)
                    cur.validation.append("amount_column_inferred")
            else:
                if id(cell) in consumed_marker_ids:
                    continue
                role = get_text_role(cell, text_cols, height)
                if role in ("desc", "ref") or (role == "other" and not cell.money):
                    desc_parts.append(cell.text)
                elif role == "extra":
                    extra_parts.append(cell.text)
                elif role == "date" and not PURE_TIME_RE.match(cell.text):
                    ref_parts.append(cell.text)

    if not desc_parts and extra_parts:
        desc_parts = extra_parts
    cur.description = clean_text(" ".join(desc_parts)).upper()
    cur.reference = clean_text(" ".join(ref_parts)).upper()
    cur.extra = clean_text(" ".join(extra_parts)).upper()
    cur.raw_text = clean_text(" ".join(raw_parts)).upper()
    cur.clean_description = extract_clean_description(cur.description)
    cur.counterparty_name = ""
    cur.has_printed_balance = cur.printed_balance is not None

    _finalize_band_row(cur, band)

    return cur


def postprocess_rows(
    rows: list[ExtractedRow],
    state: State,
    fingerprint: PageFingerprint,
    numeric: Sequence[dict[str, Any]],
) -> list[ExtractedRow]:
    from component.ner import get_transaction_ner

    kept = [
        r
        for r in rows
        if r.is_opening
        or r.printed_balance is not None
        or r.debit
        or r.credit
        or r.pending_mutasi
        or (
            EXTRACTION_CONFIDENCE_CONFLICT in r.validation
            and (r.date_parts is not None or bool(r.numeric_profiles))
        )
        or EXTRACTION_CONFIDENCE_OVERLAID in r.validation
    ]

    # Filter out redundant daily balance restatements (same balance as previous row with no debit or credit movement)
    filtered_kept: list[ExtractedRow] = []
    curr_bal: float | None = getattr(state, "balance", None)
    for r in kept:
        if (
            not r.is_opening
            and not (r.debit or r.credit or r.pending_mutasi)
            and r.printed_balance is not None
            and curr_bal is not None
            and abs(r.printed_balance - curr_bal) < 0.01
        ):
            continue
        filtered_kept.append(r)
        if r.printed_balance is not None:
            curr_bal = r.printed_balance
    kept = filtered_kept

    # Resolve remaining pending_mutasi via running balance delta when available
    resolved_dirs: list[str] = []
    prev_bal: float | None = getattr(state, "balance", None)
    for r in kept:
        if r.is_opening:
            if r.printed_balance is not None:
                prev_bal = r.printed_balance
            continue
        if r.pending_mutasi is not None and not r.debit and not r.credit and prev_bal is not None and r.printed_balance is not None:
            tol_bal = max(1.0, 1e-4 * max(abs(prev_bal), abs(r.printed_balance)))
            resolved = resolve_pending(r, prev_bal, tol=tol_bal, prefer="debit")
            if resolved:
                resolved_dirs.append(resolved)
        if r.printed_balance is not None:
            prev_bal = r.printed_balance
    setattr(state, "balance", prev_bal)

    # If a 2-column cropped table had a single active mutation column and all balance-verified rows
    # share one direction, apply that column direction to the leading row if its predecessor balance was cropped
    if resolved_dirs and len(set(resolved_dirs)) == 1 and len(numeric) == 2:
        unanimous_dir = resolved_dirs[0]
        for r in kept:
            if not r.is_opening and r.pending_mutasi is not None and not r.debit and not r.credit:
                setattr(r, unanimous_dir, r.pending_mutasi)
                r.pending_mutasi = None
                drop_flag(r, "direction_unknown")

    # For Testing with or without NER
    enable_ner = os.getenv("DISABLE_NER", "0") not in ("1", "true", "True")

    if kept and enable_ner:
        ner_inputs = [
            "" if r.is_opening else " ".join(filter(None, (r.description, r.extra)))
            for r in kept
        ]
        ner_results = get_transaction_ner().extract_batch(ner_inputs)
        for r, ner_res in zip(kept, ner_results):
            if not r.is_opening:
                r.counterparty_name = ner_res.get("counterparty", "")

    page_years = [r.date_parts[2] for r in kept if r.date_parts and len(r.date_parts) >= 3 and r.date_parts[2]]
    effective_year = (
        fingerprint.statement_year
        or state.active_year
        or (Counter(page_years).most_common(1)[0][0] if page_years else None)
    )
    apply_dates(
        kept,
        effective_year,
        statement_month=fingerprint.statement_month or state.active_month,
        period_start=fingerprint.statement_period_start,
        state=state,
    )

    return kept


# ---------------------------------------------------------------------------
# Page Processing
# ---------------------------------------------------------------------------


def process_page(
    words: list[Word],
    page_w: float,
    page_no: int,
    state: State,
    spans: list[Span] | None = None,
) -> tuple[list[ExtractedRow], dict, str]:
    from component.ner import get_transaction_ner

    diag: dict[str, Any] = {"page": page_no, "pdf_page_index": page_no, "rows": 0}
    if not words:
        return [], diag, ""
    page_spans = spans or []
    height = median_height(words)
    lines = group_lines(words, height)
    page_text = "\n".join(line.text for line in lines)
    # Cross-page split summary table handling (e.g. Imam Tohari where header is at bottom of page X and 4 values are at top of page X+1)
    if state.has_pending_summary_table:
        for idx, line in enumerate(lines[:8]):
            parsed = _parse_four_totals(line.cells, check_balance=True)
            if parsed is not None:
                v0, v1, v2, v3 = parsed["opening"], parsed["debit"], parsed["credit"], parsed["closing"]
                state.printed["opening"] = v0
                state.printed["debit"] = v1
                state.printed["credit"] = v2
                state.printed["closing"] = v3
                state.has_pending_summary_table = False
                state.is_sealed = True
                diag.update({
                    "account": state.account,
                    "account_number": state.account or "UNKNOWN_ACCOUNT",
                    "bank": state.bank,
                    "reported_totals": {
                        "opening": v0,
                        "debit": v1,
                        "credit": v2,
                        "closing_balance": v3,
                    },
                    "is_sealed": True,
                    "rows": 0,
                })
                return [], diag, page_text

    band_totals = summary_band(lines, state=state)
    if band_totals:
        state.printed.update(band_totals)

    # Phase 1.1 & 1.2 & 1.3: Dynamic Horizon & Spatial Page Splitting
    is_ocr_page = bool(page_spans and all(s.font == "OCR" for s in page_spans))
    if (
        (state.last_is_ocr is not None and state.last_is_ocr != is_ocr_page)
        or (state.last_page_w > 0 and abs(page_w - state.last_page_w) > 3.0)
    ):
        state.layout = None
        state.header_numeric = []
    state.last_is_ocr = is_ocr_page
    state.last_page_w = page_w

    header_band = find_table_header_band(words)
    header = find_header(lines, height)
    if header:
        state.header_numeric, state.layout = header[2], None
    diag["header"] = bool(header or header_band)
    diag["header_band"] = list(header_band) if header_band else None

    if header:
        first = header[1] + 1
        header_y0 = lines[header[0]].y0
        header_y1 = lines[header[1]].y1
        if header_band is not None:
            header_band = (min(header_band[0], header_y0), max(header_band[1], header_y1))
        else:
            header_band = (header_y0, header_y1)
    elif header_band:
        header_y0, header_y1 = header_band
        first = next((i for i, ln in enumerate(lines) if ln.y0 > header_y1), len(lines))
    else:
        first = 0
        header_y0 = None
        header_y1 = None
    diag["header_band"] = list(header_band) if header_band else None

    prelim_starts = {i: s for i in range(first, len(lines)) if (s := detect_start(lines[i], page_w))}
    prelim_starts = filter_time_only_starts(lines, prelim_starts)
    first_start = min(prelim_starts) if prelim_starts else None

    if header_y0 is not None:
        # Phase 1.2: Metadata Region strictly above header_y0 - 2
        meta_lines = [ln for ln in lines if ln.y1 <= header_y0 + 2.0]
    else:
        # Fallback if unformatted/no titles exist: above first valid Date + Amount line y0
        h_end = first_start if first_start is not None else (0 if page_no > 1 and state.has_rows else len(lines))
        meta_lines = lines[:h_end]
    meta_text = "\n".join(line.text for line in meta_lines)

    page_detected_account = detect_account(meta_text)
    if page_no == 1 and not state.account and page_detected_account:
        state.account = page_detected_account

    fingerprint = extract_page_fingerprint(
        page_text=meta_text if (header_y0 is not None or first_start is not None) else page_text,
        header_text=meta_text,
        pdf_page_index=page_no,
        fallback_account=state.account,
        fallback_bank=state.bank,
    )
    if fingerprint.account_number != "UNKNOWN_ACCOUNT" and not state.account:
        state.account = fingerprint.account_number

    # Seed or update active_year / active_month from metadata_rect when present
    if fingerprint.statement_year is not None and state.active_year is None:
        state.active_year = fingerprint.statement_year
    if fingerprint.statement_month is not None and state.active_month is None:
        state.active_month = fingerprint.statement_month
    if fingerprint.statement_period_start is not None and state.active_date is None:
        state.active_year = fingerprint.statement_period_start.year
        state.active_month = fingerprint.statement_period_start.month

    diag["account"] = state.account or fingerprint.account_number
    diag["account_number"] = state.account or fingerprint.account_number
    diag["page_declared_account"] = page_detected_account
    diag["bank"] = state.bank
    diag["printed_page_number"] = fingerprint.printed_page_number
    diag["statement_month"] = fingerprint.statement_month
    diag["statement_year"] = fingerprint.statement_year
    diag["statement_period_start"] = (
        fingerprint.statement_period_start.isoformat() if fingerprint.statement_period_start else None
    )
    diag["statement_period_end"] = (
        fingerprint.statement_period_end.isoformat() if fingerprint.statement_period_end else None
    )

    # Phase 1.3 Case B: On headerless continuation pages, scan from y = 0 downward for the first valid transaction line
    if header is None and header_band is None and page_no > 1 and state.layout is not None:
        first_txn_idx = 0
        for idx, ln in enumerate(lines):
            if idx in prelim_starts or OPENING_RE.match(ln.text):
                first_txn_idx = idx
                break
            if any(c.money for c in ln.cells) and not FOOTER_BOILERPLATE_RE.search(ln.text):
                first_txn_idx = idx
                break
        first = first_txn_idx

    region = list(range(first, len(lines)))

    col_intervals = discover_column_intervals([lines[i] for i in region], page_w, height)
    date_interval = next((c for c in col_intervals if c.assigned_column == "date"), None)
    date_col_limit = (date_interval.x1 + 2.0 * height) if date_interval else (0.55 * page_w)

    starts = {
        i: s
        for i in region
        if (s := detect_start(lines[i], page_w, date_col_x1=date_col_limit))
    }
    starts = filter_time_only_starts(lines, starts)
    if not starts and prelim_starts:
        starts = {i: s for i, s in prelim_starts.items() if i in region}
    first_start = min(starts) if starts else None

    date_x1 = max((lines[i].cells[starts[i][0]].x1 for i in starts), default=0.0)
    non_summary_region = [
        i for i in region
        if not STOP_RE.match(lines[i].text)
        and not (
            (header_band is None or lines[i].y0 > header_band[1])
            and intercept_summary_line(lines[i], has_date=(i in starts), header_band=header_band)[0]
        )
    ]
    formatted_money_count = sum(
        1 for i in (non_summary_region or region) for c in lines[i].cells if c.money and c.num_x1 > date_x1
    )
    allow_plain_ints = formatted_money_count < 3
    money_cells = [
        c
        for i in (non_summary_region or region)
        for c in lines[i].cells
        if (
            c.money
            or (
                c.is_int
                and (
                    c.text.lstrip().startswith(("+", "-"))
                    or (allow_plain_ints and len(re.sub(r"\D", "", c.text)) >= 4)
                )
            )
        )
        and c.num_x1 > date_x1
    ]
    layout = state.layout
    if money_cells:
        if header is None and state.header_numeric and is_ocr_page:
            no_hdr_learned = learn_numeric(money_cells, [], height, page_w)
            if no_hdr_learned and all(
                min(abs(col["edge"] - hx) for hx, _ in state.header_numeric) > 3.5 * height
                for col in no_hdr_learned[1]
            ):
                learned = no_hdr_learned
            else:
                learned = learn_numeric(money_cells, state.header_numeric, height, page_w)
        else:
            learned = learn_numeric(money_cells, state.header_numeric, height, page_w)
    else:
        learned = None
    if learned and (len(starts) >= 3 or layout is None):
        layout = Layout(*learned, [], column_intervals=col_intervals)
    elif layout is None:
        return [], diag, page_text
    align, numeric, tol = layout.align, layout.numeric, layout.tol

    _stitch_wrapped_numeric_cells(
        lines,
        region,
        starts,
        numeric,
        date_x1,
        tol,
        height,
        header_band=header_band,
    )

    for i in region:
        for cell in lines[i].cells:
            if not (cell.money or cell.is_int or cell.contaminated):
                cell.assigned_column = None
                continue
            if cell.num_x1 <= date_x1:
                cell.assigned_column = None
                continue
            cell_e = edge(cell, align)
            best = min(numeric, key=lambda n: abs(n["edge"] - cell_e), default=None)
            is_wide_or_signed = (
                cell.money
                or cell.contaminated
                or cell.text.lstrip().startswith(("+", "-"))
                or len(re.sub(r"\D", "", cell.text)) >= 4
            )
            allowed_tol = tol if is_wide_or_signed else 0.5 * height
            in_gutter = bool(
                best
                and (
                    abs(best["edge"] - cell_e) <= allowed_tol
                    or (
                        is_wide_or_signed
                        and best["assigned_column"] in ("debit", "credit", "amount", "balance")
                        and cell.x0 >= best.get("x0", best["edge"] - 8.5 * height) - 1.5 * height
                        and cell_e <= best.get("x1", best["edge"]) + allowed_tol
                    )
                )
            )
            matched_col = best if in_gutter else None
            if (
                matched_col is not None
                and not allow_plain_ints
                and not cell.money
                and cell.is_int
                and not cell.text.lstrip().startswith(("+", "-"))
            ):
                if not (
                    matched_col["assigned_column"] == "balance"
                    and abs(best["edge"] - cell_e) <= 0.5 * height
                ):
                    matched_col = None
            cell.assigned_column = matched_col["assigned_column"] if matched_col else None
            if matched_col and not cell.contaminated:
                cell.money = True

    n_start = len(starts)
    text_cells = [
        c
        for i, (di, _, _, _) in starts.items()
        for j, c in enumerate(lines[i].cells)
        if j > di and not ((c.money or c.contaminated) and c.assigned_column)
    ]
    date_starts = [lines[i].cells[di].x0 for i, (di, _, _, _) in starts.items()]
    text_cols: list[dict] = layout.text_cols if (layout.text_cols and n_start < 3) else []
    if not text_cols and date_starts:
        thr = 1 if n_start <= 2 else max(2, math.ceil(0.15 * n_start))
        clusters = [c for c in cluster(text_cells, lambda c: c.x0, 0.8 * height) if len(c) >= thr]
        date_start = statistics.median(date_starts)
        if (state.bank in ("BBRI", "BRI")) and date_interval:
            date_bound = date_interval.x1 - 1.0 * height
        else:
            date_bound = date_start + 0.8 * height
        cols = [
            {"start": statistics.median(c.x0 for c in cl), "chars": sum(len(c.text) for c in cl)}
            for cl in clusters
            if statistics.median(c.x0 for c in cl) > date_bound
        ]
        if cols:
            desc = max(cols, key=lambda c: c["chars"])
            for col in cols:
                col["role"] = "desc" if col is desc else ("ref" if col["start"] < desc["start"] else "extra")
        text_cols = [{"start": date_start, "role": "date"}] + sorted(cols, key=lambda c: c["start"])
        layout.text_cols = text_cols
    state.layout = layout

    def text_role(cell: Cell) -> str:
        role = next((col["role"] for col in reversed(text_cols) if col["start"] - 1.0 * height <= cell.x0), None)
        if role in (None, "other") and not cell.money and not PURE_TIME_RE.match(cell.text):
            return "desc"
        return role or "other"

    pre_y_min = lines[region[0]].y0 if region else 0.0
    pre_y_max = lines[region[-1]].y1 if region else float("inf")
    numeric_pre_spans = filter_numeric_column_spans(page_spans, numeric, pre_y_min, pre_y_max, height)

    rows: list[ExtractedRow] = []

    pre_desc: list[str] = []
    scan_limit = first_start if first_start is not None else len(lines)
    for i in region:
        if i >= scan_limit:
            break
        line = lines[i]
        text = line.text
        if OPENING_RE.match(text) and not rows:
            bal = [c for c in line.cells if c.money and c.assigned_column == "balance"] or [c for c in line.cells if c.money][-1:]
            if bal:
                amt = parse_amount(bal[-1].text)
                if amt:
                    desc_str = (clean_text(" ".join(c.text for c in line.cells if not c.money)) or "SALDO AWAL").upper()
                    rows.append(
                        ExtractedRow(
                            page=page_no,
                            pdf_page_index=page_no,
                            printed_page_number=fingerprint.printed_page_number,
                            statement_year=fingerprint.statement_year,
                            statement_month=fingerprint.statement_month,
                            row_top_y=line.y0,
                            raw_text=clean_text(text).upper(),
                            description=desc_str,
                            clean_description=extract_clean_description(desc_str),
                            counterparty_name="",
                            balance=amt.value,
                            printed_balance=amt.value,
                            has_printed_balance=True,
                            is_opening=True,
                            account=state.account,
                            bank=state.bank,
                        )
                    )
            continue
        if first_start is not None and lines[first_start].y - line.y <= 2.5 * height and not any(c.money for c in line.cells):
            pre_desc.append(text)

    allow_dateless_start = bool(page_no > 1 and state.has_rows and header is None and header_band is None)
    row_bands, quarantined_totals, page_is_sealed = build_date_row_bands(
        lines,
        starts,
        region,
        words,
        numeric_pre_spans,
        height,
        allow_dateless_start=allow_dateless_start,
        header_band=header_band,
    )
    table_y_min = row_bands[0].row_top_y if row_bands else pre_y_min
    table_y_max = row_bands[-1].y_end if row_bands else pre_y_max
    if rows:
        table_y_min = min(table_y_min, rows[0].row_top_y)
    numeric_table_spans = [
        sp for sp in numeric_pre_spans
        if (table_y_min - height) <= sp.yc <= (table_y_max + height)
    ]
    dominant_font_profile = build_dominant_font_profile(numeric_table_spans, table_y_min, table_y_max)
    overlaid_pairs = detect_overlaid_spans(numeric_table_spans)
    if quarantined_totals:
        state.printed.update(normalize_reported_totals(quarantined_totals))
    state.is_sealed = page_is_sealed
    col_by_role = {n["assigned_column"]: n for n in numeric}

    time_col_ranges: list[tuple[float, float]] = []
    if header:
        for k in range(header[0], header[1] + 1):
            for hc in lines[k].cells:
                if re.search(r"\b(?:JAM|WAKTU|TIME)\b", hc.text, re.IGNORECASE) and not re.search(
                    r"\b(?:TANGGAL|TGL|DATE)\b", hc.text, re.IGNORECASE
                ):
                    time_col_ranges.append((hc.x0 - 2.0 * height, hc.x1 + 2.0 * height))

    for band in row_bands:
        row_time = band.row_time
        rest_text = band.rest_text
        if row_time is None and rest_text:
            parsed_exact = parse_time(rest_text)
            if parsed_exact is not None:
                row_time = parsed_exact
                rest_text = ""
            else:
                lead_t, stripped_rest = _extract_leading_time(rest_text)
                if lead_t is not None:
                    row_time = lead_t
                    rest_text = stripped_rest

        cur = ExtractedRow(
            page=page_no,
            pdf_page_index=page_no,
            printed_page_number=fingerprint.printed_page_number,
            statement_year=fingerprint.statement_year,
            statement_month=fingerprint.statement_month,
            row_top_y=band.row_top_y,
            raw_text="",
            date_parts=band.date_parts,
            account=state.account,
            bank=state.bank,
            time=row_time,
            extraction_confidence=EXTRACTION_CONFIDENCE_HIGH,
        )
        if band.date_parts is None and band.date_word is not None:
            cur.validation.append("pending_transaction")

        desc_parts: list[str] = list(pre_desc) if band.index == 0 else []
        if band.rest_text and not PURE_TIME_RE.match(band.rest_text):
            desc_parts.append(band.rest_text)
        ref_parts: list[str] = []
        extra_parts: list[str] = []
        raw_parts: list[str] = list(pre_desc) if band.index == 0 else []
        adj_amount_dirs, neg_balance_ids, consumed_marker_ids = _resolve_band_adjacent_markers(
            band, numeric, tol, height
        )

        for line_offset, line in enumerate(band.lines):
            is_date_line = band.date_word is not None and any(band.date_word in c.words for c in line.cells)
            if is_date_line:
                di = next(
                    (idx for idx, c in enumerate(line.cells) if band.date_word in c.words),
                    0,
                )
                raw_parts.append(line.cells[di].text)
                cells = line.cells[di + 1 :]
                if cells:
                    raw_parts.append(" ".join(c.text for c in cells))
                    m_sec = match_date(cells[0].text)
                    if m_sec and not cells[0].text[m_sec[1]:].strip():
                        ref_parts.append(cells[0].text)
                        cells = cells[1:]
            else:
                cells = line.cells
                raw_parts.append(line.text)

            line_dir = line_direction(cells)
            for cell in cells:
                if cur.time is None:
                    parsed_t = _extract_cell_time(cell, date_x1, height, time_col_ranges)
                    if parsed_t is not None:
                        cur.time = parsed_t
                        continue

                if (cell.money or cell.contaminated) and cell.assigned_column:
                    is_stitched_multiline = len({round(w.y0, 1) for w in cell.words}) > 1
                    stitched_amt = parse_amount(cell.text) if is_stitched_multiline else None
                    if stitched_amt is not None:
                        detected_amount = stitched_amt.value
                        assigned_column = cell.assigned_column
                        direction = stitched_amt.direction or line_dir or adj_amount_dirs.get(id(cell))
                        is_overlaid_consensus = False
                        consensus_dir = stitched_amt.direction
                    else:
                        matched_col = min(
                            (n for n in numeric if n["assigned_column"] == cell.assigned_column),
                            key=lambda n: abs(n["edge"] - cell.num_x1),
                            default=col_by_role.get(cell.assigned_column),
                        )
                        consensus = resolve_numeric_consensus(
                            cell=cell,
                            matched_col=matched_col,
                            y_start=band.row_top_y,
                            y_end=band.y_end,
                            tol=tol,
                            spans=band.spans or numeric_table_spans,
                            dominant_profile=dominant_font_profile,
                            overlaid_pairs=overlaid_pairs,
                            height=height,
                        )
                        for flag in consensus.flags:
                            if flag not in cur.validation:
                                cur.validation.append(flag)
                        if consensus.extraction_confidence in (EXTRACTION_CONFIDENCE_OVERLAID, EXTRACTION_CONFIDENCE_CONFLICT):
                            cur.extraction_confidence = consensus.extraction_confidence
                        elif (
                            consensus.extraction_confidence == EXTRACTION_CONFIDENCE_MEDIUM
                            and cur.extraction_confidence == EXTRACTION_CONFIDENCE_HIGH
                        ):
                            cur.extraction_confidence = EXTRACTION_CONFIDENCE_MEDIUM

                        if consensus.profile is not None:
                            cur.numeric_profiles.append(consensus.profile)

                        if consensus.detected_amount is None:
                            continue

                        detected_amount = consensus.detected_amount
                        assigned_column = cell.assigned_column
                        direction = consensus.direction or line_dir or adj_amount_dirs.get(id(cell))
                        is_overlaid_consensus = EXTRACTION_CONFIDENCE_OVERLAID in consensus.flags
                        consensus_dir = consensus.direction

                    if assigned_column == "balance":
                        norm = norm_money(cell.text)
                        is_neg = (
                            norm.startswith(("-", "("))
                            or norm.endswith(("DB", "DR", "-"))
                            or consensus_dir == "debit"
                            or id(cell) in neg_balance_ids
                        )
                        printed_bal = -abs(detected_amount) if is_neg else detected_amount
                        if cur.printed_balance is not None:
                            if "multiple_balances" not in cur.validation:
                                cur.validation.append("multiple_balances")
                            if is_overlaid_consensus:
                                cur.printed_balance = printed_bal
                                cur.balance = printed_bal
                        else:
                            cur.printed_balance = printed_bal
                            cur.balance = printed_bal
                        cur.has_printed_balance = cur.printed_balance is not None
                    elif assigned_column in ("debit", "credit"):
                        if detected_amount != 0:
                            existing_val = getattr(cur, assigned_column)
                            if existing_val is not None and existing_val != 0.0:
                                cur.validation.append("multiple_amounts")
                            else:
                                setattr(cur, assigned_column, abs(detected_amount))
                        elif getattr(cur, assigned_column) is None:
                            setattr(cur, assigned_column, 0.0)
                    elif assigned_column == "amount" and detected_amount != 0:
                        if "debit" not in col_by_role and "credit" not in col_by_role:
                            cur.single_amount_col = True
                        if direction == "credit":
                            if cur.credit is not None:
                                cur.validation.append("multiple_amounts")
                            else:
                                cur.credit = abs(detected_amount)
                        elif direction == "debit":
                            if cur.debit is not None:
                                cur.validation.append("multiple_amounts")
                            else:
                                cur.debit = abs(detected_amount)
                        else:
                            if cur.pending_mutasi is not None:
                                cur.validation.append("multiple_amounts")
                            else:
                                cur.pending_mutasi = abs(detected_amount)
                elif cell.money and cell.assigned_column is None and cur.pending_mutasi is None and cur.debit is None and cur.credit is None:
                    if _UNFORMATTED_ECHO_AMT_RE.fullmatch(cell.text.strip()) and any(
                        _FORMATTED_THOUSANDS_RE.search(c.text)
                        and (a := parse_amount(c.text)) is not None
                        and (ca := parse_amount(cell.text)) is not None
                        and abs(a.value - ca.value) <= 0.01
                        for ln in band.lines
                        for c in ln.cells
                        if c.assigned_column in ("debit", "credit", "amount")
                    ):
                        desc_parts.append(cell.text)
                        continue
                    amount = parse_amount(cell.text)
                    if amount and amount.value != 0:
                        if "debit" not in col_by_role and "credit" not in col_by_role:
                            cur.single_amount_col = True
                        direction = amount.direction or line_dir or adj_amount_dirs.get(id(cell))
                        if direction == "credit":
                            cur.credit = abs(amount.value)
                        elif direction == "debit":
                            cur.debit = abs(amount.value)
                        else:
                            cur.pending_mutasi = abs(amount.value)
                        cur.validation.append("amount_column_inferred")
                else:
                    if id(cell) in consumed_marker_ids:
                        continue
                    role = text_role(cell)
                    if role in ("desc", "ref") or (role == "other" and not cell.money):
                        desc_parts.append(cell.text)
                    elif role == "extra":
                        extra_parts.append(cell.text)
                    elif role == "date" and not PURE_TIME_RE.match(cell.text):
                        ref_parts.append(cell.text)

        if not desc_parts and extra_parts:
            desc_parts = extra_parts
        cur.description = clean_text(" ".join(desc_parts)).upper()
        cur.reference = clean_text(" ".join(ref_parts)).upper()
        cur.extra = clean_text(" ".join(extra_parts)).upper()
        cur.raw_text = clean_text(" ".join(raw_parts)).upper()
        cur.clean_description = extract_clean_description(cur.description)
        cur.counterparty_name = ""
        cur.has_printed_balance = cur.printed_balance is not None

        _finalize_band_row(cur, band)

        rows.append(cur)

    kept = [
        r
        for r in rows
        if r.is_opening
        or r.printed_balance is not None
        or r.debit
        or r.credit
        or r.pending_mutasi
        or (
            EXTRACTION_CONFIDENCE_CONFLICT in r.validation
            and (r.date_parts is not None or bool(r.numeric_profiles))
        )
        or EXTRACTION_CONFIDENCE_OVERLAID in r.validation
    ]

    # Filter out redundant daily balance restatements (same balance as previous row with no debit or credit movement)
    filtered_kept: list[ExtractedRow] = []
    curr_bal: float | None = getattr(state, "balance", None)
    for r in kept:
        if (
            not r.is_opening
            and not (r.debit or r.credit or r.pending_mutasi)
            and r.printed_balance is not None
            and curr_bal is not None
            and abs(r.printed_balance - curr_bal) < 0.01
        ):
            continue
        filtered_kept.append(r)
        if r.printed_balance is not None:
            curr_bal = r.printed_balance
    kept = filtered_kept

    # Resolve remaining pending_mutasi via running balance delta when available
    resolved_dirs: list[str] = []
    prev_bal: float | None = getattr(state, "balance", None)
    for r in kept:
        if r.is_opening:
            if r.printed_balance is not None:
                prev_bal = r.printed_balance
            continue
        if r.pending_mutasi is not None and not r.debit and not r.credit and prev_bal is not None and r.printed_balance is not None:
            tol_bal = max(1.0, 1e-4 * max(abs(prev_bal), abs(r.printed_balance)))
            resolved = resolve_pending(r, prev_bal, tol=tol_bal, prefer="debit")
            if resolved:
                resolved_dirs.append(resolved)
        if r.printed_balance is not None:
            prev_bal = r.printed_balance
    setattr(state, "balance", prev_bal)

    # If a 2-column cropped table had a single active mutation column and all balance-verified rows
    # share one direction, apply that column direction to the leading row if its predecessor balance was cropped
    if resolved_dirs and len(set(resolved_dirs)) == 1 and len(numeric) == 2:
        unanimous_dir = resolved_dirs[0]
        for r in kept:
            if not r.is_opening and r.pending_mutasi is not None and not r.debit and not r.credit:
                setattr(r, unanimous_dir, r.pending_mutasi)
                r.pending_mutasi = None
                drop_flag(r, "direction_unknown")

    # For Testing with or without NER
    enable_ner = os.getenv("DISABLE_NER", "0") not in ("1", "true", "True")

    if kept and enable_ner:
        ner_inputs = [
            "" if r.is_opening else " ".join(filter(None, (r.description, r.extra)))
            for r in kept
        ]
        ner_results = get_transaction_ner().extract_batch(ner_inputs)
        for r, ner_res in zip(kept, ner_results):
            if not r.is_opening:
                r.counterparty_name = ner_res.get("counterparty", "")

    page_years = [r.date_parts[2] for r in kept if r.date_parts and len(r.date_parts) >= 3 and r.date_parts[2]]
    effective_year = (
        fingerprint.statement_year
        or state.active_year
        or (Counter(page_years).most_common(1)[0][0] if page_years else None)
    )
    apply_dates(
        kept,
        effective_year,
        statement_month=fingerprint.statement_month or state.active_month,
        period_start=fingerprint.statement_period_start,
        state=state,
    )

    diag.update({
        "dropped_noise_rows": len(rows) - len(kept),
        "rows": len(kept),
        "columns": [n["assigned_column"] for n in numeric],
        "discovered_intervals": [
            {"role": c.assigned_column, "x0": c.x0, "x1": c.x1, "edge_x1": c.edge_x1}
            for c in col_intervals
        ],
        "dominant_font": dominant_font_profile,
        "overlaid_spans": len(overlaid_pairs),
        "numeric_spans": numeric_table_spans,
        "overlaid_pairs": overlaid_pairs,
        "reported_totals": quarantined_totals,
        "is_sealed": page_is_sealed,
    })
    if kept:
        state.has_rows = True
    return kept, diag, page_text
