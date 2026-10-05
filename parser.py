from __future__ import annotations

import argparse
from datetime import datetime
from functools import lru_cache
import io
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import cv2
import pymupdf as fitz
import numpy as np
import pandas as pd


HEADER_WORDS = {
    "tanggal", "tgl", "date", "waktu", "time", "uraian", "keterangan",
    "deskripsi", "description", "transaksi", "mutasi", "debit", "debet",
    "kredit", "credit", "saldo", "balance", "nominal", "jumlah", "amount",
    "penarikan", "withdrawal", "setoran", "deposit",
}
DATE_RE = re.compile(
    r"\b(?P<day>\d{1,2})[./-](?P<month>\d{1,2})(?:[./-](?P<year>\d{2,4}))?\b"
    r"|\b(?P<day_name>\d{1,2})\s+(?P<month_name>[A-Za-z]+)(?:\s+(?P<year_name>\d{2,4}))?\b",
    re.IGNORECASE,
)


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float = 1.0
    source: str = "pdf"

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return max(1.0, self.y1 - self.y0)


@dataclass
class Cell:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float


@dataclass
class ExtractedRow:
    page: int
    cells: list[Cell]
    raw_text: str
    date: str | None = None
    description: str = ""
    debit: float | None = None
    credit: float | None = None
    amount: float | None = None
    balance: float | None = None
    confidence: float = 0.0
    validation: list[str] = field(default_factory=list)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()


def _looks_like_date(value: str) -> bool:
    return DATE_RE.search(value) is not None


def normalize_date(value: str) -> str | None:
    """Keep the detected date text without interpreting it as a timestamp."""
    match = DATE_RE.search(value.strip())
    return _clean_text(match.group(0)) if match else None


def _excel_date_value(value: str) -> datetime | str:
    """Create an explicit day-first Excel date; keep unrecognized text unchanged."""
    match = DATE_RE.fullmatch(value.strip())
    if not match:
        return value
    try:
        if match.group("day_name"):
            month_names = {
                "jan": 1, "januari": 1, "feb": 2, "februari": 2,
                "mar": 3, "maret": 3, "apr": 4, "april": 4,
                "mei": 5, "jun": 6, "juni": 6, "jul": 7, "juli": 7,
                "agu": 8, "agt": 8, "agustus": 8, "sep": 9,
                "sept": 9, "september": 9, "okt": 10, "oktober": 10,
                "nov": 11, "november": 11, "des": 12, "desember": 12,
            }
            day = int(match.group("day_name"))
            month = month_names[match.group("month_name").lower()]
            year_text = match.group("year_name")
        else:
            day = int(match.group("day"))
            month = int(match.group("month"))
            year_text = match.group("year")
        year = int(year_text) if year_text else 1900
        if year < 100:
            year += 2000
        return datetime(year, month, day)
    except (KeyError, TypeError, ValueError, OverflowError):
        return value


def normalize_amount(value: str) -> float | None:
    """Parse common Indonesian and OCR-corrupted currency formats."""
    text = _clean_text(value).upper()
    if not text or _looks_like_date(text):
        return None
    negative = text.startswith("-") or ("(" in text and ")" in text)
    negative = negative or bool(re.search(r"\b(DB|DEBIT|DR)\b", text))
    amount_text = re.sub(r"\b(RP|IDR|DB|CR|DEBIT|CREDIT|DR)\b", "", text)
    if re.search(r"[A-Z]", amount_text):
        return None
    text = amount_text
    text = re.sub(r"[^0-9,.\-]", "", text).replace("-", "")
    if not text or not re.search(r"\d", text):
        return None
    if "," in text and "." in text:
        decimal_separator = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        text = text.replace(thousands_separator, "").replace(decimal_separator, ".")
    elif "," in text:
        parts = text.split(",")
        text = "".join(parts[:-1]) + "." + parts[-1] if len(parts[-1]) in (1, 2) else "".join(parts)
    elif text.count(".") > 1:
        text = text.replace(".", "")
    elif "." in text:
        left, right = text.split(".", 1)
        text = left + right if len(right) == 3 else text
    try:
        number = float(text)
        return -number if negative else number
    except ValueError:
        return None


EMBEDDED_AMOUNT_RE = re.compile(
    r"(?<![\w/])(?:\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"
    r"|\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?)(?![\w/])"
)


def _embedded_amounts(value: str) -> list[tuple[str, float]]:
    amounts: list[tuple[str, float]] = []
    for match in EMBEDDED_AMOUNT_RE.finditer(value):
        parsed = normalize_amount(match.group(0))
        if parsed is not None:
            amounts.append((match.group(0), abs(parsed)))
    return amounts


