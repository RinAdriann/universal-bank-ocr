import json
import logging
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st

from component import excel_bytes, extract_statement
from component.constants import ALLOWED_EXTS
from component.export import (
    format_date,
    month_label,
    review_df,
    rows_df,
    saldo_mengendap,
    serialize_row,
    top5_monthly,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def render_alerts(summary: dict) -> None:
    if summary.get("chain_reconciles") is True:
        st.info("Balance check passed.")
    elif summary.get("chain_reconciles") is False:
        st.warning("Balance check failed. Review flagged rows.")
    if summary.get("printed_totals_match") is False:
        st.warning("Extracted totals differ from the totals printed on the statement.")


def render_analytics(rows: list, suffix: str = "") -> None:
    with st.expander(f"Saldo Mengendap & Top 5 CR/DB{suffix}"):
        sm_data = saldo_mengendap(rows)
        top5_data = top5_monthly(rows)
        if sm_data:
            cols = st.columns(min(len(sm_data), 4))
            for idx, (ym, items) in enumerate(sm_data.items()):
                with cols[idx % len(cols)]:
                    st.caption(f"Saldo Mengendap — {month_label(ym)}")
                    df = pd.DataFrame(
                        [(format_date(dt), bal) for dt, bal in items],
                        columns=["Tanggal", "Saldo Mengendap"],
                    )
                    st.dataframe(df, width="stretch", hide_index=True)

        if top5_data:
            for ym, data in top5_data.items():
                col_cr, col_db = st.columns(2)
                with col_cr:
                    st.caption(f"{month_label(ym)} — Top 5 CR")
                    cr_df = pd.DataFrame(
                        [{"Tanggal": format_date(r.date), "Kredit": r.credit, "Deskripsi": r.description} for r in data["cr"]],
                        columns=["Tanggal", "Kredit", "Deskripsi"],
                    )
                    st.dataframe(cr_df, width="stretch", hide_index=True)
                with col_db:
                    st.caption(f"{month_label(ym)} — Top 5 DB")
                    db_df = pd.DataFrame(
                        [{"Tanggal": format_date(r.date), "Debit": r.debit, "Deskripsi": r.description} for r in data["db"]],
                        columns=["Tanggal", "Debit", "Deskripsi"],
                    )
                    st.dataframe(db_df, width="stretch", hide_index=True)


def render_account_view(rows: list, summary: dict, label: str = "") -> None:
    suffix = f" ({label})" if label else ""
    if label:
        st.subheader(f"Rekening: {label}")
    render_alerts(summary)
    st.dataframe(rows_df(rows), width="stretch", hide_index=True)
    render_analytics(rows, suffix)
    with st.expander(f"Review information{suffix}"):
        st.dataframe(review_df(rows), width="stretch", hide_index=True)


st.set_page_config(page_title="Indonesian Bank Statement Extractor", layout="wide")
st.title("PDF/Image to Excel")

uploaded_files = st.file_uploader(
    "Upload PDF or statement image(s)",
    type=ALLOWED_EXTS,
    accept_multiple_files=True,
)

if uploaded_files and st.button("Extract", type="primary"):
    started_at = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_paths: list[str] = []
            for idx, uploaded in enumerate(uploaded_files, start=1):
                ext = os.path.splitext(uploaded.name)[1].lower().lstrip(".")
                safe_ext = ext if ext in ALLOWED_EXTS else "pdf"
                tmp_path = os.path.join(temp_dir, f"statement_{idx}.{safe_ext}")
                with open(tmp_path, "wb") as tmp_file:
                    tmp_file.write(uploaded.getvalue())
                temp_paths.append(tmp_path)

            timer_placeholder = st.empty()
            with st.spinner("Extracting transactions..."):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(extract_statement, temp_paths)
                    while not future.done():
                        elapsed = time.perf_counter() - started_at
                        timer_placeholder.caption(f"Processing time: {elapsed:.2f} seconds")
                        time.sleep(0.2)
                    result = future.result()
                    excel_data = excel_bytes(result)

        elapsed = time.perf_counter() - started_at
        timer_placeholder.caption(f"Processing time: {elapsed:.2f} seconds")
        logger.info(
            "Extraction finished: %s rows from %s pages in %.3f seconds",
            len(result["rows"]),
            len(result["pages"]),
            elapsed,
        )

        accounts = result.get("accounts") or {}
        year = result.get("document_year")
        year_label = f" (document year: {year})" if year else ""

        if len(accounts) == 1:
            only_acc = next(iter(accounts.values()))
            tag = " - ".join(x for x in (only_acc.get("bank"), only_acc.get("account")) if x)
            acc_label = f" ({tag})" if tag else ""
        elif len(accounts) > 1:
            acc_label = f" across {len(accounts)} account(s)"
        else:
            acc_label = ""

        st.success(f"Completed: {len(result['rows'])} transaction rows from {len(result['pages'])} pages{acc_label}{year_label}.")

        flagged_count = sum(bool(r.validation) for r in result["rows"])
        if flagged_count:
            st.warning(f"{flagged_count} row(s) need review")

        if len(accounts) > 1:
            for acc_key, acc_data in accounts.items():
                label = " - ".join(x for x in (acc_data.get("bank"), acc_data.get("account")) if x) or acc_key
                render_account_view(acc_data["rows"], acc_data.get("summary", {}), label=label)
        else:
            render_account_view(result["rows"], result.get("summary", {}))

        json_payload = {
            "document_year": year,
            "summary": result.get("summary"),
            "accounts": {
                k: {**v, "rows": [serialize_row(r) for r in v.get("rows", [])]}
                for k, v in accounts.items()
            },
            "pages": result["pages"],
            "rows": [serialize_row(r) for r in result["rows"]],
        }

        st.download_button(
            "Download Excel",
            data=excel_data,
            file_name="result.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.download_button(
            "Download JSON",
            data=json.dumps(json_payload, ensure_ascii=False, indent=2),
            file_name="result.json",
            mime="application/json",
        )

        with st.expander("Page diagnostics"):
            st.json(result["pages"])
    except Exception as exc:
        logger.exception("Extraction failed after %.3f seconds", time.perf_counter() - started_at)
        st.error(str(exc))
