from __future__ import annotations

import io
import re
import statistics
from dataclasses import asdict
from datetime import date, datetime
from typing import Any, Callable, Sequence

import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from component.core import DETAIL_COLS, MONTH_NAMES_ID, OPENING_RE, REVIEW_COLS
from component.parsing import category, month_key


# ---- Frames & Formatting ----


def safe_str(val: Any) -> str:
    s = str(val or "")
    if s.startswith(("=", "+", "-", "@")):
        return "'" + s
    return s


def format_date(dt: Any) -> str:
    if isinstance(dt, (date, datetime)):
        return dt.strftime("%d/%m/%Y")
    return str(dt) if dt is not None else ""


def transaction_record(row: Any, format_dt: bool = False, escape_excel: bool = True) -> dict[str, Any]:
    desc = safe_str(row.description) if escape_excel else (row.description or "")
    counterparty = safe_str(getattr(row, "counterparty_name", "")) if escape_excel else (getattr(row, "counterparty_name", "") or "")
    ref = safe_str(getattr(row, "reference", "")) if escape_excel else (getattr(row, "reference", "") or "")
    dt_val = format_date(row.date) if format_dt else (row.date if row.date is not None else "")
    printed_bal = getattr(row, "printed_balance", None)
    bal_val = printed_bal if printed_bal is not None else row.balance
    return {
        "Tanggal": dt_val,
        "Kredit": row.credit,
        "Debit": row.debit,
        "Saldo": bal_val,
        "Deskripsi": desc,
        "Pihak Terkait": counterparty,
        "No. Referensi": ref,
        "Kategori Transaksi": category(row.description, row.debit, row.credit),
    }


def rows_df(rows: list[Any], format_dt: bool = True, escape_excel: bool = False) -> pd.DataFrame:
    records = [transaction_record(r, format_dt=format_dt, escape_excel=escape_excel) for r in rows]
    idx = [getattr(r, "transaction_index", i) for i, r in enumerate(rows)]
    df = pd.DataFrame(records, index=idx if rows else None, columns=DETAIL_COLS)
    df.index.name = "transaction_index"
    return df


def review_df(rows: list[Any], detailed: bool = True, escape_excel: bool = False) -> pd.DataFrame:
    records = []
    idx_list = []
    for i, row in enumerate(rows):
        tx_idx = getattr(row, "transaction_index", i)
        idx_list.append(tx_idx)
        raw = safe_str(row.raw_text) if escape_excel else (row.raw_text or "")
        counterparty = safe_str(getattr(row, "counterparty_name", "")) if escape_excel else (getattr(row, "counterparty_name", "") or "")
        ref = safe_str(getattr(row, "reference", "")) if escape_excel else (getattr(row, "reference", "") or "")
        flags = ", ".join(row.validation)
        if detailed:
            records.append({
                "transaction_index": tx_idx,
                "Halaman": row.page,
                "Pihak Terkait": counterparty,
                "No. Referensi": ref,
                "Extraction confidence": getattr(row, "extraction_confidence", "HIGH"),
                "Reconciliation status": getattr(row, "reconciliation_status", "UNVERIFIED"),
                "Confidence": round(getattr(row, "confidence", 1.0), 3),
                "Human review status": "Needs review" if row.validation else "Pending review",
                "Validation flags": flags,
                "Full detected text": raw,
            })
        else:
            records.append({
                "Halaman": row.page,
                "Pihak Terkait": counterparty,
                "No. Referensi": ref,
                "Validation flags": flags,
                "Full detected text": raw,
            })
    cols = None if detailed else REVIEW_COLS
    df = pd.DataFrame(records, index=idx_list if rows else None, columns=cols)
    df.index.name = "transaction_index"
    return df


def serialize_row(row: Any) -> dict[str, Any]:
    item = asdict(row)
    item["date"] = row.date.isoformat() if isinstance(row.date, (date, datetime)) else row.date
    item.pop("date_parts", None)
    item.pop("time", None)
    item.pop("pending_dir", None)
    item.pop("numeric_profiles", None)
    return item


# ---- Monthly Analytics ----


def month_label(ym: tuple[int, int]) -> str:
    year, month = ym
    name = MONTH_NAMES_ID.get(month, f"Bulan {month}")
    return f"{name} {year}" if year else name


