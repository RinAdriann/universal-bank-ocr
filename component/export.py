import io
import re
from datetime import date, datetime
from typing import Any
import pandas as pd
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from .constants import MONTH_NAMES_ID
from .transactioncat import category

DETAIL_COLS = ["Tanggal", "Kredit", "Debit", "Saldo", "Deskripsi", "Kategori Transaksi"]
REVIEW_COLS = ["Halaman", "Validation flags", "Full detected text"]

def _safe_str(val: Any) -> str:
    s = "" if val is None else str(val)
    return f"'{s}" if s.startswith(("=", "+", "-", "@")) else s

def _transaction_record(row: Any) -> dict[str, Any]:
    return {
        "Tanggal": row.date if row.date is not None else "",
        "Kredit": row.credit,
        "Debit": row.debit,
        "Saldo": row.balance,
        "Deskripsi": _safe_str(row.description),
        "Kategori Transaksi": category(row.description, row.debit, row.credit),
    }

def _review_record(row: Any) -> dict[str, Any]:
    return {"Halaman": row.page, "Validation flags": ", ".join(row.validation), "Full detected text": _safe_str(row.raw_text)}

def _month_key(dt: Any) -> tuple[int, int] | None:
    if isinstance(dt, (date, datetime)):
        return (dt.year, dt.month)
    if isinstance(dt, str) and (m := re.match(r"^(\d{2})/(\d{2})(?:/(\d{4}))?$", dt)):
        return (int(m.group(3) or 0), int(m.group(2)))
    return None

def _month_label(ym: tuple[int, int]) -> str:
    yr, mo = ym
    mname = MONTH_NAMES_ID.get(mo, f"Bulan {mo}")
    return f"{mname} {yr}" if yr else mname

def compute_saldo_mengendap(rows: list[Any]) -> dict[tuple[int, int], list[tuple[Any, float]]]:
    by_day: dict[Any, list[Any]] = {}
    for r in rows:
        if not getattr(r, "is_opening", False) and r.date is not None and r.balance is not None and _month_key(r.date):
            by_day.setdefault(r.date, []).append(r)
    by_month: dict[tuple[int, int], list[tuple[Any, float]]] = {}
    for dt, drows in by_day.items():
        ym = _month_key(dt)
        if not ym:
            continue
        if any(getattr(r, "time", None) is not None for r in drows):
            chosen = max(enumerate(drows), key=lambda item: (getattr(item[1], "time", None) or (-1, -1, -1), item[0]))[1]
        else:
            chosen = drows[-1]
        by_month.setdefault(ym, []).append((dt, float(chosen.balance)))
    for ym in by_month:
        by_month[ym].sort(key=lambda pair: (pair[0] if isinstance(pair[0], date) else date.min))
    return dict(sorted(by_month.items()))

def compute_top5_monthly(rows: list[Any]) -> dict[tuple[int, int], dict[str, list[Any]]]:
    by_month: dict[tuple[int, int], list[Any]] = {}
    for r in rows:
        if not getattr(r, "is_opening", False) and r.date is not None and (ym := _month_key(r.date)):
            by_month.setdefault(ym, []).append(r)
    out: dict[tuple[int, int], dict[str, list[Any]]] = {}
    for ym in sorted(by_month):
        mrows = by_month[ym]
        top_cr = sorted([r for r in mrows if r.credit and r.credit > 0], key=lambda r: float(r.credit), reverse=True)[:5]
        top_db = sorted([r for r in mrows if r.debit and r.debit > 0], key=lambda r: float(r.debit), reverse=True)[:5]
        out[ym] = {"cr": top_cr, "db": top_db}
    return out

def _sheet_title(prefix: str, bank: str, acc: str, used: set[str]) -> str:
    clean_bank = re.sub(r"[\[\]:*?/\\]", "", (bank or "").strip())
    clean_acc = re.sub(r"[\[\]:*?/\\]", "", (acc or "").strip())
    if clean_acc in ("Default", "Unknown"):
        clean_acc = ""
    if clean_bank and clean_acc:
        spaced_sfx = f"{clean_bank} - {clean_acc}"
        compact_sfx = f"{clean_bank}-{clean_acc}"
    else:
        spaced_sfx = compact_sfx = clean_bank or clean_acc

    if not spaced_sfx:
        cand = prefix
    else:
        cand = f"{prefix} {spaced_sfx}"
        if len(cand) > 31:
            cand = f"{prefix} {compact_sfx}"
        if len(cand) > 31:
            short_p = "Saldo" if prefix.lower().startswith("saldo") else prefix[: max(2, 30 - len(compact_sfx))].rstrip()
            cand = f"{short_p} {compact_sfx}"
    cand = cand[:31]
    base = cand
    idx = 2
    while cand in used:
        tag = f"_{idx}"
        cand = f"{base[: 31 - len(tag)]}{tag}"
        idx += 1
    used.add(cand)
    return cand

def _write_saldo_mengendap_sheet(ws: Any, rows: list[Any], bold_font: Font) -> None:
    monthly = compute_saldo_mengendap(rows)
    if not monthly:
        ws.cell(1, 1, "Tanggal").font = bold_font
        ws.cell(1, 2, "Saldo Mengendap").font = bold_font
        ws.column_dimensions["A"].width = 14
        ws.column_dimensions["B"].width = 18
        return
    for m_idx, (ym, items) in enumerate(monthly.items()):
        c0 = 1 + m_idx * 3
        c1 = c0 + 1
        title_cell = ws.cell(1, c0, _month_label(ym))
        title_cell.font = bold_font
        ws.merge_cells(start_row=1, start_column=c0, end_row=1, end_column=c1)
        ws.cell(2, c0, "Tanggal").font = bold_font
        ws.cell(2, c1, "Saldo Mengendap").font = bold_font
        for r_idx, (dt, bal) in enumerate(items, start=3):
            dc = ws.cell(r_idx, c0, dt)
            if isinstance(dt, (date, datetime)):
                dc.number_format = "dd/mm/yyyy"
            bc = ws.cell(r_idx, c1, bal)
            bc.number_format = "#,##0.00"
        ws.column_dimensions[get_column_letter(c0)].width = 14
        ws.column_dimensions[get_column_letter(c1)].width = 18
        ws.column_dimensions[get_column_letter(c1 + 1)].width = 4
    ws.freeze_panes = "A3"

