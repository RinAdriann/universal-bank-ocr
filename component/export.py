import io
from datetime import date, datetime
import pandas as pd
from typing import Any
from .transactioncat import category
from .transactioncat import flow


def _transaction_record(row: Any) -> dict[str, Any]:
    return {
        "Tanggal": row.date if row.date is not None else "",
        "Kredit": row.credit,
        "Debit": row.debit,
        "Arus": flow(row.debit, row.credit),
        "Saldo": row.balance,
        "Deskripsi": row.description,
        "Kategori Transaksi": category(row.description, row.debit, row.credit),
    }

def excel_bytes(result: dict[str, Any]) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        rows = result["rows"]

        detail_columns = [
            "Tanggal", "Kredit", "Debit", "Saldo",
            "Deskripsi", "Kategori Transaksi",
        ]

        detail_rows = [_transaction_record(row) for row in rows]
        pd.DataFrame(detail_rows, columns=detail_columns).to_excel(
            writer, sheet_name="Detail transaksi rekening", index=False
        )
        pd.DataFrame(
            [_transaction_record(row) for row in rows if row.credit and row.credit > 0],
            columns=detail_columns,
        ).to_excel(writer, sheet_name="In", index=False)
        pd.DataFrame(
            [_transaction_record(row) for row in rows if row.debit and row.debit > 0],
            columns=detail_columns,
        ).to_excel(writer, sheet_name="Out", index=False)

        pd.DataFrame([{
            "Halaman": row.page,
            "Validation flags": ", ".join(row.validation),
            "Full detected text": row.raw_text
        } for row in result["rows"]]).to_excel(writer, sheet_name="Review", index=False)

        for name in writer.sheets:
            sheet = writer.sheets[name]
            sheet.freeze_panes = "A2"
            if name in {"Detail transaksi rekening", "In", "Out"}:
                widths = [14, 16, 16, 16, 16, 80, 22]
            else:
                widths = [10, 12, 18, 28, 24, 100]
            for col, w in zip(["A", "B", "C", "D", "E", "F", "G"], widths):
                sheet.column_dimensions[col].width = w
            if name in {"Detail transaksi rekening", "In", "Out"}:
                for r in sheet.iter_rows(min_row=2):
                    if isinstance(r[0].value, (date, datetime)):
                        r[0].number_format = "dd/mm/yyyy"
                    for c in r[1:5]:
                        c.number_format = "#,##0.00"
    return output.getvalue()

__all__ = ["excel_bytes"]
