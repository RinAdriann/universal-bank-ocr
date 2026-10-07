"""Output/export API."""

import io
from datetime import date, datetime
import pandas as pd
from typing import Any
from .categories import category

def excel_bytes(result: dict[str, Any]) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame([{
            "Tanggal": row.date if row.date is not None else "",
            "Kredit": row.credit,
            "Debit": row.debit,
            "Saldo": row.balance,
            "Deskripsi": row.description,
            "Kategori Transaksi": category(row.description, row.debit, row.credit),
        } for row in result["rows"]]).to_excel(writer, sheet_name="Detail transaksi rekening - 1", index=False)

        pd.DataFrame([{
            "Halaman": row.page, "Confidence": round(row.confidence, 3),
            "Human review status": "Needs review" if row.validation else "Pending review",
            "Validation flags": ", ".join(row.validation), "Referensi": row.reference,
            "Full detected text": row.raw_text,
        } for row in result["rows"]]).to_excel(writer, sheet_name="Review", index=False)

        for name in writer.sheets:
            sheet = writer.sheets[name]
            sheet.freeze_panes = "A2"
            widths = [14, 16, 16, 16, 80, 22] if name == "Detail transaksi rekening - 1" else [10, 12, 18, 28, 24, 100]
            for col, w in zip(["A", "B", "C", "D", "E", "F"], widths):
                sheet.column_dimensions[col].width = w
            if name == "Detail transaksi rekening - 1":
                for r in sheet.iter_rows(min_row=2):
                    if isinstance(r[0].value, (date, datetime)):
                        r[0].number_format = "dd/mm/yyyy"
                    for c in r[1:4]:
                        c.number_format = "#,##0.00"
    return output.getvalue()

__all__ = ["excel_bytes"]
