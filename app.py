import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st

from component import excel_bytes, extract_statement
from component.core import ALLOWED_EXTS, StatementAuditResult
from component.report import build_portfolio_overview_df, calculate_account_counterparty_recap, calculate_monthly_metrics, collapse_saldo_streaks, format_date, get_account_holder_name, month_label, review_df, rows_df, saldo_mengendap, serialize_row, top5_monthly

ISSUE_DISPLAY_COLS = [
    "transaction_index",
    "pdf_page",
    "date",
    "issue_type",
    "printed_value",
    "expected_value",
    "discrepancy",
    "description",
]


def render_top_summary_banner(rows: list, summary: dict, audit_result: StatementAuditResult | None) -> None:
    total_txns = len(rows)
    reconciled_pct = summary.get(
        "reconciled_pct",
        round(100.0 * sum(1 for r in rows if getattr(r, "is_reconciled", True)) / max(total_txns, 1), 1),
    )
    total_flags = len(audit_result.issues) if audit_result is not None else summary.get("rows_flagged", 0)

    col_tx, col_rec, col_flg = st.columns(3)
    col_tx.metric("Total Transactions", total_txns)
    col_rec.metric("Reconciliation %", f"{reconciled_pct:.1f}%")
    col_flg.metric("Total Flags", total_flags)

    if audit_result is not None:
        st.code(audit_result.summary_line(), language="text")

    if summary.get("chain_reconciles") is True and total_flags == 0:
        st.info("Balance check passed.")
    elif summary.get("chain_reconciles") is False or total_flags > 0:
        st.warning("Audit issues or balance discrepancies detected. Inspect 'View Audit Issues & Discrepancies' below.")
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
                    streaks = collapse_saldo_streaks(items)
                    df = pd.DataFrame(
                        [(format_date(dt), bal, count, round(bal * count, 2)) for dt, bal, count in streaks],
                        columns=["Tanggal", "Saldo Mengendap", "Count", "Total"],
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


def render_memo_summary(result: dict) -> None:
    with st.expander("Executive Credit Memo Summary", expanded=True):
        overview_df = build_portfolio_overview_df(result)
        st.caption("Portfolio Multi-Account Overview")
        st.dataframe(overview_df, width="stretch", hide_index=True)

        all_rows = result.get("rows", [])
        m_metrics = calculate_monthly_metrics(all_rows)
        if m_metrics:
            st.caption("Monthly Turnover & Net Cash Flow (MK - MD)")
            m_df = pd.DataFrame(
                [
                    {
                        "Periode": v["label"],
                        "Mutasi Debet (MD)": v["md"],
                        "Mutasi Kredit (MK)": v["mk"],
                        "Selisih (MK - MD)": v["selisih"],
                        "Rata-rata Pengendapan": v["pengendapan"],
                        "Saldo Akhir (SA)": v["sa"],
                        "Jumlah Transaksi": v["tx_count"],
                    }
                    for v in m_metrics.values()
                ]
            )
            st.dataframe(m_df, width="stretch", hide_index=True)

        accounts_map = result.get("accounts")
        acc_groups = (
            [
                (
                    v.get("bank") or "-",
                    v.get("account") or "-",
                    get_account_holder_name(v, default="-"),
                    v.get("rows", []),
                )
                for v in accounts_map.values()
            ]
            if accounts_map
            else [("-", "-", get_account_holder_name(result, default="-"), all_rows)]
        )

        for bank_name, acc_no, holder, acc_rows in acc_groups:
            header_tag = f"{bank_name}, {acc_no} ({holder})"
            col_md, col_mk = st.columns(2)
            for col_obj, flow_title, is_cr in (
                (col_md, f"Rekapan Transaksi MD — {header_tag}", False),
                (col_mk, f"Rekapan Transaksi MK — {header_tag}", True),
            ):
                recap = calculate_account_counterparty_recap(acc_rows, is_credit=is_cr, top_n=None)
                months = recap["months"]
                m_labels = [month_label(ym) for ym in months]
                rows_table = []
                for item in recap["items"]:
                    r_dict: dict = {"Pihak Terkait": item["counterparty"]}
                    for ym, m_lbl in zip(months, m_labels):
                        r_dict[m_lbl] = item["monthly"].get(ym, 0.0)
                    r_dict["Total Result"] = item["total"]
                    rows_table.append(r_dict)
                with col_obj:
                    st.caption(flow_title)
                    st.dataframe(
                        pd.DataFrame(rows_table, columns=["Pihak Terkait"] + m_labels + ["Total Result"]),
                        width="stretch",
                        hide_index=True,
                    )


def render_account_view(
    rows: list,
    summary: dict,
    audit_result: StatementAuditResult | None = None,
    label: str = "",
) -> None:
    suffix = f" ({label})" if label else ""
    if label:
        st.subheader(f"Rekening: {label}")

    render_top_summary_banner(rows, summary, audit_result)

    tx_df = audit_result.transactions_df if audit_result is not None else rows_df(rows)
    st.dataframe(tx_df, width="stretch", hide_index=False)

    with st.expander(f"View Audit Issues & Discrepancies{suffix}", expanded=bool(audit_result and not audit_result.is_clean)):
        if audit_result is not None:
            issues_table = audit_result.issues_df[ISSUE_DISPLAY_COLS]
        else:
            issues_table = pd.DataFrame(columns=ISSUE_DISPLAY_COLS)
        st.dataframe(issues_table, width="stretch", hide_index=True)

    render_analytics(rows, suffix)
    with st.expander(f"Review information{suffix}"):
        st.dataframe(review_df(rows), width="stretch", hide_index=True)


@st.cache_resource
def get_ner():
    from component.ner import TransactionNER
    return TransactionNER()


st.set_page_config(page_title="Indonesian Bank Statement Extractor", layout="wide")
st.title("PDF to Excel")
get_ner()

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

        render_memo_summary(result)

        if len(accounts) > 1:
            for acc_key, acc_data in accounts.items():
                label = " - ".join(x for x in (acc_data.get("bank"), acc_data.get("account")) if x) or acc_key
                render_account_view(
                    acc_data["rows"],
                    acc_data.get("summary", {}),
                    audit_result=acc_data.get("audit_result"),
                    label=label,
                )
        else:
            render_account_view(
                result["rows"],
                result.get("summary", {}),
                audit_result=result.get("audit_result"),
            )

        json_payload = {
            "document_year": year,
            "summary": result.get("summary"),
            "accounts": {
                k: {
                    "account": v.get("account"),
                    "bank": v.get("bank"),
                    "document_year": v.get("document_year"),
                    "summary": v.get("summary"),
                    "rows": [serialize_row(r) for r in v.get("rows", [])],
                }
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
        st.error(str(exc))