def _remove_embedded_amounts(value: str) -> str:
    return _clean_text(EMBEDDED_AMOUNT_RE.sub("", value))


@lru_cache(maxsize=1)
def _rapidocr_engine() -> Any:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            "Install dependencies with "
            "`pip install -r requirements.txt`."
        ) from exc
    return RapidOCR()


def _extract_rapidocr(image: np.ndarray) -> list[Word]:
    ocr = _rapidocr_engine()
    result, _ = ocr(image)
    words: list[Word] = []
    for item in result or []:
        box, text, confidence = item
        points = np.asarray(box, dtype=float)
        words.append(
            Word(
                text=_clean_text(str(text)),
                x0=float(points[:, 0].min()),
                y0=float(points[:, 1].min()),
                x1=float(points[:, 0].max()),
                y1=float(points[:, 1].max()),
                confidence=float(confidence),
                source="ocr",
            )
        )
    return [word for word in words if word.text]


def _extract_pdf_words(page: fitz.Page) -> list[Word]:
    words: list[Word] = []
    for item in page.get_text("words", sort=True):
        x0, y0, x1, y1, text = item[:5]
        text = _clean_text(text)
        if text:
            words.append(Word(text, x0, y0, x1, y1))
    return words


def _preprocess(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(gray, None, 7, 7, 21)
    return cv2.cvtColor(denoised, cv2.COLOR_GRAY2BGR)


def _digital_page_is_usable(words: Sequence[Word]) -> bool:
    """Use the text layer only when it contains enough statement-like content."""
    if len(words) < 8:
        return False
    date_count = sum(_looks_like_date(word.text) for word in words)
    amount_count = sum(normalize_amount(word.text) is not None for word in words)
    header_count = sum(
        re.sub(r"[^a-z]", "", word.text.lower()) in HEADER_WORDS for word in words
    )
    return date_count >= 1 and amount_count >= 1 and (header_count >= 1 or date_count >= 2)


def _render_page(page: fitz.Page, dpi: int) -> np.ndarray:
    scale = dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        return cv2.cvtColor(image, cv2.COLOR_RGBA2BGR)
    return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)


def _page_words(page: fitz.Page, dpi: int = 220) -> tuple[list[Word], bool]:
    pdf_words = _extract_pdf_words(page)
    page_area = max(1.0, page.rect.width * page.rect.height)
    text_area = sum((word.x1 - word.x0) * (word.y1 - word.y0) for word in pdf_words)
    has_reasonable_text_density = text_area / page_area < 0.25
    is_digital = has_reasonable_text_density and _digital_page_is_usable(pdf_words)
    if is_digital:
        return pdf_words, True
    image = _render_page(page, dpi)
    return _extract_rapidocr(_preprocess(image)), False


def _group_rows(words: Sequence[Word]) -> list[list[Word]]:
    if not words:
        return []
    tolerance = max(3.0, float(np.median([word.height for word in words])) * 0.7)
    rows: list[list[Word]] = []
    row_centers: list[float] = []
    for word in sorted(words, key=lambda item: (item.center_y, item.x0)):
        candidates = [index for index, center in enumerate(row_centers)
                      if abs(word.center_y - center) <= tolerance]
        if not candidates:
            rows.append([word])
            row_centers.append(word.center_y)
            continue
        index = min(candidates, key=lambda candidate: abs(word.center_y - row_centers[candidate]))
        rows[index].append(word)
        row_centers[index] = sum(item.center_y for item in rows[index]) / len(rows[index])
    return [sorted(row, key=lambda item: item.x0) for row in sorted(rows, key=lambda row: min(item.y0 for item in row))]


def _is_header(row: Sequence[Word]) -> bool:
    normalized = {re.sub(r"[^a-z]", "", word.text.lower()) for word in row}
    return len(normalized & HEADER_WORDS) >= 2


def _row_to_cells(row: Sequence[Word], gap_factor: float = 1.8) -> list[Cell]:
    if not row:
        return []
    median_height = float(np.median([word.height for word in row]))
    cells: list[list[Word]] = [[row[0]]]
    for word in row[1:]:
        previous = cells[-1][-1]
        gap = word.x0 - previous.x1
        if gap > median_height * gap_factor:
            cells.append([word])
        else:
            cells[-1].append(word)
    return [
        Cell(
            text=_clean_text(" ".join(word.text for word in cell)),
            x0=min(word.x0 for word in cell),
            y0=min(word.y0 for word in cell),
            x1=max(word.x1 for word in cell),
            y1=max(word.y1 for word in cell),
            confidence=float(np.mean([word.confidence for word in cell])),
        )
        for cell in cells
    ]


