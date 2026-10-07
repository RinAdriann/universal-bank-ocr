"""Universal Indonesian Bank Statement Extractor (PDF/Image -> Excel)."""
from __future__ import annotations

import argparse
import io
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np
import pandas as pd
import pymupdf as fitz

SUPPORTED_INPUT_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

# --------------------------------------------------------------------------- constants

MONTHS = {
    "jan": 1, "januari": 1, "january": 1, "feb": 2, "pebruari": 2, "februari": 2,
    "mar": 3, "maret": 3, "apr": 4, "april": 4, "mei": 5, "may": 5, "jun": 6, "juni": 6,
    "jul": 7, "juli": 7, "agu": 8, "agt": 8, "agustus": 8, "aug": 8, "sep": 9,
    "september": 9, "okt": 10, "oktober": 10, "oct": 10, "nov": 11, "nopember": 11,
    "november": 11, "des": 12, "desember": 12, "dec": 12
}
_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))

DATE_RE = re.compile(rf"^(?P<d>\d{{1,2}})[-\s./]*(?P<mon>{_MONTH_ALT}|\d{{1,2}})(?:[-\s./]*(?P<y>\d{{4}}|\d{{2}}))?(?!\d)", re.IGNORECASE)

OPENING_RE = re.compile(r"\b(SALDO\s+AWAL|SAK\s+AWAL|OPENING\s+BALANCE|BEGINNING\s+BALANCE|SALDO\s+SEBELUMNYA)\b", re.IGNORECASE)
STOP_RE = re.compile(r"\b(SALDO\s+AKHIR|CLOSING\s+BALANCE|TOTAL|JUMLAH\s+MUTASI|HALAMAN|BERSAMBUNG|CONTINUED)\b", re.IGNORECASE)

DEBIT_HINT_RE = re.compile(r"\b(TARIK|TARIKAN|BIAYA|ADM|PAJAK|PEMBAYARAN|BAYAR|PEMBELIAN|DEBIT|DB)\b")
CREDIT_HINT_RE = re.compile(r"\b(SETOR|SETORAN|BUNGA|JASA GIRO|KREDIT|GAJI|TRSF DARI|TRF DARI|CR)\b")

CATEGORY_RULES = [
    ("Starting Balance", r"SALDO AWAL|OPENING BALANCE|SAK AWAL"),
    ("Bank Fee", r"BIAYA|ADM|ADMIN|FEE|PAJAK|TAX|PROVISI|MATERAI|CHARGE"),
    ("Interest", r"BUNGA|INTEREST|JASA GIRO"),
    ("Cash Withdrawal", r"TARIKAN|TARIK TUNAI|CASH WITHDRAWAL|ATM"),
    ("Cash Deposit", r"SETORAN|SETOR TUNAI|CASH DEPOSIT"),
    ("Payment/Purchase", r"PEMBAYARAN|PAYMENT|BELANJA|PURCHASE|QRIS|TOKPED|SHOPEE|GOPAY|OVO|DANA"),
    ("Transfer Out", r"TRANSFER KELUAR|TRSF OUT|TRSF KE|TRF KE|REMITTANCE OUT|SWITCHING"),
    ("Transfer In", r"TRANSFER MASUK|TRSF IN|TRSF DARI|TRF DARI|INCOMING"),
]
_CATEGORY_RES = [(name, re.compile(rf"\b(?:{pattern})\b")) for name, pattern in CATEGORY_RULES]

DIRECTION_MARKERS = {"DB": "debit", "DR": "debit", "D": "debit", "DEBIT": "debit", "DEBET": "debit", "CR": "credit", "C": "credit", "K": "credit"}

# --------------------------------------------------------------------------- data classes

@dataclass
class ExtractedRow:
    page: int
    raw_text: str
    date: date | str | None = None
    description: str = ""
    debit: float | None = None
    credit: float | None = None
    balance: float | None = None
    pending_mutasi: float | None = None
    is_opening: bool = False
    validation: list[str] = field(default_factory=list)
    date_parts: tuple | None = field(default=None, repr=False)

@dataclass(frozen=True)
class Amount:
    value: float
    direction: str | None

# --------------------------------------------------------------------------- parsing logic

def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).replace("\u00a0", " ")).strip()

def _match_date(text: str) -> tuple[tuple[int, int, int | None], int] | None:
    match = DATE_RE.match(text.strip())
    if not match: return None
    groups = match.groupdict()
    day = int(groups["d"])
    month = MONTHS.get(groups["mon"].lower()) or int(groups["mon"])
    year = int(groups["y"]) if groups.get("y") else None
    if year is not None and year < 100: year += 2000
    if 1 <= day <= 31 and 1 <= month <= 12:
        return (day, month, year), match.end()
    return None

