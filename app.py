import os
import tempfile
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime

import json
import pandas as pd
import streamlit as st

from component import category, excel_bytes, extract_statement, flow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

st.set_page_config(page_title="Indonesian Bank Statement Extractor", layout="wide")
st.title("PDF/Image to Excel")

uploaded_file = st.file_uploader(
    "Upload a PDF or statement image",
    type=["pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"],
)

if uploaded_file is not None and st.button("Extract", type="primary"):
    started_at = time.perf_counter()
    logger.info("Extraction started: %s", uploaded_file.name)
    suffix = os.path.splitext(uploaded_file.name)[1].lower() or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary_file:
        temporary_file.write(uploaded_file.getvalue())
        pdf_path = temporary_file.name

    try:
        timer_placeholder = st.empty()
        with st.spinner("Extracting transactions..."):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(extract_statement, pdf_path)
                while not future.done():
                    elapsed_seconds = time.perf_counter() - started_at
                    timer_placeholder.caption(
                        f"Processing time: {elapsed_seconds:.2f} seconds"
                    )
                    time.sleep(0.2)
                result = future.result()
                excel_data = excel_bytes(result)
        elapsed_seconds = time.perf_counter() - started_at
        timer_placeholder.caption(f"Processing time: {elapsed_seconds:.2f} seconds")
        logger.info(
            "Extraction finished: %s rows from %s pages in %.3f seconds",
            len(result["rows"]),
            len(result["pages"]),
            elapsed_seconds,
        )

        dataframe = pd.DataFrame(
            [
                {
                    "Tanggal": row.date if row.date is not None else "",
                    "Kredit": row.credit,
                    "Debit": row.debit,
                    "Saldo": row.balance,
                    "Deskripsi": row.description,
                    "Kategori Transaksi": category(
                        row.description, row.debit, row.credit
                    ),
                }
                for row in result["rows"]
            ],
            columns=[
                "Tanggal",
                "Kredit",
                "Debit",
                "Saldo",
                "Deskripsi",
                "Kategori Transaksi",
            ],
        )

        failed = sum(bool(row.validation) for row in result["rows"])
        year = result.get("document_year")
        year_label = f" (document year: {year})" if year else ""
        st.success(
            f"Completed: {len(result['rows'])} transaction rows from "
            f"{len(result['pages'])} pages{year_label}."
        )

        if failed:
            st.warning(f"{failed} row(s) need review")

        summary = result.get("summary", {})
        if summary.get("chain_reconciles") is True:
            st.info("Balance check passed.")
        elif summary.get("chain_reconciles") is False:
            st.warning("Balance check failed. Review flagged rows.")
        if summary.get("printed_totals_match") is False:
            st.warning("Extracted totals differ from the totals printed on the statement.")

        # Render the dataframe in the UI
        st.dataframe(dataframe, use_container_width=True, hide_index=True)

        with st.expander("Review information"):
            st.dataframe(
                pd.DataFrame([
                    {
                        "Halaman": row.page,
                        "Confidence": round(getattr(row, "confidence", 1.0), 3),
                        "Human review status": "Needs review" if row.validation else "Pending review",
                        "Validation flags": ", ".join(row.validation),
                        "Full detected text": row.raw_text,
                    }
                    for row in result["rows"]
                ]),
                use_container_width=True,
                hide_index=True,
            )

        st.download_button(
            "Download Excel",
            data=excel_data,
            file_name="result.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        json_rows = []
        for row in result["rows"]:
            item = asdict(row)
            item["date"] = (
                row.date.isoformat()
                if isinstance(row.date, (date, datetime))
                else row.date
            )
            item.pop("date_parts", None)
            json_rows.append(item)
        st.download_button(
            "Download JSON",
            data=json.dumps(
                {
                    "document_year": result.get("document_year"),
                    "summary": result.get("summary"),
                    "pages": result["pages"],
                    "rows": json_rows,
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
        elapsed_seconds = time.perf_counter() - started_at
        logger.exception("Extraction failed after %.3f seconds", elapsed_seconds)
        st.error(str(exc))
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)