def _is_transaction_row(row: Sequence[Word]) -> bool:
    text = _clean_text(" ".join(word.text for word in row))
    has_date = _looks_like_date(text)
    numeric_values = sum(normalize_amount(word.text) is not None for word in row)
    has_embedded_amount = bool(_embedded_amounts(text))
    return has_date and (numeric_values >= 1 or has_embedded_amount)


def _classify_cells(cells: Sequence[Cell]) -> dict[str, Any]:
    date_cell = next((cell for cell in cells if _looks_like_date(cell.text)), None)
    numeric = [(cell, normalize_amount(cell.text)) for cell in cells]
    numeric = [(cell, value) for cell, value in numeric if value is not None]
    non_numeric = [
        cell for cell in cells
        if cell is not date_cell and normalize_amount(cell.text) is None
    ]
    description_cells = non_numeric
    description = _clean_text(" ".join(cell.text for cell in description_cells))
    embedded = _embedded_amounts(description)
    if embedded:
        description = _remove_embedded_amounts(description)
    result: dict[str, Any] = {
        "date": normalize_date(date_cell.text) if date_cell else None,
        "description": description,
        "numbers": numeric,
    }
    if numeric or embedded:
        if numeric:
            result["balance"] = numeric[-1][1] if (len(numeric) >= 2 or embedded) else None
        mutation_value = embedded[-1][1] if embedded else (
            numeric[-2][1] if len(numeric) >= 2 else numeric[0][1]
        )
        mutation_text = (
            embedded[-1][0]
            if embedded
            else numeric[-2][0].text if len(numeric) >= 2 else numeric[0][0].text
        ).upper()
        semantic_text = f"{mutation_text} {description.upper()}"
        amount = abs(mutation_value)
        is_debit = bool(re.search(r"\b(DB|DEBIT|DEBET|DR)\b", semantic_text))
        is_credit = bool(re.search(r"\b(CR|CREDIT|KREDIT)\b", semantic_text))
        if is_debit:
            result["debit"], result["credit"] = amount, 0.0
        elif is_credit or amount > 0:
            result["debit"], result["credit"] = 0.0, amount
        else:
            result["debit"], result["credit"] = 0.0, 0.0
        result["amount"] = amount
    return result


def _table_model_rows(page: fitz.Page, dpi: int) -> list[list[str]]:
    from table_model import recognize_table_rows

    return recognize_table_rows(_render_page(page, min(dpi, 180)))


def _rows_from_table_model(table_rows: Sequence[Sequence[str]], page_number: int) -> list[ExtractedRow]:
    extracted: list[ExtractedRow] = []
    for values in table_rows:
        cells = [
            Cell(text=_clean_text(value), x0=index, y0=0, x1=index + 1, y1=1, confidence=0.75)
            for index, value in enumerate(values) if _clean_text(value)
        ]
        if not cells or _is_header([Word(cell.text, cell.x0, 0, cell.x1, 1) for cell in cells]):
            continue
        words = [Word(cell.text, cell.x0, 0, cell.x1, 1, confidence=cell.confidence) for cell in cells]
        if not _is_transaction_row(words):
            continue
        classified = _classify_cells(cells)
        extracted.append(ExtractedRow(
            page=page_number,
            cells=cells,
            raw_text=_clean_text(" ".join(cell.text for cell in cells)),
            date=classified["date"],
            description=classified["description"],
            debit=classified.get("debit"),
            credit=classified.get("credit"),
            amount=classified.get("amount"),
            balance=classified.get("balance"),
            confidence=0.75,
        ))
    return extracted


def _category(description: str, debit: float | None, credit: float | None) -> str:
    text = description.upper()
    if "SALDO AWAL" in text:
        return "Transfer in"
    if "TARIKAN TUNAI" in text:
        return "Cash withdrawal"
    if any(term in text for term in ("BIAYA", "ADM", "ADMIN", "FEE", "PAJAK", "BUNGA")):
        return "Bank fee"
    if "CR" in text:
        return "Revenue"
    if "DB" in text:
        return "Transfer Out"
    if (credit or 0) > 0:
        return "Revenue"
    return "Uncategorized"


