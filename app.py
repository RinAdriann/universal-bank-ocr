import json
import logging
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime

import pandas as pd
import streamlit as st

from component import category, excel_bytes, extract_statement
from component.export import compute_saldo_mengendap, compute_top5_monthly, _month_label

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ALLOWED_EXTS = ["pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"]
DETAIL_COLS = ["Tanggal", "Kredit", "Debit", "Saldo", "Deskripsi", "Kategori Transaksi"]

def _rows_df(rows: list) -> pd.DataFrame:
    return pd.DataFrame(
        [{"Tanggal": r.date or "", "Kredit": r.credit, "Debit": r.debit, "Saldo": r.balance, "Deskripsi": r.description, "Kategori Transaksi": category(r.description, r.debit, r.credit)} for r in rows],
        columns=DETAIL_COLS,
    )

def _review_df(rows: list) -> pd.DataFrame:
    return pd.DataFrame(
        [{"Halaman": r.page, "Confidence": round(getattr(r, "confidence", 1.0), 3), "Human review status": "Needs review" if r.validation else "Pending review", "Validation flags": ", ".join(r.validation), "Full detected text": r.raw_text} for r in rows]
    )

def _render_alerts(summary: dict) -> None:
    if summary.get("chain_reconciles") is True:
        st.info("Balance check passed.")
    elif summary.get("chain_reconciles") is False:
        st.warning("Balance check failed. Review flagged rows.")
    if summary.get("printed_totals_match") is False:
        st.warning("Extracted totals differ from the totals printed on the statement.")

def _render_account_analytics(rows: list, suffix: str = "") -> None:
    with st.expander(f"Saldo Mengendap & Top 5 CR/DB{suffix}"):
        sm = compute_saldo_mengendap(rows)
        t5 = compute_top5_monthly(rows)
        if sm:
            cols = st.columns(min(len(sm), 4))
            for idx, (ym, items) in enumerate(sm.items()):
                with cols[idx % len(cols)]:
                    st.caption(f"Saldo Mengendap — {_month_label(ym)}")
                    st.dataframe(pd.DataFrame(items, columns=["Tanggal", "Saldo Mengendap"]), width="stretch", hide_index=True)
        if t5:
            for ym, data in t5.items():
                c1, c2 = st.columns(2)
                with c1:
                    st.caption(f"{_month_label(ym)} — Top 5 CR")
                    st.dataframe(pd.DataFrame([{"Tanggal": r.date or "", "Kredit": r.credit, "Deskripsi": r.description} for r in data["cr"]], columns=["Tanggal", "Kredit", "Deskripsi"]), width="stretch", hide_index=True)
                with c2:
                    st.caption(f"{_month_label(ym)} — Top 5 DB")
                    st.dataframe(pd.DataFrame([{"Tanggal": r.date or "", "Debit": r.debit, "Deskripsi": r.description} for r in data["db"]], columns=["Tanggal", "Debit", "Deskripsi"]), width="stretch", hide_index=True)

def _serialize_row(row) -> dict:
    item = asdict(row)
    item["date"] = row.date.isoformat() if isinstance(row.date, (date, datetime)) else row.date
    item.pop("date_parts", None)
    item.pop("time", None)
    return item

st.set_page_config(page_title="Indonesian Bank Statement Extractor", layout="wide")
st.title("PDF/Image to Excel")

uploaded_files = st.file_uploader("Upload PDF or statement image(s)", type=ALLOWED_EXTS, accept_multiple_files=True)

if uploaded_files and st.button("Extract", type="primary"):
    started_at = time.perf_counter()
    temp_paths: list[str] = []
    try:
        for uf in uploaded_files:
            ext = os.path.splitext(uf.name)[1].lower().lstrip(".")
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}" if ext in ALLOWED_EXTS else ".pdf") as tmp:
                tmp.write(uf.getvalue())
                temp_paths.append(tmp.name)

        timer_placeholder = st.empty()
        with st.spinner("Extracting transactions..."):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(extract_statement, temp_paths)
                while not future.done():
                    timer_placeholder.caption(f"Processing time: {time.perf_counter() - started_at:.2f} seconds")
                    time.sleep(0.2)
                result = future.result()
                excel_data = excel_bytes(result)

        elapsed = time.perf_counter() - started_at
        timer_placeholder.caption(f"Processing time: {elapsed:.2f} seconds")
        logger.info("Extraction finished: %s rows from %s pages in %.3f seconds", len(result["rows"]), len(result["pages"]), elapsed)

        accounts = result.get("accounts") or {}
        year = result.get("document_year")
        year_label = f" (document year: {year})" if year else ""
        if len(accounts) == 1:
            only_v = next(iter(accounts.values()))
            b_str = only_v.get("bank") or ""
            a_str = only_v.get("account") or ""
            tag = " - ".join(x for x in (b_str, a_str) if x)
            acc_label = f" ({tag})" if tag else ""
        else:
            acc_label = f" across {len(accounts)} account(s)" if len(accounts) > 1 else ""
        st.success(f"Completed: {len(result['rows'])} transaction rows from {len(result['pages'])} pages{acc_label}{year_label}.")

        if failed := sum(bool(r.validation) for r in result["rows"]):
            st.warning(f"{failed} row(s) need review")

        if len(accounts) > 1:
            for acc_key, acc_data in accounts.items():
                bank = acc_data.get("bank") or ""
                acc_no = acc_data.get("account") or acc_key
                label = f"{bank} - {acc_no}" if bank else acc_no
                st.subheader(f"Rekening: {label}")
                _render_alerts(acc_data.get("summary", {}))
                st.dataframe(_rows_df(acc_data["rows"]), width="stretch", hide_index=True)
                _render_account_analytics(acc_data["rows"], f" ({label})")
                with st.expander(f"Review information ({label})"):
                    st.dataframe(_review_df(acc_data["rows"]), width="stretch", hide_index=True)
        else:
            _render_alerts(result.get("summary", {}))
            st.dataframe(_rows_df(result["rows"]), width="stretch", hide_index=True)
            _render_account_analytics(result["rows"])
            with st.expander("Review information"):
                st.dataframe(_review_df(result["rows"]), width="stretch", hide_index=True)

        st.download_button("Download Excel", data=excel_data, file_name="result.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.download_button(
            "Download JSON",
            data=json.dumps(
                {
                    "document_year": year,
                    "summary": result.get("summary"),
                    "accounts": {k: {**v, "rows": [_serialize_row(r) for r in v.get("rows", [])]} for k, v in accounts.items()},
                    "pages": result["pages"],
                    "rows": [_serialize_row(r) for r in result["rows"]],
                },
                ensure_ascii=False,
                indent=2,
            ),
            file_name="result.json",
            mime="application/json",
        )

        with st.expander("Page diagnostics"):
            st.json(result["pages"])
    except Exception as exc:
        logger.exception("Extraction failed after %.3f seconds", time.perf_counter() - started_at)
        st.error(str(exc))
    finally:
        for p in temp_paths:
            if os.path.exists(p):
                os.remove(p)