def parse_amount(text: str) -> Amount | None:
    if not text: return None
    cleaned = re.sub(r"\s+", "", str(text).upper().replace("RP", "").replace("IDR", ""))
    match = re.search(r"([+-]?\d[\d.,]*)(DB|DR|D|CR|C|K)?", cleaned)
    if not match: return None
    
    num_str, mark = match.groups()
    if len(re.sub(r"[^0-9]", "", num_str)) == 0: return None
    
    has_dot, has_comma = "." in num_str, "," in num_str
    if has_dot and has_comma:
        last_dot, last_comma = num_str.rfind("."), num_str.rfind(",")
        decimal_sep = "," if last_comma > last_dot else "."
        num_str = num_str.replace("." if decimal_sep == "," else ",", "").replace(decimal_sep, ".")
    elif has_comma:
        parts = num_str.split(",")
        if len(parts) > 2 or len(parts[-1]) == 3: num_str = num_str.replace(",", "")
        else: num_str = num_str.replace(",", ".")
    elif has_dot:
        parts = num_str.split(".")
        if len(parts) > 2 or len(parts[-1]) == 3: num_str = num_str.replace(".", "")
    
    try: value = float(num_str)
    except ValueError: return None
    
    direction = "debit" if mark in ["DB", "DR", "D"] else "credit" if mark in ["CR", "C", "K"] else None
    if "-" in cleaned or "(" in cleaned: direction = "debit"
    elif "+" in cleaned: direction = "credit"
    
    return Amount(value, direction)

def _hint_direction(description: str) -> str | None:
    text = description.upper()
    debit, credit = bool(DEBIT_HINT_RE.search(text)), bool(CREDIT_HINT_RE.search(text))
    return "debit" if debit and not credit else "credit" if credit and not debit else None

def _merge_string_markers(cells: list[str]) -> list[str]:
    """Glues trailing 'DB'/'CR' to numbers without deleting the cell (prevents column shifting)."""
    merged = list(cells)
    for i in range(1, len(merged)):
        cell = merged[i]
        if re.sub(r"[^A-Z]", "", cell.upper()) in DIRECTION_MARKERS and len(cell) <= 6:
            if parse_amount(merged[i-1]):
                merged[i-1] = f"{merged[i-1]} {cell.upper()}"
                merged[i] = "" 
    return merged

# --------------------------------------------------------------------------- visual & structural mappings

@lru_cache(maxsize=1)
def _rapidocr_engine() -> Any:
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()

