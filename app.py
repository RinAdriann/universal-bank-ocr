import json
import os
import tempfile

import pandas as pd
import streamlit as st

from parser import excel_bytes, extract_statement, jsonable, rows_dataframe

st.set_page_config(page_title="Indonesian Bank Statement Extractor", layout="wide")
st.title("PDF to Excel")

uploaded_file = st.file_uploader("Upload", type=["pdf"])
use_table_model = st.checkbox(
    "Use PaddleOCR table recognition (slower, optional)",
    help="Requires requirements-table.txt. Falls back to the fast coordinate extractor when no table is returned.",
)

if uploaded_file is not None and st.button("Extract", type="primary"):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temporary_file:
        temporary_file.write(uploaded_file.getvalue())
        pdf_path = temporary_file.name
    try:
        with st.spinner("..."):
            result = extract_statement(pdf_path, use_table_model=use_table_model)
        dataframe = rows_dataframe(result)
        failed = sum(bool(row.validation) for row in result["rows"])
        st.success(f"Completed: {len(result['rows'])} transaction rows from {len(result['pages'])} pages.")
        if failed:
            st.warning(f"{failed} row(s) need review")
        st.dataframe(dataframe, use_container_width=True, hide_index=True)
        with st.expander("Review information"):
            st.dataframe(
                pd.DataFrame([
                    {
                        "Halaman": row.page,
                        "Confidence": round(row.confidence, 3),
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
            " Excel",
            data=excel_bytes(result),
            file_name="result.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.download_button(
            "Download JSON",
            data=json.dumps(jsonable(result), ensure_ascii=False, indent=2),
            file_name="result.json",
            mime="application/json",
        )
        with st.expander("Page diagnostics"):
            st.json(result["pages"])
    except Exception as exc:
        st.error(str(exc))
    finally:
        os.remove(pdf_path)