def saldo_mengendap(rows: list[Any]) -> dict[tuple[int, int], list[tuple[Any, float]]]:
    def _row_bal(r: Any) -> float | None:
        bal = getattr(r, "printed_balance", None)
        if bal is None:
            bal = getattr(r, "balance", None)
        return float(bal) if bal is not None else None

    def _is_opening(r: Any) -> bool:
        return bool(getattr(r, "is_opening", False)) or bool(
            OPENING_RE.search(str(getattr(r, "description", "") or ""))
        )

    month_groups: dict[tuple[int, int], dict[str, list[Any]]] = {}
    for row in rows:
        dt = getattr(row, "date", None)
        if dt is None:
            continue
        ym = month_key(dt)
        if not ym:
            continue
        bucket = month_groups.setdefault(ym, {"openings": [], "txs": []})
        if _is_opening(row):
            bucket["openings"].append(row)
        else:
            bucket["txs"].append(row)

    by_month: dict[tuple[int, int], list[tuple[Any, float]]] = {}
    prev_month_last_eod: float | None = None

    for ym in sorted(month_groups):
        year, month = ym
        openings = month_groups[ym]["openings"]
        txs = month_groups[ym]["txs"]

        txs_by_day: dict[int, list[Any]] = {}
        for r in txs:
            dt = r.date.date() if isinstance(r.date, datetime) else r.date
            if isinstance(dt, date):
                txs_by_day.setdefault(dt.day, []).append(r)

        ordered_txs: list[Any] = []
        eod_by_day: dict[int, float] = {}
        for d in sorted(txs_by_day):
            day_rows = txs_by_day[d]
            if any(getattr(r, "time", None) is not None for r in day_rows):
                day_rows = [
                    item[1]
                    for item in sorted(
                        enumerate(day_rows),
                        key=lambda item: (getattr(item[1], "time", None) or (-1, -1, -1), item[0]),
                    )
                ]
            for r in day_rows:
                ordered_txs.append(r)
                bal = _row_bal(r)
                if bal is not None:
                    eod_by_day[d] = bal

        # Priority cascade for Day-1 anchor:
        # 1. Explicit month opening row
        # 2. Previous month's final closing balance
        # 3. Derived pre-transaction opening balance from first chronological tx
        explicit_open = next((_row_bal(op) for op in openings if _row_bal(op) is not None), None)
        if explicit_open is not None:
            day1_anchor: float | None = explicit_open
        elif prev_month_last_eod is not None:
            day1_anchor = prev_month_last_eod
        else:
            first_with_bal = next((r for r in ordered_txs if _row_bal(r) is not None), None)
            if first_with_bal is not None:
                first_bal = _row_bal(first_with_bal)
                assert first_bal is not None
                cr = float(getattr(first_with_bal, "credit", None) or 0.0)
                db = float(getattr(first_with_bal, "debit", None) or 0.0)
                day1_anchor = round(first_bal - cr + db, 2) if (cr or db) else first_bal
            else:
                day1_anchor = None

        month_final_eod = eod_by_day[max(eod_by_day)] if eod_by_day else day1_anchor
        if month_final_eod is not None:
            prev_month_last_eod = month_final_eod

        if day1_anchor is None:
            continue

        schedule = [(1, float(day1_anchor))] + [
            (d, eod_by_day[d]) for d in sorted(d for d in eod_by_day if d >= 2)
        ]

        entries: list[tuple[Any, float]] = []
        for k, (d_k, bal_k) in enumerate(schedule):
            multiplier = (schedule[k + 1][0] - d_k) if k < len(schedule) - 1 else 1
            for offset in range(multiplier):
                entries.append((date(year, month, d_k + offset), bal_k))
        by_month[ym] = entries

    return by_month


def top5_monthly(rows: list[Any]) -> dict[tuple[int, int], dict[str, list[Any]]]:
    by_month: dict[tuple[int, int], list[Any]] = {}
    for row in rows:
        if getattr(row, "is_opening", False) or row.date is None:
            continue
        ym = month_key(row.date)
        if ym:
            by_month.setdefault(ym, []).append(row)

    out: dict[tuple[int, int], dict[str, list[Any]]] = {}
    for ym in sorted(by_month):
        month_rows = by_month[ym]
        credits = [r for r in month_rows if r.credit and r.credit > 0]
        debits = [r for r in month_rows if r.debit and r.debit > 0]
        top_cr = sorted(credits, key=lambda r: float(r.credit), reverse=True)[:5]
        top_db = sorted(debits, key=lambda r: float(r.debit), reverse=True)[:5]
        out[ym] = {"cr": top_cr, "db": top_db}
    return out


# ---- Memo Analytics ----


CURRENCY_FMT = "#,##0.00"
PERCENT_FMT = "0.0%"


def _extract_account_groups(result: dict[str, Any]) -> list[tuple[str, str, str, list[Any], dict[str, Any]]]:
    rows = result.get("rows", [])
    accounts_map = result.get("accounts")
    groups: list[tuple[str, str, str, list[Any], dict[str, Any]]] = []

    if accounts_map:
        for key, data in accounts_map.items():
            acc = str(data.get("account") or "").strip()
            if acc in ("Default", "Unknown", "UNKNOWN_ACCOUNT"):
                acc = ""
            bank = str(data.get("bank") or "").strip()
            acc_rows = data.get("rows", [])
            holder = get_account_holder_name(data, default="-")
            groups.append((key, bank or "-", acc or "-", acc_rows, data))
    else:
        acc = str(result.get("summary", {}).get("account") or "").strip()
        if acc in ("Default", "Unknown", "UNKNOWN_ACCOUNT"):
            acc = ""
        bank = str(result.get("summary", {}).get("bank") or "").strip()
        if not bank and rows:
            bank = next((r.bank for r in rows if getattr(r, "bank", "")), "")
        if not acc and rows:
            acc = next(
                (r.account for r in rows if getattr(r, "account", "") and r.account != "UNKNOWN_ACCOUNT"),
                "",
            )
        holder = get_account_holder_name(result, default="-")
        groups.append(("Default", bank or "-", acc or "-", rows, result))

    return groups


def get_account_holder_name(acc_data: dict[str, Any] | Sequence[Any], default: str = "") -> str:
    if isinstance(acc_data, dict):
        for key in ("account_holder", "holder_name", "nama_pemilik", "owner", "customer_name", "name"):
            val = acc_data.get(key)
            if val and str(val).strip():
                return str(val).strip()
        summary = acc_data.get("summary")
        if isinstance(summary, dict):
            for key in ("account_holder", "holder_name", "nama_pemilik", "owner"):
                val = summary.get(key)
                if val and str(val).strip():
                    return str(val).strip()
        pages = acc_data.get("pages") or []
        for p in pages:
            if isinstance(p, dict):
                for key in ("account_holder", "holder_name"):
                    val = p.get(key)
                    if val and str(val).strip():
                        return str(val).strip()
    return default