def extract_statement(
    pdf_path: str | Path,
    dpi: int = 220,
    use_table_model: bool = False,
) -> dict[str, Any]:
    document = fitz.open(str(pdf_path))
    rows: list[ExtractedRow] = []
    pages: list[dict[str, Any]] = []
    for page_number, page in enumerate(document, start=1):
        words, is_digital = _page_words(page, dpi=dpi)
        grouped = _group_rows(words)
        page_rows: list[ExtractedRow] = []
        table_model_used = False
        if use_table_model:
            table_rows = _table_model_rows(page, dpi)
            page_rows = _rows_from_table_model(table_rows, page_number)
            table_model_used = bool(page_rows)
            if page_rows:
                rows.extend(page_rows)
        for grouped_row in grouped:
            if table_model_used:
                break
            if _is_header(grouped_row):
                continue
            if not _is_transaction_row(grouped_row):
                continue
            cells = _row_to_cells(grouped_row)
            classified = _classify_cells(cells)
            row = ExtractedRow(
                page=page_number,
                cells=cells,
                raw_text=_clean_text(" ".join(word.text for word in grouped_row)),
                date=classified["date"],
                description=classified["description"],
                debit=classified.get("debit"),
                credit=classified.get("credit"),
                amount=classified.get("amount"),
                balance=classified.get("balance"),
                confidence=float(np.mean([cell.confidence for cell in cells])),
            )
            page_rows.append(row)
            rows.append(row)
        pages.append({
            "page": page_number,
            "digital": is_digital,
            "word_count": len(words),
            "rows": len(page_rows),
            "table_model": "paddleocr" if table_model_used else "coordinate-fallback",
        })
    _validate_balances(rows)
    return {"pages": pages, "rows": rows}


def _validate_balances(rows: list[ExtractedRow]) -> None:
    previous: float | None = None
    for row in rows:
        if row.balance is not None and previous is not None:
            change = (row.credit or 0) - (row.debit or 0)
            if row.amount is not None and row.debit is None and row.credit is None:
                change = row.amount
            if abs((previous + change) - row.balance) > 0.02:
                row.validation.append("balance_check_failed")
        if row.balance is not None:
            previous = row.balance
        if row.date is None:
            row.validation.append("date_missing")
        if not row.description:
            row.validation.append("description_missing")
        if row.confidence < 0.75:
            row.validation.append("low_ocr_confidence")


def rows_dataframe(result: dict[str, Any]) -> pd.DataFrame:
    records = []
    for row in result["rows"]:
        records.append({
            "Tanggal": row.date or "",
            "Kredit": row.credit,
            "Debit": row.debit,
            "Saldo": row.balance,
            "Deskripsi": row.description,
            "Kategori Transaksi": _category(row.description, row.debit, row.credit),
        })
    return pd.DataFrame(records)


def review_dataframe(result: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Halaman": row.page,
            "Confidence": round(row.confidence, 3),
            "Human review status": "Needs review" if row.validation else "Pending review",
            "Validation flags": ", ".join(row.validation),
            "Full detected text": row.raw_text,
        }
        for row in result["rows"]
    ])


def excel_bytes(result: dict[str, Any]) -> bytes:
    output = io.BytesIO()
    detail_dataframe = rows_dataframe(result)
    if not detail_dataframe.empty:
        detail_dataframe["Tanggal"] = detail_dataframe["Tanggal"].apply(
            lambda value: _excel_date_value(value) if value else value
        )
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        detail_dataframe.to_excel(
            writer, sheet_name="Detail transaksi rekening - 1", index=False
        )
        review_dataframe(result).to_excel(writer, sheet_name="Review", index=False)
        detail_sheet = writer.sheets["Detail transaksi rekening - 1"]
        detail_sheet.freeze_panes = "A2"
        for column, width in {"A": 14, "B": 16, "C": 16, "D": 16, "E": 80, "F": 22}.items():
            detail_sheet.column_dimensions[column].width = width
        review_sheet = writer.sheets["Review"]
        review_sheet.freeze_panes = "A2"
        for column, width in {"A": 12, "B": 14, "C": 22, "D": 28, "E": 100}.items():
            review_sheet.column_dimensions[column].width = width
        for cell in detail_sheet["A"][1:]:
            if isinstance(cell.value, datetime):
                cell.number_format = "dd/mm" if cell.value.year == 1900 else "dd/mm/yyyy"
    return output.getvalue()


def jsonable(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "pages": result["pages"],
        "rows": [
            {
                **{key: value for key, value in asdict(row).items() if key != "cells"},
                "cells": [asdict(cell) for cell in row.cells],
            }
            for row in result["rows"]
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Indonesian bank statement transactions.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument(
        "--table-model",
        action="store_true",
        help="Use optional PaddleOCR table recognition before coordinate fallback.",
    )
    args = parser.parse_args()
    result = extract_statement(args.pdf, dpi=args.dpi, use_table_model=args.table_model)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "transactions.json").write_text(
        json.dumps(jsonable(result), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output / "transactions.xlsx").write_bytes(excel_bytes(result))
    print(f"Extracted {len(result['rows'])} transaction rows to {args.output}")


if __name__ == "__main__":
    main()