def _preprocess(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    scale = 1000 / max(gray.shape)
    small = cv2.resize(gray, None, fx=scale, fy=scale) if scale < 1 else gray
    _, bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = bw.shape
    score = lambda angle: float(np.var(cv2.warpAffine(bw, cv2.getRotationMatrix2D((w/2, h/2), angle, 1.0), (w, h), flags=cv2.INTER_NEAREST).sum(axis=1)))
    coarse = max(np.arange(-5, 5.01, 0.5), key=score)
    angle = float(max(np.arange(coarse - 0.5, coarse + 0.51, 0.1), key=score))
    if abs(angle) >= 0.3:
        gray = cv2.warpAffine(gray, cv2.getRotationMatrix2D((gray.shape[1]/2, gray.shape[0]/2), angle, 1.0), (gray.shape[1], gray.shape[0]), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

def _create_synthetic_pdf(image: np.ndarray) -> fitz.Document:
    result, _ = _rapidocr_engine()(image)
    doc = fitz.open()
    page = doc.new_page(width=image.shape[1], height=image.shape[0])
    for box, text, _ in result or []:
        text = _clean_text(str(text))
        if not text: continue
        pts = np.asarray(box, dtype=float)
        x0, y0, y1 = pts[:, 0].min(), pts[:, 1].min(), pts[:, 1].max()
        page.insert_text((x0, y1 - (y1-y0)*0.2), text, fontsize=max(4, (y1 - y0) * 0.8))
    return doc

def _discover_dynamic_columns(tokens: list[dict]) -> dict:
    numeric_tokens = [t for t in tokens if parse_amount(t["text"])]
    
    x_coords = sorted([t["x1"] for t in numeric_tokens])
    
    clusters = []
    for x in x_coords:
        if not clusters or x - clusters[-1][-1] > 30:
            clusters.append([x])
        else:
            clusters[-1].append(x)
            
    col_centers = [sum(c)/len(c) for c in clusters]
    
    # The right-most cluster is ALWAYS the Balance.
    # The one before it is Credit/Debit/Mutasi.
    if not col_centers:
        return {} 
        
    balance_max_x = col_centers[-1] + 20
    mutation_max_x = col_centers[-2] + 20 if len(col_centers) >= 2 else balance_max_x - 100
    
    return {
        "date_min": 0, "date_max": 100,             # Dates are always far left
        "desc_min": 100, "desc_max": mutation_max_x - 60, # Description takes the middle space
        "mut_min": mutation_max_x - 60, "mut_max": mutation_max_x,
        "bal_min": mutation_max_x, "bal_max": balance_max_x
    }

# --------------------------------------------------------------------------- transaction assembly

def _extract_page_tokens(page: fitz.Page) -> list[dict]:
    # get_text("words") returns: (x0, y0, x1, y1, text, block_no, line_no, word_no)
    words = page.get_text("words")
    tokens = []
    for w in words:
        tokens.append({
            "text": w[4],
            "x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3]
        })
    
    # Sort: Y first (with ~4px tolerance for slight skews), then X
    tokens.sort(key=lambda t: (t["y0"] // 4, t["x0"]))
    return tokens

def _validate_balances(rows: list[ExtractedRow]) -> None:
    """Forward sweep to deduce missing Mutasi directions and calculate dropped Saldos."""
    dates = [r.date for r in rows if isinstance(r.date, date)]
    chronological = rows[::-1] if len(dates) >= 2 and dates[0] > dates[-1] else rows
    
    prev_balance = None
    for row in chronological:
        if row.is_opening and row.balance is not None:
            prev_balance = row.balance
            break
        elif row.balance is not None:
            prev_balance = row.balance - (row.credit or 0.0) + (row.debit or 0.0)
            break

    for row in chronological:
        if row.pending_mutasi is not None:
            if row.balance is not None and prev_balance is not None:
                delta = row.balance - prev_balance
                if abs(delta + row.pending_mutasi) < 0.02: row.debit, row.credit = row.pending_mutasi, None
                elif abs(delta - row.pending_mutasi) < 0.02: row.debit, row.credit = None, row.pending_mutasi
                else: 
                    dir_hint = _hint_direction(row.description)
                    if dir_hint == "debit": row.debit = row.pending_mutasi
                    elif dir_hint == "credit": row.credit = row.pending_mutasi
                    else: row.debit = row.pending_mutasi
            else:
                dir_hint = _hint_direction(row.description)
                if dir_hint == "debit": row.debit = row.pending_mutasi
                elif dir_hint == "credit": row.credit = row.pending_mutasi
                else: row.debit = row.pending_mutasi
                
        if row.balance is None and prev_balance is not None and not row.is_opening:
            row.balance = prev_balance + (row.credit or 0.0) - (row.debit or 0.0)
            row.validation.append("saldo_calculated")
            
        if row.balance is not None: prev_balance = row.balance

def _apply_dates(rows: Sequence[ExtractedRow], year: int | None) -> None:
    for row in rows:
        if row.date_parts is None: continue
        day, month, row_year = row.date_parts
        row_year = row_year or year
        if row_year is None: row.date = f"{day:02d}/{month:02d}"
        else:
            try: row.date = date(row_year, month, day)
            except ValueError: row.validation.append("date_invalid")

# --------------------------------------------------------------------------- Input & Output
def _assemble_transactions(tokens: list[dict], cols: dict) -> list[ExtractedRow]:
    rows = []
    current_tx = None
    
    for t in tokens:
        x_center = (t["x0"] + t["x1"]) / 2
        text = t["text"].strip()
        
        # Check if token is in Date column AND matches Date regex
        if cols["date_min"] <= x_center <= cols["date_max"]:
            date_match = _match_date(text)
            if date_match:
                # Anchor found! Save previous transaction
                if current_tx:
                    rows.append(current_tx)
                
                # Start new transaction unit
                current_tx = ExtractedRow(
                    page=0, raw_text="", description="", 
                    date_parts=date_match[0]
                )
                continue
                
        if not current_tx:
            continue
            
        # Route tokens to the correct bucket based on X-coordinate
        if cols["desc_min"] <= x_center <= cols["desc_max"]:
            current_tx.description += f" {text}"
        elif cols["mut_min"] <= x_center <= cols["mut_max"]:
            amt = parse_amount(text)
            if amt:
                # Temporarily store in pending_mutasi; let your _validate_balances handle DB/CR logic
                current_tx.pending_mutasi = amt.value 
        elif cols["bal_min"] <= x_center <= cols["bal_max"]:
            amt = parse_amount(text)
            if amt:
                current_tx.balance = amt.value
                
    if current_tx:
        rows.append(current_tx)
        
    return rows

def extract_statement(pdf_path: str | Path, dpi: int = 220) -> dict[str, Any]:
    input_path = Path(pdf_path)
    if input_path.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
        raise ValueError(f"Unsupported file format. Needs: {', '.join(SUPPORTED_INPUT_SUFFIXES)}")

    rows, pages, all_words = [], [], []
    is_image = input_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
    doc = _create_synthetic_pdf(_preprocess(cv2.imread(str(input_path)))) if is_image else fitz.open(str(input_path))
        
    try:
        for page_number, page in enumerate(doc, start=1):
            if not is_image and len(page.get_text().split()) < 8:
                syn_doc = _create_synthetic_pdf(_preprocess(cv2.cvtColor(np.frombuffer(page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), alpha=False).samples, dtype=np.uint8).reshape(page.rect.height, page.rect.width, 3), cv2.COLOR_RGB2BGR)))
                usable_page = syn_doc[0]
            else:
                usable_page, syn_doc = page, None
                
            # --- NEW PIPELINE EXECUTION ---
            tokens = _extract_page_tokens(usable_page)
            cols = _discover_dynamic_columns(tokens)
            
            if cols: # Only assemble if we successfully found columns
                page_rows = _assemble_transactions(tokens, cols)
                for r in page_rows: 
                    r.page = page_number
                rows.extend(page_rows)
                pages.append({"page": page_number, "rows": len(page_rows)})
            else:
                pages.append({"page": page_number, "rows": 0})
                
            all_words.extend(usable_page.get_text("words"))
            if syn_doc: syn_doc.close()
    finally:
        doc.close()

    explicit_years = [r.date_parts[2] for r in rows if r.date_parts and r.date_parts[2]]
    if not explicit_years: explicit_years = [int(m.group(1)) for w in all_words for m in re.finditer(r"(?<!\d)(20\d{2})(?!\d)", w[4])]
    explicit_years = [y for y in explicit_years if 2000 <= y <= 2100]
    year = Counter(explicit_years).most_common(1)[0][0] if explicit_years else None

    _apply_dates(rows, year)
    _validate_balances(rows)
    return {"pages": pages, "rows": rows, "document_year": year}

def _category(description: str, debit: float | None, credit: float | None) -> str:
    if not description: return "Uncategorized"
    for name, pattern in _CATEGORY_RES:
        if pattern.search(description.upper()): return name
    return "Transfer Out / Debit" if (debit or 0) > 0 else "Transfer In / Credit" if (credit or 0) > 0 else "Uncategorized"

def excel_bytes(result: dict[str, Any]) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame([{
            "Tanggal": row.date if row.date is not None else "",
            "Kredit": row.credit,
            "Debit": row.debit,
            "Saldo": row.balance,
            "Deskripsi": row.description,
            "Kategori Transaksi": _category(row.description, row.debit, row.credit),
        } for row in result["rows"]]).to_excel(writer, sheet_name="Detail transaksi rekening - 1", index=False)
        
        pd.DataFrame([{
            "Halaman": row.page, "Human review status": "Needs review" if row.validation else "Pending review",
            "Validation flags": ", ".join(row.validation), "Full detected text": row.raw_text,
        } for row in result["rows"]]).to_excel(writer, sheet_name="Review", index=False)
        
        for name in writer.sheets:
            sheet = writer.sheets[name]
            sheet.freeze_panes = "A2"
            for col, w in zip(["A", "B", "C", "D", "E", "F"], [14, 16, 16, 16, 80, 22] if name == "Detail transaksi rekening - 1" else [12, 18, 28, 100]): sheet.column_dimensions[col].width = w
            if name == "Detail transaksi rekening - 1":
                for r in sheet.iter_rows(min_row=2):
                    if isinstance(r[0].value, (date, datetime)): r[0].number_format = "dd/mm/yyyy"
                    for c in r[1:4]: c.number_format = "#,##0.00"
    return output.getvalue()

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Indonesian bank statements to Excel.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--output", type=Path, default=Path("output"))
    args = parser.parse_args()
    
    result = extract_statement(args.file)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "transactions.xlsx").write_bytes(excel_bytes(result))
    print(f"Extracted {len(result['rows'])} transaction rows to {args.output}")

if __name__ == "__main__":
    main()