def calculate_monthly_metrics(rows: Sequence[Any]) -> dict[tuple[int, int], dict[str, Any]]:
    sm_by_month = saldo_mengendap(list(rows))
    by_month: dict[tuple[int, int], list[Any]] = {}

    for row in rows:
        if getattr(row, "is_opening", False) or getattr(row, "date", None) is None:
            continue
        ym = month_key(row.date)
        if ym is not None:
            by_month.setdefault(ym, []).append(row)

    all_months = sorted(set(by_month.keys()) | set(sm_by_month.keys()))
    metrics: dict[tuple[int, int], dict[str, Any]] = {}

    for ym in all_months:
        m_rows = by_month.get(ym, [])
        sm_items = sm_by_month.get(ym, [])

        md = round(sum(float(r.debit or 0.0) for r in m_rows if r.debit and r.debit > 0), 2)
        mk = round(sum(float(r.credit or 0.0) for r in m_rows if r.credit and r.credit > 0), 2)
        debit_count = sum(1 for r in m_rows if r.debit and r.debit > 0)
        credit_count = sum(1 for r in m_rows if r.credit and r.credit > 0)

        if sm_items:
            daily_bals = [float(bal) for _, bal in sm_items]
            pengendapan = round(statistics.mean(daily_bals), 2)
            sa = round(float(sm_items[-1][1]), 2)
        else:
            bals = [
                float(getattr(r, "printed_balance", None) if getattr(r, "printed_balance", None) is not None else r.balance)
                for r in m_rows
                if getattr(r, "printed_balance", None) is not None or getattr(r, "balance", None) is not None
            ]
            pengendapan = round(statistics.mean(bals), 2) if bals else 0.0
            sa = round(bals[-1], 2) if bals else 0.0

        metrics[ym] = {
            "label": month_label(ym),
            "md": md,
            "mk": mk,
            "selisih": round(mk - md, 2),
            "pengendapan": pengendapan,
            "sa": sa,
            "tx_count": len(m_rows),
            "debit_count": debit_count,
            "credit_count": credit_count,
        }

    return metrics


def _accumulate_counterparty_monthly(
    rows: Sequence[Any],
    amount_fn: Callable[[Any], float],
    include_zero_amount_months: bool = False,
) -> tuple[dict[str, dict[tuple[int, int], float]], dict[str, int], set[tuple[int, int]]]:
    """Accumulate counterparty transaction volumes and occurrence counts across calendar months."""
    months_set: set[tuple[int, int]] = set()
    cp_monthly: dict[str, dict[tuple[int, int], float]] = {}
    cp_counts: dict[str, int] = {}

    for row in rows:
        if getattr(row, "is_opening", False) or getattr(row, "date", None) is None:
            continue
        ym = month_key(row.date)
        if ym is None:
            continue
        if include_zero_amount_months:
            months_set.add(ym)
        val = amount_fn(row)
        if val <= 0.0:
            continue
        if not include_zero_amount_months:
            months_set.add(ym)

        raw_cp = str(getattr(row, "counterparty_name", "") or "").strip()
        cp_name = raw_cp if raw_cp else "Lain - lain"
        cp_monthly.setdefault(cp_name, {})
        cp_monthly[cp_name][ym] = round(cp_monthly[cp_name].get(ym, 0.0) + val, 2)
        cp_counts[cp_name] = cp_counts.get(cp_name, 0) + 1

    return cp_monthly, cp_counts, months_set


def calculate_counterparty_breakdown(rows: Sequence[Any]) -> dict[str, Any]:
    def _both_amount(r: Any) -> float:
        d = float(r.debit or 0.0) if (r.debit and r.debit > 0) else 0.0
        c = float(r.credit or 0.0) if (r.credit and r.credit > 0) else 0.0
        return c + d

    cp_monthly, cp_counts, months_set = _accumulate_counterparty_monthly(
        rows, _both_amount, include_zero_amount_months=False
    )
    sorted_months = sorted(months_set)
    named_cps = sorted(
        (cp for cp in cp_monthly if cp != "Lain - lain"),
        key=lambda cp: (-sum(cp_monthly[cp].values()), cp),
    )
    ordered_cps = named_cps + (["Lain - lain"] if "Lain - lain" in cp_monthly else [])
    grand_total = round(
        sum(sum(m_map.values()) for m_map in cp_monthly.values()),
        2,
    )

    items: list[dict[str, Any]] = []
    for cp_name in ordered_cps:
        m_map = cp_monthly[cp_name]
        tot = round(sum(m_map.get(ym, 0.0) for ym in sorted_months), 2)
        share = round((tot / grand_total) * 100.0, 2) if grand_total > 0 else 0.0
        items.append({
            "counterparty": cp_name,
            "category": cp_name,
            "monthly": {ym: round(m_map.get(ym, 0.0), 2) for ym in sorted_months},
            "total": tot,
            "share_pct": share,
            "count": cp_counts.get(cp_name, 0),
        })

    return {
        "months": sorted_months,
        "categories": items,
        "items": items,
        "grand_total": grand_total,
    }


calculate_category_breakdown = calculate_counterparty_breakdown


def calculate_account_counterparty_recap(
    rows: Sequence[Any],
    is_credit: bool = True,
    top_n: int | None = None,
) -> dict[str, Any]:
    cp_monthly, cp_counts, all_months_set = _accumulate_counterparty_monthly(
        rows,
        lambda r: float((r.credit if is_credit else r.debit) or 0.0),
        include_zero_amount_months=True,
    )
    sorted_months = sorted(all_months_set)
    named_totals = [
        (cp, round(sum(m_map.values()), 2))
        for cp, m_map in cp_monthly.items()
        if cp != "Lain - lain"
    ]
    named_totals.sort(key=lambda item: (-item[1], item[0]))

    if top_n is not None and top_n > 0:
        top_names = [cp for cp, _ in named_totals[:top_n]]
        remainder_names = [cp for cp, _ in named_totals[top_n:]]
    else:
        top_names = [cp for cp, _ in named_totals]
        remainder_names = []

    if "Lain - lain" in cp_monthly:
        remainder_names.append("Lain - lain")

    items: list[dict[str, Any]] = []
    for cp in top_names:
        m_map = cp_monthly[cp]
        tot = round(sum(m_map.get(ym, 0.0) for ym in sorted_months), 2)
        items.append({
            "counterparty": cp,
            "monthly": {ym: round(m_map.get(ym, 0.0), 2) for ym in sorted_months},
            "total": tot,
            "count": cp_counts.get(cp, 0),
        })

    if remainder_names:
        rem_monthly: dict[tuple[int, int], float] = {
            ym: round(sum(cp_monthly[c].get(ym, 0.0) for c in remainder_names), 2)
            for ym in sorted_months
        }
        rem_tot = round(sum(rem_monthly.values()), 2)
        rem_cnt = sum(cp_counts.get(c, 0) for c in remainder_names)
        if rem_tot > 0:
            items.append({
                "counterparty": "Lain - lain",
                "monthly": rem_monthly,
                "total": rem_tot,
                "count": rem_cnt,
            })

    grand_total = round(sum(item["total"] for item in items), 2)
    return {
        "months": sorted_months,
        "items": items,
        "grand_total": grand_total,
    }