def _write_top5_sheet(ws: Any, rows: list[Any], bold_font: Font) -> None:
    monthly = compute_top5_monthly(rows)
    if not monthly:
        ws.cell(1, 1, "Tanggal").font = bold_font
        ws.cell(1, 2, "Nominal").font = bold_font
        ws.cell(1, 3, "Deskripsi").font = bold_font
        return
    for m_idx, (ym, data) in enumerate(monthly.items()):
        c0 = 1 + m_idx * 4
        label = _month_label(ym)
        ws.cell(1, c0, f"{label} - Top 5 CR").font = bold_font
        ws.merge_cells(start_row=1, start_column=c0, end_row=1, end_column=c0 + 2)
        for offset, h in enumerate(("Tanggal", "Kredit", "Deskripsi")):
            ws.cell(2, c0 + offset, h).font = bold_font
        for idx, r in enumerate(data["cr"], start=3):
            dc = ws.cell(idx, c0, r.date if r.date is not None else "")
            if isinstance(r.date, (date, datetime)):
                dc.number_format = "dd/mm/yyyy"
            ac = ws.cell(idx, c0 + 1, r.credit)
            ac.number_format = "#,##0.00"
            ws.cell(idx, c0 + 2, _safe_str(r.description))

        db_start = 9
        ws.cell(db_start, c0, f"{label} - Top 5 DB").font = bold_font
        ws.merge_cells(start_row=db_start, start_column=c0, end_row=db_start, end_column=c0 + 2)
        for offset, h in enumerate(("Tanggal", "Debit", "Deskripsi")):
            ws.cell(db_start + 1, c0 + offset, h).font = bold_font
        for idx, r in enumerate(data["db"], start=db_start + 2):
            dc = ws.cell(idx, c0, r.date if r.date is not None else "")
            if isinstance(r.date, (date, datetime)):
                dc.number_format = "dd/mm/yyyy"
            ac = ws.cell(idx, c0 + 1, r.debit)
            ac.number_format = "#,##0.00"
            ws.cell(idx, c0 + 2, _safe_str(r.description))

        ws.column_dimensions[get_column_letter(c0)].width = 14
        ws.column_dimensions[get_column_letter(c0 + 1)].width = 18
        ws.column_dimensions[get_column_letter(c0 + 2)].width = 50
        ws.column_dimensions[get_column_letter(c0 + 3)].width = 4
    ws.freeze_panes = "A3"

def excel_bytes(result: dict[str, Any]) -> bytes:
    output = io.BytesIO()
    rows = result.get("rows", [])
    if accounts_map := result.get("accounts"):
        groups = [(data.get("account") or acc, data.get("bank") or "", data["rows"]) for acc, data in accounts_map.items()]
    else:
        by_acc: dict[str, list[Any]] = {}
        for r in rows:
            by_acc.setdefault(getattr(r, "account", "") or "Default", []).append(r)
        groups = [(acc, next((getattr(r, "bank", "") for r in rlist if getattr(r, "bank", "")), ""), rlist) for acc, rlist in by_acc.items()]

    used_names: set[str] = set()
    bold_font = Font(bold=True)

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for acc, bank, acc_rows in groups:
            for prefix, pred in (
                ("Detail", lambda _: True),
                ("In", lambda r: bool(r.credit and r.credit > 0)),
                ("Out", lambda r: bool(r.debit and r.debit > 0)),
            ):
                sname = _sheet_title(prefix, bank, acc, used_names)
                recs = [_transaction_record(r) for r in acc_rows if pred(r)]
                pd.DataFrame(recs, columns=DETAIL_COLS).to_excel(writer, sheet_name=sname, index=False)
                ws = writer.sheets[sname]
                ws.freeze_panes = "A2"
                for col, w in zip(["A", "B", "C", "D", "E", "F"], [14, 16, 16, 16, 80, 22]):
                    ws.column_dimensions[col].width = w
                for r in ws.iter_rows(min_row=2):
                    if isinstance(r[0].value, (date, datetime)):
                        r[0].number_format = "dd/mm/yyyy"
                    for c in r[1:4]:
                        c.number_format = "#,##0.00"

            sm_name = _sheet_title("Saldo Mengendap", bank, acc, used_names)
            sm_ws = writer.book.create_sheet(title=sm_name)
            _write_saldo_mengendap_sheet(sm_ws, acc_rows, bold_font)

            t5_name = _sheet_title("Top 5", bank, acc, used_names)
            t5_ws = writer.book.create_sheet(title=t5_name)
            _write_top5_sheet(t5_ws, acc_rows, bold_font)

            rev_name = _sheet_title("Review", bank, acc, used_names)
            rev_recs = [_review_record(r) for r in acc_rows]
            pd.DataFrame(rev_recs, columns=REVIEW_COLS).to_excel(writer, sheet_name=rev_name, index=False)
            rev_ws = writer.sheets[rev_name]
            rev_ws.freeze_panes = "A2"
            for col, w in zip(["A", "B", "C"], [10, 28, 100]):
                rev_ws.column_dimensions[col].width = w

    return output.getvalue()

__all__ = ["excel_bytes", "compute_saldo_mengendap", "compute_top5_monthly"]