def calculate_top_counterparties(
    rows: Sequence[Any],
    is_credit: bool = True,
    top_n: int = 8,
) -> dict[str, Any]:
    return calculate_account_counterparty_recap(rows, is_credit=is_credit, top_n=top_n)


def compute_portfolio_averages(
    m_metrics: dict[tuple[int, int], dict[str, Any]],
) -> tuple[float, float, float, float]:
    """Computes (avg_md, avg_mk, avg_peng, last_sa) for account monthly metrics."""
    if m_metrics:
        vals = list(m_metrics.values())
        avg_md = round(statistics.mean(v["md"] for v in vals), 2)
        avg_mk = round(statistics.mean(v["mk"] for v in vals), 2)
        avg_peng = round(statistics.mean(v["pengendapan"] for v in vals), 2)
        last_sa = round(vals[-1]["sa"], 2)
        return avg_md, avg_mk, avg_peng, last_sa
    return 0.0, 0.0, 0.0, 0.0


def build_portfolio_overview_df(result: dict[str, Any]) -> pd.DataFrame:
    groups = _extract_account_groups(result)
    records: list[dict[str, Any]] = []

    for idx, (_, bank, acc, acc_rows, acc_data) in enumerate(groups, start=1):
        holder = get_account_holder_name(acc_data, default="-")
        m_metrics = calculate_monthly_metrics(acc_rows)
        avg_md, avg_mk, avg_peng, last_sa = compute_portfolio_averages(m_metrics)

        records.append({
            "No": idx,
            "Bank": bank,
            "No Rekening": acc,
            "Nama Pemilik": holder,
            "Rata-Rata MD": avg_md,
            "Rata-Rata MK": avg_mk,
            "Selisih (MK - MD)": round(avg_mk - avg_md, 2),
            "Rata-rata Pengendapan": avg_peng,
            "Saldo Akhir Terakhir": last_sa,
        })

    cols = [
        "No",
        "Bank",
        "No Rekening",
        "Nama Pemilik",
        "Rata-Rata MD",
        "Rata-Rata MK",
        "Selisih (MK - MD)",
        "Rata-rata Pengendapan",
        "Saldo Akhir Terakhir",
    ]
    return pd.DataFrame(records, columns=cols)


# ---- Memo Sheet ----


def _write_account_recap_block(
    ws: Any,
    cur_row: int,
    bank: str,
    acc: str,
    holder: str,
    acc_rows: Sequence[Any],
    is_credit: bool,
    styles: dict[str, Any],
    start_col: int = 1,
    precomputed_recap: dict[str, Any] | None = None,
) -> int:
    recap = precomputed_recap or calculate_account_counterparty_recap(acc_rows, is_credit=is_credit, top_n=None)
    months = recap["months"]
    month_headers = [month_label(ym) for ym in months] if months else ["Periode"]
    n_months = max(1, len(months))
    s = start_col
    first_m_idx = s + 1
    last_m_idx = s + n_months
    tot_col_idx = s + 1 + n_months
    ket_col_idx = s + 2 + n_months

    bold_font = styles["bold_font"]
    reg_font = styles["reg_font"]
    gray_fill = styles["gray_fill"]
    yellow_fill = styles["yellow_fill"]
    box_border = styles["box_border"]
    align_center = styles["align_center"]
    align_left = styles["align_left"]
    align_right = styles["align_right"]

    bank_acc_title = f"{bank}, {acc}" if (bank != "-" and acc != "-") else (bank if bank != "-" else acc)
    ws.cell(cur_row, s, safe_str(bank_acc_title))
    ws.merge_cells(start_row=cur_row, start_column=s, end_row=cur_row, end_column=ket_col_idx)
    for col_i in range(s, ket_col_idx + 1):
        c = ws.cell(cur_row, col_i)
        c.font = bold_font
        c.alignment = align_center
        c.border = box_border
    cur_row += 1

    ws.cell(cur_row, s, safe_str(holder))
    ws.merge_cells(start_row=cur_row, start_column=s, end_row=cur_row, end_column=ket_col_idx)
    for col_i in range(s, ket_col_idx + 1):
        c = ws.cell(cur_row, col_i)
        c.font = bold_font
        c.alignment = align_center
        c.border = box_border
    cur_row += 1

    headers = [""] + month_headers + ["Total Result", "Keterangan"]
    for offset, h_text in enumerate(headers):
        c = ws.cell(cur_row, s + offset, h_text)
        c.font = bold_font
        c.fill = gray_fill
        c.alignment = align_center
        c.border = box_border
    cur_row += 1

    start_row = cur_row
    first_m_let = get_column_letter(first_m_idx)
    last_m_let = get_column_letter(last_m_idx)

    items = recap["items"] or [{"counterparty": "Lain - lain", "monthly": {}, "total": 0.0}]
    for item in items:
        c_cp = ws.cell(cur_row, s, safe_str(item["counterparty"]))
        c_cp.font = reg_font
        c_cp.alignment = align_left
        c_cp.border = box_border

        for m_i, ym in enumerate(months or [None], start=first_m_idx):
            val = item["monthly"].get(ym, 0.0) if ym is not None else 0.0
            cm = ws.cell(cur_row, m_i, val if val > 0 else None)
            cm.number_format = CURRENCY_FMT
            cm.alignment = align_right
            cm.font = reg_font
            cm.border = box_border

        ct = ws.cell(cur_row, tot_col_idx, f"=SUM({first_m_let}{cur_row}:{last_m_let}{cur_row})")
        ct.number_format = CURRENCY_FMT
        ct.alignment = align_right
        ct.font = reg_font
        ct.border = box_border

        ck = ws.cell(cur_row, ket_col_idx, "")
        ck.font = reg_font
        ck.alignment = align_left
        ck.border = box_border
        cur_row += 1

    end_row = cur_row - 1
    tot_row = cur_row
    c_tot_lbl = ws.cell(tot_row, s, "Total Result")
    c_tot_lbl.font = bold_font
    c_tot_lbl.alignment = align_left
    c_tot_lbl.border = box_border

    for col_i in range(first_m_idx, tot_col_idx + 1):
        col_let = get_column_letter(col_i)
        cs = ws.cell(tot_row, col_i, f"=SUM({col_let}{start_row}:{col_let}{end_row})")
        cs.font = bold_font
        cs.number_format = CURRENCY_FMT
        cs.alignment = align_right
        cs.border = box_border

    ck_tot = ws.cell(tot_row, ket_col_idx, "")
    ck_tot.border = box_border
    cur_row += 1

    avg_row = cur_row
    ws.cell(avg_row, s, "")
    ws.cell(avg_row, first_m_idx, f"=AVERAGE({first_m_let}{tot_row}:{last_m_let}{tot_row})")
    ws.merge_cells(start_row=avg_row, start_column=first_m_idx, end_row=avg_row, end_column=tot_col_idx)
    ws.cell(avg_row, ket_col_idx, "")

    for col_i in range(s, ket_col_idx + 1):
        c = ws.cell(avg_row, col_i)
        c.font = bold_font
        c.fill = yellow_fill
        c.border = box_border
        if col_i == first_m_idx:
            c.number_format = CURRENCY_FMT
            c.alignment = align_center

    return avg_row + 2


def create_memo_sheet(wb: Any, result: dict[str, Any], title: str = "00_RINGKASAN") -> Any:
    if title in wb.sheetnames:
        del wb[title]
    ws = wb.create_sheet(title=title[:31], index=0)

    title_font = Font(name="Calibri", size=13, bold=True, color="1F4E78")
    section_font = Font(name="Calibri", size=11, bold=True, color="000000", underline="single")
    sub_font = Font(name="Calibri", size=10, bold=True, color="2F5597")
    hdr_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    bold_font = Font(name="Calibri", size=10, bold=True)
    reg_font = Font(name="Calibri", size=10)

    hdr_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    sub_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    total_fill = PatternFill(start_color="F2F4F8", end_color="F2F4F8", fill_type="solid")
    gray_fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
    yellow_fill = PatternFill(start_color="FFFF99", end_color="FFFF99", fill_type="solid")

    thin_side = Side(style="thin", color="BFBFBF")
    black_thin = Side(style="thin", color="000000")
    double_bottom = Side(style="double", color="000000")

    grid_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
    box_border = Border(left=black_thin, right=black_thin, top=black_thin, bottom=black_thin)
    accounting_total_border = Border(
        top=black_thin,
        bottom=double_bottom,
        left=thin_side,
        right=thin_side,
    )

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    recap_styles = {
        "bold_font": bold_font,
        "reg_font": reg_font,
        "gray_fill": gray_fill,
        "yellow_fill": yellow_fill,
        "box_border": box_border,
        "align_center": align_center,
        "align_left": align_left,
        "align_right": align_right,
    }

    groups = _extract_account_groups(result)

    ws.cell(1, 1, "RINGKASAN EKSEKUTIF REKENING KORAN (CREDIT MEMO SUMMARY)").font = title_font
    ws.cell(2, 1, "1. SUMMARY REKENING (PORTFOLIO MULTI-ACCOUNT OVERVIEW)").font = section_font

    s1_headers = [
        "No",
        "Bank",
        "No Rekening",
        "Nama Pemilik",
        "Rata-Rata MD",
        "Rata-Rata MK",
        "Selisih (MK - MD)",
        "Rata-rata Pengendapan",
        "Saldo Akhir Terakhir",
    ]
    s1_hdr_row = 3
    for col_idx, h_text in enumerate(s1_headers, start=1):
        c = ws.cell(s1_hdr_row, col_idx, h_text)
        c.font = hdr_font
        c.fill = hdr_fill
        c.alignment = align_center if col_idx == 1 else (align_left if col_idx <= 4 else align_right)
        c.border = grid_border

    s1_start_row = 4
    s1_row_map: dict[int, int] = {}
    for idx, (_, bank, acc, acc_rows, acc_data) in enumerate(groups, start=1):
        r_idx = s1_start_row + idx - 1
        s1_row_map[idx] = r_idx
        holder = get_account_holder_name(acc_data, default="-")
        m_metrics = calculate_monthly_metrics(acc_rows)
        avg_md, avg_mk, avg_peng, last_sa = compute_portfolio_averages(m_metrics)

        c_no = ws.cell(r_idx, 1, idx)
        c_no.alignment = align_center
        c_bank = ws.cell(r_idx, 2, safe_str(bank))
        c_bank.alignment = align_left
        c_acc = ws.cell(r_idx, 3, safe_str(acc))
        c_acc.alignment = align_left
        c_holder = ws.cell(r_idx, 4, safe_str(holder))
        c_holder.alignment = align_left

        c_md = ws.cell(r_idx, 5, avg_md)
        c_mk = ws.cell(r_idx, 6, avg_mk)
        c_diff = ws.cell(r_idx, 7, f"=F{r_idx}-E{r_idx}")
        c_peng = ws.cell(r_idx, 8, avg_peng)
        c_sa = ws.cell(r_idx, 9, last_sa)

        for num_c in (c_md, c_mk, c_diff, c_peng, c_sa):
            num_c.number_format = CURRENCY_FMT
            num_c.alignment = align_right

        for col_i in range(1, 10):
            cell_obj = ws.cell(r_idx, col_i)
            cell_obj.font = reg_font
            cell_obj.border = grid_border

    s1_end_row = max(s1_start_row, s1_start_row + len(groups) - 1)
    if not groups:
        for col_i in range(1, 10):
            c = ws.cell(s1_start_row, col_i, 0.0 if col_i >= 5 else "-")
            c.font = reg_font
            c.border = grid_border
            if col_i >= 5:
                c.number_format = CURRENCY_FMT
                c.alignment = align_right

    s1_total_row = s1_end_row + 1
    ws.cell(s1_total_row, 2, "Total")
    ws.cell(s1_total_row, 2).alignment = align_left
    for col_i in range(1, 5):
        ws.cell(s1_total_row, col_i).fill = total_fill
        ws.cell(s1_total_row, col_i).border = accounting_total_border
        ws.cell(s1_total_row, col_i).font = bold_font

    for col_i in range(5, 10):
        col_let = get_column_letter(col_i)
        c_tot = ws.cell(s1_total_row, col_i, f"=SUM({col_let}{s1_start_row}:{col_let}{s1_end_row})")
        c_tot.font = bold_font
        c_tot.fill = total_fill
        c_tot.border = accounting_total_border
        c_tot.number_format = CURRENCY_FMT
        c_tot.alignment = align_right

    cur_row = s1_total_row + 2

    ws.cell(cur_row, 1, "2. REKENING BANK (PER-ACCOUNT MONTHLY BREAKDOWN MATRIX)").font = section_font
    cur_row += 1

    for idx, (_, bank, acc, acc_rows, acc_data) in enumerate(groups, start=1):
        holder = get_account_holder_name(acc_data, default="-")
        m_metrics = calculate_monthly_metrics(acc_rows)
        months = list(m_metrics.keys())

        sub_title = f"Bank : {bank} | No Rek : {acc} | Nama : {holder}"
        c_sub = ws.cell(cur_row, 1, sub_title)
        c_sub.font = sub_font
        c_sub.fill = sub_fill
        cur_row += 1

        month_headers = [m_metrics[ym]["label"] for ym in months] if months else ["Periode"]
        matrix_headers = ["Keterangan"] + month_headers + ["Total", "Rata-rata", "MK - MD"]
        for col_i, h_text in enumerate(matrix_headers, start=1):
            c = ws.cell(cur_row, col_i, h_text)
            c.font = hdr_font
            c.fill = hdr_fill
            c.alignment = align_left if col_i == 1 else align_right
            c.border = grid_border
        cur_row += 1

        n_months = max(1, len(months))
        first_m_col = get_column_letter(2)
        last_m_col = get_column_letter(1 + n_months)
        tot_col_idx = 2 + n_months
        avg_col_idx = 3 + n_months
        diff_col_idx = 4 + n_months
        avg_col_let = get_column_letter(avg_col_idx)

        md_row = cur_row
        mk_row = cur_row + 1
        peng_row = cur_row + 2
        sa_row = cur_row + 3

        ws.cell(md_row, 1, "MD").font = bold_font
        ws.cell(mk_row, 1, "MK").font = bold_font
        ws.cell(peng_row, 1, "Pengendapan").font = bold_font
        ws.cell(sa_row, 1, "Saldo Akhir (SA)").font = bold_font

        for offset_i, ym in enumerate(months or [None], start=2):
            m_data = m_metrics.get(ym, {"md": 0.0, "mk": 0.0, "pengendapan": 0.0, "sa": 0.0})
            c1 = ws.cell(md_row, offset_i, m_data["md"])
            c2 = ws.cell(mk_row, offset_i, m_data["mk"])
            c3 = ws.cell(peng_row, offset_i, m_data["pengendapan"])
            c4 = ws.cell(sa_row, offset_i, m_data["sa"])
            for c_cell in (c1, c2, c3, c4):
                c_cell.number_format = CURRENCY_FMT
                c_cell.alignment = align_right
                c_cell.font = reg_font

        for r_i, is_sa in ((md_row, False), (mk_row, False), (peng_row, False), (sa_row, True)):
            if is_sa:
                c_t = ws.cell(r_i, tot_col_idx, "-")
                c_t.alignment = align_center
                c_t.font = bold_font
            else:
                c_t = ws.cell(r_i, tot_col_idx, f"=SUM({first_m_col}{r_i}:{last_m_col}{r_i})")
                c_t.number_format = CURRENCY_FMT
                c_t.alignment = align_right
                c_t.font = bold_font

            c_a = ws.cell(r_i, avg_col_idx, f"=AVERAGE({first_m_col}{r_i}:{last_m_col}{r_i})")
            c_a.number_format = CURRENCY_FMT
            c_a.alignment = align_right
            c_a.font = bold_font

        c_diff_md = ws.cell(md_row, diff_col_idx, f"={avg_col_let}{mk_row}-{avg_col_let}{md_row}")
        c_diff_md.number_format = CURRENCY_FMT
        c_diff_md.alignment = align_right
        c_diff_md.font = bold_font

        c_diff_mk = ws.cell(mk_row, diff_col_idx, "")
        c_diff_mk.alignment = align_right

        last_m_label = month_headers[-1]
        c_diff_peng = ws.cell(peng_row, diff_col_idx, f"SA {last_m_label}")
        c_diff_peng.alignment = align_right
        c_diff_peng.font = bold_font

        c_diff_sa = ws.cell(sa_row, diff_col_idx, f"={last_m_col}{sa_row}")
        c_diff_sa.number_format = CURRENCY_FMT
        c_diff_sa.alignment = align_right
        c_diff_sa.font = bold_font

        for r_i in (md_row, mk_row, peng_row, sa_row):
            ws.cell(r_i, 1).alignment = align_left
            for col_i in range(1, diff_col_idx + 1):
                ws.cell(r_i, col_i).border = grid_border

        s1_r = s1_row_map.get(idx)
        if s1_r is not None:
            ws.cell(s1_r, 5, f"={avg_col_let}{md_row}")
            ws.cell(s1_r, 6, f"={avg_col_let}{mk_row}")
            ws.cell(s1_r, 8, f"={avg_col_let}{peng_row}")
            ws.cell(s1_r, 9, f"={last_m_col}{sa_row}")

        cur_row = sa_row + 2

    col_widths: dict[int, int] = {
        1: 36,
        2: 18,
        3: 20,
        4: 26,
        5: 20,
        6: 20,
        7: 20,
        8: 22,
        9: 22,
    }

    recap_pairs = [
        (
            bank,
            acc,
            get_account_holder_name(acc_data, default="-"),
            acc_rows,
            calculate_account_counterparty_recap(acc_rows, is_credit=False, top_n=None),
            calculate_account_counterparty_recap(acc_rows, is_credit=True, top_n=None),
        )
        for _, bank, acc, acc_rows, acc_data in groups
    ]
    max_md_cols = max((3 + max(1, len(md_r["months"])) for _, _, _, _, md_r, _ in recap_pairs), default=4)
    mk_start_col = 2 + max_md_cols

    ws.cell(cur_row, 1, "3. Rekapan Transaksi MD").font = section_font
    ws.cell(cur_row, mk_start_col, "4. Rekapan Transaksi MK").font = section_font
    cur_row += 1

    for bank, acc, holder, acc_rows, md_recap, mk_recap in recap_pairs:
        next_md_row = _write_account_recap_block(
            ws=ws,
            cur_row=cur_row,
            bank=bank,
            acc=acc,
            holder=holder,
            acc_rows=acc_rows,
            is_credit=False,
            styles=recap_styles,
            start_col=1,
            precomputed_recap=md_recap,
        )
        next_mk_row = _write_account_recap_block(
            ws=ws,
            cur_row=cur_row,
            bank=bank,
            acc=acc,
            holder=holder,
            acc_rows=acc_rows,
            is_credit=True,
            styles=recap_styles,
            start_col=mk_start_col,
            precomputed_recap=mk_recap,
        )
        cur_row = max(next_md_row, next_mk_row)

        mk_n_m = max(1, len(mk_recap["months"]))
        col_widths[mk_start_col] = max(col_widths.get(mk_start_col, 18), 36)
        for offset in range(1, mk_n_m + 1):
            c_idx = mk_start_col + offset
            col_widths[c_idx] = max(col_widths.get(c_idx, 18), 18)
        col_widths[mk_start_col + mk_n_m + 1] = max(col_widths.get(mk_start_col + mk_n_m + 1, 18), 20)
        col_widths[mk_start_col + mk_n_m + 2] = max(col_widths.get(mk_start_col + mk_n_m + 2, 18), 22)

    spacer_col = mk_start_col - 1
    if spacer_col > 9:
        col_widths.setdefault(spacer_col, 4)

    for col_idx in range(1, ws.max_column + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = col_widths.get(col_idx, 18)

    ws.freeze_panes = "A4"
    return ws


# ---- Workbook ----


def sheet_title(prefix: str, bank: str, acc: str, used: set[str]) -> str:
    clean_bank = re.sub(r"[\[\]:*?/\\]", "", (bank or "").strip())
    clean_acc = re.sub(r"[\[\]:*?/\\]", "", (acc or "").strip())
    if clean_acc in ("Default", "Unknown", "UNKNOWN_ACCOUNT"):
        clean_acc = ""

    if clean_bank and clean_acc:
        spaced = f"{clean_bank} - {clean_acc}"
        compact = f"{clean_bank}-{clean_acc}"
    else:
        spaced = compact = clean_bank or clean_acc

    if not spaced:
        name = prefix
    else:
        name = f"{prefix} {spaced}"
        if len(name) > 31:
            name = f"{prefix} {compact}"
        if len(name) > 31:
            short_prefix = "Saldo" if prefix.lower().startswith("saldo") else prefix[: max(2, 30 - len(compact))].rstrip()
            name = f"{short_prefix} {compact}"

    name = name[:31]
    base = name
    idx = 2
    while name in used:
        tag = f"_{idx}"
        name = f"{base[: 31 - len(tag)]}{tag}"
        idx += 1
    used.add(name)
    return name


def write_date_cell(ws: Any, row_idx: int, col_idx: int, dt: Any) -> None:
    cell = ws.cell(row_idx, col_idx, dt if dt is not None else "")
    if isinstance(dt, (date, datetime)):
        cell.number_format = "dd/mm/yyyy"


def write_money_cell(ws: Any, row_idx: int, col_idx: int, val: float | str | None) -> Any:
    cell = ws.cell(row_idx, col_idx, val)
    cell.number_format = "#,##0.00"
    return cell


def collapse_saldo_streaks(items: Sequence[tuple[Any, float]]) -> list[tuple[Any, float, int]]:
    streaks: list[tuple[Any, float, int]] = []
    for dt, bal in items:
        bal_val = float(bal)
        if streaks and streaks[-1][1] == bal_val:
            start_dt, prev_bal, count = streaks[-1]
            streaks[-1] = (start_dt, prev_bal, count + 1)
        else:
            streaks.append((dt, bal_val, 1))
    return streaks


def saldo_mengendap_sheet(ws: Any, rows: list[Any], bold_font: Font) -> None:
    monthly = saldo_mengendap(rows)
    if not monthly:
        ws.cell(1, 1, "Tanggal").font = bold_font
        ws.cell(1, 2, "Saldo Mengendap").font = bold_font
        ws.cell(1, 3, "Count").font = bold_font
        ws.cell(1, 4, "Total").font = bold_font
        ws.column_dimensions["A"].width = 14
        ws.column_dimensions["B"].width = 18
        ws.column_dimensions["C"].width = 10
        ws.column_dimensions["D"].width = 20
        return

    for idx, (ym, items) in enumerate(monthly.items()):
        col_date = 1 + idx * 5
        col_bal = col_date + 1
        col_count = col_date + 2
        col_total = col_date + 3

        bal_let = get_column_letter(col_bal)
        count_let = get_column_letter(col_count)
        total_let = get_column_letter(col_total)

        ws.cell(1, col_date, month_label(ym)).font = bold_font
        ws.merge_cells(start_row=1, start_column=col_date, end_row=1, end_column=col_total)
        ws.cell(2, col_date, "Tanggal").font = bold_font
        ws.cell(2, col_bal, "Saldo Mengendap").font = bold_font
        ws.cell(2, col_count, "Count").font = bold_font
        ws.cell(2, col_total, "Total").font = bold_font

        streaks = collapse_saldo_streaks(items)
        start_row = 3
        for offset, (dt, bal, count) in enumerate(streaks):
            row_idx = start_row + offset
            write_date_cell(ws, row_idx, col_date, dt)
            write_money_cell(ws, row_idx, col_bal, bal)
            c_cnt = ws.cell(row_idx, col_count, count)
            c_cnt.number_format = "#,##0"
            write_money_cell(ws, row_idx, col_total, f"={bal_let}{row_idx}*{count_let}{row_idx}")

        if streaks:
            end_row = start_row + len(streaks) - 1
            sum_row = end_row + 1
            ws.cell(sum_row, col_date, "Total / Rata-rata").font = bold_font
            c_avg = write_money_cell(ws, sum_row, col_bal, f"={total_let}{sum_row}/{count_let}{sum_row}")
            c_avg.font = bold_font
            c_sum_cnt = ws.cell(sum_row, col_count, f"=SUM({count_let}{start_row}:{count_let}{end_row})")
            c_sum_cnt.font = bold_font
            c_sum_cnt.number_format = "#,##0"
            c_sum_tot = write_money_cell(ws, sum_row, col_total, f"=SUM({total_let}{start_row}:{total_let}{end_row})")
            c_sum_tot.font = bold_font

        ws.column_dimensions[get_column_letter(col_date)].width = 16
        ws.column_dimensions[bal_let].width = 18
        ws.column_dimensions[count_let].width = 10
        ws.column_dimensions[total_let].width = 20
        ws.column_dimensions[get_column_letter(col_total + 1)].width = 4
    ws.freeze_panes = "A3"


def write_top5_block(
    ws: Any,
    start_row: int,
    col_start: int,
    title: str,
    amt_header: str,
    rows: list[Any],
    field: str,
    bold_font: Font,
) -> None:
    ws.cell(start_row, col_start, title).font = bold_font
    ws.merge_cells(start_row=start_row, start_column=col_start, end_row=start_row, end_column=col_start + 2)
    for offset, header in enumerate(("Tanggal", amt_header, "Deskripsi")):
        ws.cell(start_row + 1, col_start + offset, header).font = bold_font

    for idx, row in enumerate(rows, start=start_row + 2):
        write_date_cell(ws, idx, col_start, row.date)
        write_money_cell(ws, idx, col_start + 1, getattr(row, field))
        ws.cell(idx, col_start + 2, safe_str(row.description))


def top5_sheet(ws: Any, rows: list[Any], bold_font: Font) -> None:
    monthly = top5_monthly(rows)
    if not monthly:
        ws.cell(1, 1, "Tanggal").font = bold_font
        ws.cell(1, 2, "Nominal").font = bold_font
        ws.cell(1, 3, "Deskripsi").font = bold_font
        return

    for idx, (ym, data) in enumerate(monthly.items()):
        col_start = 1 + idx * 4
        label = month_label(ym)
        write_top5_block(ws, 1, col_start, f"{label} - Top 5 CR", "Kredit", data["cr"], "credit", bold_font)
        write_top5_block(ws, 9, col_start, f"{label} - Top 5 DB", "Debit", data["db"], "debit", bold_font)

        ws.column_dimensions[get_column_letter(col_start)].width = 14
        ws.column_dimensions[get_column_letter(col_start + 1)].width = 18
        ws.column_dimensions[get_column_letter(col_start + 2)].width = 50
        ws.column_dimensions[get_column_letter(col_start + 3)].width = 4
    ws.freeze_panes = "A3"


def excel_bytes(result: dict[str, Any]) -> bytes:
    output = io.BytesIO()
    rows = result.get("rows", [])
    accounts_map = result.get("accounts")

    if accounts_map:
        groups = [
            (data.get("account") or "", data.get("bank") or "", data["rows"])
            for data in accounts_map.values()
        ]
    else:
        groups = [("", "", rows)]

    used_names: set[str] = set()
    bold_font = Font(bold=True)
    sheet_specs = (
        ("Detail", lambda _: True),
        ("In", lambda r: bool(r.credit and r.credit > 0)),
        ("Out", lambda r: bool(r.debit and r.debit > 0)),
    )

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        create_memo_sheet(writer.book, result, title="00_RINGKASAN")
        for acc, bank, acc_rows in groups:
            for prefix, pred in sheet_specs:
                sname = sheet_title(prefix, bank, acc, used_names)
                filtered = [r for r in acc_rows if pred(r)]
                rows_df(filtered, format_dt=False, escape_excel=True).to_excel(writer, sheet_name=sname, index=False)

                ws = writer.sheets[sname]
                ws.freeze_panes = "A2"
                for col, width in zip(["A", "B", "C", "D", "E", "F", "G", "H"], [14, 16, 16, 16, 80, 28, 22, 22]):
                    ws.column_dimensions[col].width = width
                for excel_row in ws.iter_rows(min_row=2):
                    if isinstance(excel_row[0].value, (date, datetime)):
                        excel_row[0].number_format = "dd/mm/yyyy"
                    for cell in excel_row[1:4]:
                        cell.number_format = "#,##0.00"

            sm_name = sheet_title("Saldo Mengendap", bank, acc, used_names)
            saldo_mengendap_sheet(writer.book.create_sheet(title=sm_name), acc_rows, bold_font)

            t5_name = sheet_title("Top 5", bank, acc, used_names)
            top5_sheet(writer.book.create_sheet(title=t5_name), acc_rows, bold_font)

            rev_name = sheet_title("Review", bank, acc, used_names)
            review_df(acc_rows, detailed=False, escape_excel=True).to_excel(writer, sheet_name=rev_name, index=False)
            rev_ws = writer.sheets[rev_name]
            rev_ws.freeze_panes = "A2"
            for col, width in zip(["A", "B", "C", "D", "E"], [10, 28, 22, 28, 100]):
                rev_ws.column_dimensions[col].width = width

    return output.getvalue()
