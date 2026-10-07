"""Universal Indonesian Bank Statement Extractor engine (PDF/Image -> Excel).

Design (no bank-specific templates):

1.  Get word boxes: PyMuPDF text layer -> OCR when a page has no text layer.
2.  Group words into lines, lines into *cells* (words separated by a wide gap).
3.  Find the table header by vocabulary (tanggal/keterangan/debet/kredit/jumlah/saldo ...,
    Indonesian + English, fuzzy for OCR noise).  Pages without a header reuse the previous one.
4.  Learn the columns from the DATA itself: money cells are clustered by x-position
    (right, left or centre aligned, whichever is tightest) and named from the header
    (or inferred: right-most = balance).  Text columns are learned the same way.
5.  A line whose left part is a date starts a transaction; following lines belong to it
    until the next date, a summary/footer line, or a large vertical gap.
6.  Running-balance reconciliation fixes debit/credit direction, detects newest-first
    ordering, and flags rows that do not add up.
"""
from __future__ import annotations

import argparse
import io
import itertools
import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from difflib import get_close_matches
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Sequence

import numpy as np
import pandas as pd

try:  # optional at import time so the text-layer path works without OpenCV
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    try:
        import fitz  # type: ignore
    except ImportError:
        fitz = None

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
SUPPORTED_INPUT_SUFFIXES = {".pdf"} | IMAGE_SUFFIXES

# --------------------------------------------------------------------------- constants

MONTHS = {
    "jan": 1, "januari": 1, "january": 1, "feb": 2, "pebruari": 2, "februari": 2, "february": 2,
    "mar": 3, "maret": 3, "march": 3, "apr": 4, "april": 4, "mei": 5, "may": 5,
    "jun": 6, "juni": 6, "june": 6, "jul": 7, "juli": 7, "july": 7,
    "agu": 8, "agt": 8, "agustus": 8, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "okt": 10, "oktober": 10, "oct": 10, "october": 10, "nov": 11, "nopember": 11, "november": 11,
    "des": 12, "desember": 12, "dec": 12, "december": 12,
}
_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))

_TIME_RE = re.compile(r"^\s*\d{1,2}[:.]\d{2}(?::\d{2})?(?:\s*(?:WIB|WITA|WIT))?\b", re.IGNORECASE)
_PURE_TIME_RE = re.compile(r"^\d{1,2}[:.]\d{2}(?::\d{2})?(?:\s*(?:WIB|WITA|WIT))?$", re.IGNORECASE)
_DATE_PATTERNS = [
    ("num", re.compile(r"^(\d{1,2})([/\-.])(\d{1,2})(?:\2(\d{4}|\d{2}))?(?![\d,])")),
    ("mon", re.compile(rf"^(\d{{1,2}})[\s\-./]*({_MONTH_ALT})(?![A-Za-z])\.?(?:[\s\-./,]*(\d{{4}}|\d{{2}}))?(?!\d)", re.IGNORECASE)),
    ("iso", re.compile(r"^(\d{4})([/\-.])(\d{1,2})\2(\d{1,2})(?!\d)")),
]

OPENING_RE = re.compile(r"^\W*(SALDO\s+AWAL|SAK\s+AWAL|OPENING\s+BALANCE|BEGINNING\s+BALANCE|SALDO\s+SEBELUMNYA|BALANCE\s+B/?F|BROUGHT\s+FORWARD)\b", re.IGNORECASE)
STOP_RE = re.compile(
    r"^\W*(TOTAL\b|JUMLAH\s+(?:MUTASI|DEBET|DEBIT|KREDIT|CREDIT)|SALDO\s+AKHIR|CLOSING\s+BALANCE|ENDING\s+BALANCE|"
    r"SALDO\s+AWAL|OPENING\s+BALANCE|SALDO\s+REKENING|HALAMAN\b|PAGE\b|BERSAMBUNG|CONTINUED|CREATED\s+BY|TERBILANG|"
    r"APABILA|DICETAK|PRINTED|MUTASI\s+(?:DEBET|DEBIT|KREDIT|CREDIT)|\*+\s*(?:AKHIR|END))",
    re.IGNORECASE,
)

DEBIT_HINT_RE = re.compile(r"\b(TARIK|TARIKAN|PENARIKAN|WITHDRAWAL|OUTGOING|BIAYA|ADM|ADMIN|FEE|PAJAK|PEMBAYARAN|BAYAR|PEMBELIAN|TRANSAKSI|DEBIT|DB|TRANSFER KE|TRF KE|TRSF KE|TRF|TRSF|KE)\b")
CREDIT_HINT_RE = re.compile(r"\b(SETOR|SETORAN|BUNGA|INTEREST|JASA GIRO|BAGI HASIL|KREDIT|CREDIT|KR|CR|INCOMING|DEPOSIT|RECEIVED|REFUND|GAJI|SALARY|TRANSFER DARI|TRSF DARI|TRF DARI|DARI)\b")

CATEGORY_RULES = [
    ("Starting Balance", r"SALDO AWAL|OPENING BALANCE|SAK AWAL"),
    ("Bank Fee", r"BIAYA|ADM|ADMIN|FEE|PAJAK|TAX|PROVISI|MATERAI|CHARGE"),
    ("Interest", r"BUNGA|INTEREST|JASA GIRO|BAGI HASIL"),
    ("Cash Withdrawal", r"TARIKAN|TARIK TUNAI|TARIK|PENARIKAN TUNAI|CASH WITHDRAWAL|ATM"),
    ("Cash Deposit", r"SETORAN|SETOR TUNAI|CASH DEPOSIT"),
    ("Salary", r"SALARY|GAJI|PAYROLL"),
    ("Payment/Purchase", r"PEMBAYARAN|PEMBELIAN|PAYMENT|BELANJA|PURCHASE|QRIS|TOKPED|SHOPEE|GOPAY|OVO|DANA|PLN|PULSA|TOKEN"),
    ("Transfer Out", r"TRANSFER KELUAR|TRANSFER KE|TRSF OUT|TRSF KE|TRF KE|REMITTANCE OUT|SWITCHING"),
    ("Transfer In", r"TRANSFER MASUK|TRANSFER DARI|TRSF IN|TRSF DARI|TRF DARI|INCOMING"),
]
_CATEGORY_RES = [(name, re.compile(rf"\b(?:{pattern})\b")) for name, pattern in CATEGORY_RULES]

# header vocabulary: role -> keywords (first matching role wins, so order matters)
ROLE_KEYS: list[tuple[str, set[str]]] = [
    ("balance", {"saldo", "balance", "saldoakhir"}),
    ("debit", {"debet", "debit", "db", "dr", "keluar", "pengeluaran", "withdrawal", "withdrawals", "tarik"}),
    ("credit", {"kredit", "credit", "cr", "masuk", "pemasukan", "deposit", "deposits", "setor"}),
    ("desc", {"keterangan", "uraian", "deskripsi", "description", "remark", "remarks", "berita", "narasi", "details", "detail", "particulars", "transaksi:"}),
    ("date", {"tanggal", "tgl", "date", "waktu", "tanggal/waktu"}),
    ("amount", {"jumlah", "mutasi", "nominal", "amount", "nilai"}),
]
NUMERIC_ROLES = {"balance", "debit", "credit", "amount"}
_ALL_ROLE_WORDS = sorted({k for _, ks in ROLE_KEYS for k in ks if len(k) >= 5})

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
    reference: str = ""
    extra: str = ""
    confidence: float = 1.0
    pending_dir: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Amount:
    value: float
    direction: str | None


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return max(self.y1 - self.y0, 1e-6)


@dataclass
class Cell:
    text: str
    x0: float
    x1: float
    num_x1: float
    words: list[Word]
    money: bool = False
    role: str | None = None  # numeric role assigned by layout
    is_int: bool = False

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Line:
    cells: list[Cell]
    y: float

    @property
    def text(self) -> str:
        return " ".join(c.text for c in self.cells)


# --------------------------------------------------------------------------- amount & date parsing


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).replace(" ", " ")).strip()


def _norm_money(text: str) -> str:
    s = re.sub(r"\s+", "", str(text).upper())
    return s.replace("RP.", "").replace("RP", "").replace("IDR", "")


_MONEY_RE = re.compile(r"^[+\-]?\(?(?:\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+[.,]\d{1,2})\)?(?:DB|DR|CR|D|K|C|[+\-])?$")
_INT_RE = re.compile(r"^[+\-]?\d{1,13}(?:DB|DR|CR|D|K|C)?$")


def _is_money(text: str) -> bool:
    return bool(_MONEY_RE.match(_norm_money(text)))


def _is_int_amount(text: str) -> bool:
    return bool(_INT_RE.match(_norm_money(text)))


def parse_amount(text: str) -> Amount | None:
    """Parse '1.234.567,89', '1,234,567.89', '-7.000', '+147.115', '500.00 DB', '(1,000.00)'."""
    if not text:
        return None
    s = _norm_money(text).strip(".,;:")
    # OCR often inserts spaces into a long amount or turns a decimal point
    # into a stray character. Keep only amount characters before matching.
    s = re.sub(r"(?<=\d)\s+(?=[\d.,])", "", s)
    m = re.fullmatch(r"([+\-]?)(\()?(\d[\d.,]*)(\))?(DB|DR|CR|D|K|C|[+\-])?", s)
    if not m:
        return None
    sign, lparen, num, _rparen, suffix = m.groups()
    if re.search(r"[.,]$", num):
        num = num[:-1]
    has_dot, has_comma = "." in num, "," in num
    if has_dot and has_comma:
        decimal = "," if num.rfind(",") > num.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        num = num.replace(thousands, "").replace(decimal, ".")
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = num.split(sep)
        if len(parts) > 2:
            if len(parts[-1]) <= 2:
                num = "".join(parts[:-1]) + "." + parts[-1]
            else:
                num = num.replace(sep, "")
        elif len(parts[-1]) == 3 and parts[0] not in ("0", "00") and len(parts[0]) <= 3:
            num = num.replace(sep, "")
        else:
            num = num.replace(sep, ".")
    try:
        value = float(num)
    except ValueError:
        return None
    direction = None
    if sign == "-" or lparen or suffix in ("DB", "DR", "D", "-"):
        direction = "debit"
    elif sign == "+" or suffix in ("CR", "C", "K", "+"):
        direction = "credit"
    return Amount(value, direction)


def _match_date(text: str) -> tuple[tuple[int, int, int | None], int] | None:
    """Match a date at the start of *text*. Returns ((day, month, year|None), end_index incl. time)."""
    text = text.strip()
    for kind, pattern in _DATE_PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        if kind == "num":
            d, sep, mo, y = m.groups()
            if y is None and sep == ".":
                continue
            day, month, year = int(d), int(mo), (int(y) if y else None)
        elif kind == "mon":
            d, mon, y = m.groups()
            day, month, year = int(d), MONTHS[mon.lower()], (int(y) if y else None)
        else:
            y, _sep, mo, d = m.groups()
            day, month, year = int(d), int(mo), int(y)
        if year is not None and year < 100:
            year += 2000
        if not (1 <= day <= 31 and 1 <= month <= 12):
            continue
        end = m.end()
        t = _TIME_RE.match(text[end:])
        if t:
            end += t.end()
        return (day, month, year), end
    return None


def _hint_direction(description: str) -> str | None:
    text = description.upper()
    debit, credit = bool(DEBIT_HINT_RE.search(text)), bool(CREDIT_HINT_RE.search(text))
    return "debit" if debit and not credit else "credit" if credit and not debit else None


def _description_direction(description: str) -> str | None:
    """Use transaction narrative markers as the primary flow signal."""
    text = description.upper()
    if re.search(r"\b(KR|CR|KREDIT|CREDIT)\b", text):
        return "credit"
    if re.search(r"\b(TARIK|TARIKAN|PENARIKAN|TRANSAKSI|DEBIT|DB|TRSF|TRF)\b", text):
        return "debit"
    return None


def _delta_direction(previous: float | None, balance: float | None, amount: float | None) -> str | None:
    """Infer money flow from the change in running balance."""
    if previous is None or balance is None or amount is None:
        return None
    delta = balance - previous
    if abs(delta - amount) < _TOL:
        return "credit"
    if abs(delta + amount) < _TOL:
        return "debit"
    return None


def _choose_direction(
    previous: float | None,
    balance: float | None,
    amount: float,
    explicit: str | None,
    description: str,
) -> tuple[str | None, str]:
    """Resolve a combined mutation column and report the evidence used."""
    by_balance = _delta_direction(previous, balance, amount)
    by_hint = _hint_direction(description)
    if explicit:
        return explicit, "marker"
    if by_balance:
        return by_balance, "balance_delta"
    if by_hint:
        return by_hint, "description"
    return None, "unknown"


def _marker_direction(text: str) -> str | None:
    """Return a direction from a standalone bank statement marker."""
    tokens = re.findall(r"[A-Z]+", text.upper())
    if any(token in {"DB", "DR", "D", "DEBIT", "DEBET", "OUT", "KELUAR"} for token in tokens):
        return "debit"
    if any(token in {"CR", "C", "K", "CREDIT", "KREDIT", "IN", "MASUK"} for token in tokens):
        return "credit"
    return None


def _line_direction(cells: Sequence[Cell]) -> str | None:
    """Find D/K or DB/CR markers without treating ordinary descriptions as markers."""
    for cell in cells:
        if cell.money:
            amount = parse_amount(cell.text)
            if amount and amount.direction:
                return amount.direction
        direction = _marker_direction(cell.text)
        if direction:
            return direction
    return None


# --------------------------------------------------------------------------- word sources


def _words_pymupdf(page: Any) -> list[Word]:
    """Horizontal words from the text layer (rotated watermarks/stamps are dropped)."""
    words: list[Word] = []
    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            dx, dy = line.get("dir", (1.0, 0.0))
            if abs(dy) > 0.1 or dx < 0.9:
                continue
            cur: list[tuple[str, float, float, float, float]] = []

            def flush() -> None:
                if cur:
                    words.append(Word("".join(c[0] for c in cur), min(c[1] for c in cur),
                                      min(c[2] for c in cur), max(c[3] for c in cur), max(c[4] for c in cur)))
                    cur.clear()

            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    c = ch["c"]
                    x0, y0, x1, y1 = ch["bbox"]
                    if c.isspace():
                        flush()
                        continue
                    if cur and x0 - cur[-1][3] > 0.25 * max(y1 - y0, 1.0):
                        flush()
                    cur.append((c, x0, y0, x1, y1))
            flush()
    return words


def _words_pdfplumber(page: Any) -> list[Word]:
    out = []
    for w in page.extract_words(use_text_flow=False, extra_attrs=["upright"]):
        if w.get("upright", True):
            out.append(Word(w["text"], w["x0"], w["top"], w["x1"], w["bottom"]))
    return out


@lru_cache(maxsize=1)
def _rapidocr_engine() -> Any:
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _preprocess(image: np.ndarray) -> np.ndarray:
    """Deskew (small angles) and wash out light-grey watermarks."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    scale = 1000 / max(gray.shape)
    small = cv2.resize(gray, None, fx=scale, fy=scale) if scale < 1 else gray
    _, bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = bw.shape

    def score(angle: float) -> float:
        rot = cv2.warpAffine(bw, cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0), (w, h), flags=cv2.INTER_NEAREST)
        return float(np.var(rot.sum(axis=1)))

    coarse = max(np.arange(-5, 5.01, 0.5), key=score)
    angle = float(max(np.arange(coarse - 0.5, coarse + 0.51, 0.1), key=score))
    if abs(angle) >= 0.3:
        gray = cv2.warpAffine(
            gray, cv2.getRotationMatrix2D((gray.shape[1] / 2, gray.shape[0] / 2), angle, 1.0),
            (gray.shape[1], gray.shape[0]), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=255,
        )
    gray = np.where(gray > 195, 255, gray).astype(np.uint8)  # light watermark / tinted header -> white
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def _ocr_words(bgr: np.ndarray) -> list[Word]:
    """OCR an image into word boxes. RapidOCR first, Tesseract as fallback."""
    words: list[Word] = []
    try:
        engine = _rapidocr_engine()
    except ImportError:
        engine = None
    if engine is not None:
        result, _ = engine(bgr)
        for box, text, _score in result or []:
            text = _clean_text(str(text).replace("|", " "))
            if not text:
                continue
            pts = np.asarray(box, dtype=float)
            x0, x1, y0, y1 = pts[:, 0].min(), pts[:, 0].max(), pts[:, 1].min(), pts[:, 1].max()
            parts = text.split(" ")
            total = sum(len(p) for p in parts) + max(len(parts) - 1, 0)
            pos = 0
            for part in parts:
                if part:
                    a = x0 + (x1 - x0) * pos / total
                    b = x0 + (x1 - x0) * (pos + len(part)) / total
                    words.append(Word(part, a, y0, b, y1))
                pos += len(part) + 1
        return words
    try:
        import pytesseract
    except ImportError as exc:
        raise RuntimeError("Scanned page needs an OCR engine: pip install rapidocr-onnxruntime") from exc
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    data = pytesseract.image_to_data(rgb, config="--psm 6", output_type=pytesseract.Output.DICT)
    for i, text in enumerate(data["text"]):
        text = _clean_text(text.replace("|", " "))
        if not text or float(data["conf"][i]) < 0:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        words.append(Word(text, x, y, x + w, y + h))
    return words


def _iter_pages(path: Path, dpi: int) -> Iterator[tuple[int, list[Word], float, str]]:
    """Yield (page_no, words, page_width, source) with source in text|ocr|blank."""
    if path.suffix.lower() in IMAGE_SUFFIXES:
        if cv2 is None:
            raise RuntimeError("opencv-python is required for image input")
        img = cv2.imread(str(path))
        if img is None:
            raise ValueError("Could not read the image file")
        yield 1, _ocr_words(_preprocess(img)), float(img.shape[1]), "ocr"
        return

    if fitz is not None:
        doc = fitz.open(str(path))
        try:
            for number, page in enumerate(doc, start=1):
                words = _words_pymupdf(page)
                if len(words) >= 8:
                    yield number, words, float(page.rect.width), "text"
                    continue
                if not page.get_images(full=True) and len(page.get_drawings()) < 50:
                    yield number, words, float(page.rect.width), ("text" if words else "blank")
                    continue
                pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR if pix.n >= 3 else cv2.COLOR_GRAY2BGR)
                yield number, _ocr_words(_preprocess(bgr)), float(pix.width), "ocr"
        finally:
            doc.close()
        return

    # Fallback without PyMuPDF: pdfplumber (text) + pypdfium2 (render for OCR)
    import pdfplumber
    import pypdfium2

    with pdfplumber.open(str(path)) as plumber:
        renderer = pypdfium2.PdfDocument(str(path))
        for number, page in enumerate(plumber.pages, start=1):
            words = _words_pdfplumber(page)
            if len(words) >= 8:
                yield number, words, float(page.width), "text"
                continue
            if not words and not page.images:
                yield number, words, float(page.width), "blank"
                continue
            pil = renderer[number - 1].render(scale=dpi / 72).to_pil().convert("RGB")
            bgr = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
            yield number, _ocr_words(_preprocess(bgr)), float(bgr.shape[1]), "ocr"


# --------------------------------------------------------------------------- lines & cells


def _median_height(words: Sequence[Word]) -> float:
    heights = [w.h for w in words if len(w.text) >= 2] or [w.h for w in words]
    return float(statistics.median(heights)) if heights else 10.0


def _make_cell(words: list[Word]) -> Cell:
    text = " ".join(w.text for w in words)
    num_x1 = words[-1].x1
    if len(words) >= 2 and re.fullmatch(r"(DB|DR|CR|D|K|C)", words[-1].text.upper()):
        num_x1 = words[-2].x1
    cell = Cell(text, words[0].x0, words[-1].x1, num_x1, list(words))
    cell.money = _is_money(text)
    cell.is_int = (not cell.money) and _is_int_amount(text)
    return cell


def _group_lines(words: Sequence[Word], u: float) -> list[Line]:
    groups: list[dict] = []
    for w in sorted(words, key=lambda w: (w.yc, w.x0)):
        if groups and abs(w.yc - groups[-1]["y"]) <= 0.55 * u:
            g = groups[-1]
            g["w"].append(w)
            g["y"] = sum(x.yc for x in g["w"]) / len(g["w"])
        else:
            groups.append({"y": w.yc, "w": [w]})
    lines = []
    gap = 0.8 * u
    for g in groups:
        cells, cur = [], []
        for w in sorted(g["w"], key=lambda w: w.x0):
            if cur and w.x0 - cur[-1].x1 > gap:
                cells.append(_make_cell(cur))
                cur = []
            cur.append(w)
        if cur:
            cells.append(_make_cell(cur))
        lines.append(Line(cells, g["y"]))
    return lines


# --------------------------------------------------------------------------- header detection


def _cell_role(text: str) -> str | None:
    tokens = re.findall(r"[a-z/:]+", text.lower())
    if not tokens or len(text) > 40:
        return None
    for role, keys in ROLE_KEYS:
        for tok in tokens:
            if tok in keys:
                return role
    for tok in tokens:  # OCR typos: "Kredlt", "Deblt"
        if len(tok) >= 5:
            close = get_close_matches(tok, _ALL_ROLE_WORDS, n=1, cutoff=0.82)
            if close:
                for role, keys in ROLE_KEYS:
                    if close[0] in keys:
                        return role
    return None


def _is_header_line(line: Line) -> bool:
    roles = {r for c in line.cells if (r := _cell_role(c.text))}
    return len(roles) >= 3 and bool(roles & NUMERIC_ROLES) and bool(roles & {"date", "desc"})


def _find_header(lines: list[Line], u: float) -> tuple[int, int, list[tuple[float, str]]] | None:
    for i, line in enumerate(lines):
        if not _is_header_line(line):
            continue
        j = i
        while j + 1 < len(lines) and _is_header_line(lines[j + 1]) and lines[j + 1].y - lines[j].y <= 2.5 * u:
            j += 1
        found: dict[str, list[float]] = {}
        for k in range(i, j + 1):
            for c in lines[k].cells:
                role = _cell_role(c.text)
                if role in NUMERIC_ROLES:
                    bucket = found.setdefault(role, [])
                    for idx, centre in enumerate(bucket):
                        if abs(centre - c.xc) <= 3 * u:
                            bucket[idx] = (centre + c.xc) / 2
                            break
                    else:
                        bucket.append(c.xc)
        header_numeric = sorted((x, r) for r, xs in found.items() for x in xs)
        return i, j, header_numeric
    return None


# --------------------------------------------------------------------------- layout learning


def _cluster(items: list, key, eps: float) -> list[list]:
    ordered = sorted(items, key=key)
    clusters: list[list] = []
    for it in ordered:
        if clusters and key(it) - key(clusters[-1][-1]) <= eps:
            clusters[-1].append(it)
        else:
            clusters.append([it])
    return clusters


def _keep_big(clusters: list[list]) -> list[list]:
    if not clusters:
        return []
    mx = max(len(c) for c in clusters)
    thr = 1 if mx <= 2 else max(2, math.ceil(0.2 * mx))
    return [c for c in clusters if len(c) >= thr]


def _edge(cell: Cell, align: str) -> float:
    return cell.num_x1 if align == "right" else cell.x0 if align == "left" else cell.xc


def _best_match(centres: list[float], header: list[float]) -> list[int]:
    """Monotonic matching of data clusters to header columns; returns header index per cluster (-1 = none)."""
    c, h = len(centres), len(header)
    best_cost, best = math.inf, None
    if c <= h:
        for sub in itertools.combinations(range(h), c):
            d = [centres[i] - header[sub[i]] for i in range(c)]
            med = statistics.median(d)
            cost = sum(abs(x - med) for x in d)
            if cost < best_cost:
                best_cost, best = cost, list(sub)
        return best or []
    for sub in itertools.combinations(range(c), h):
        d = [centres[sub[i]] - header[i] for i in range(h)]
        med = statistics.median(d)
        cost = sum(abs(x - med) for x in d)
        if cost < best_cost:
            best_cost, best = cost, sub
    out = [-1] * c
    for hi, ci in enumerate(best or []):
        out[ci] = hi
    return out


@dataclass
class Layout:
    align: str
    numeric: list[dict]  # {"role","edge","n"}
    tol: float
    text_cols: list[dict]  # {"role","start"}


def _learn_numeric(money_cells: list[Cell], header_numeric: list[tuple[float, str]], u: float) -> tuple[str, list[dict], float] | None:
    if not money_cells:
        return None
    eps = 0.7 * u
    best_align, best_cov, best_clusters = "right", -1.0, []
    for align in ("right", "left", "center"):
        kept = _keep_big(_cluster(money_cells, lambda c, a=align: _edge(c, a), eps))
        cov = sum(len(c) for c in kept) / len(money_cells)
        if cov > best_cov + 0.03:
            best_align, best_cov, best_clusters = align, cov, kept
    clusters = sorted(best_clusters, key=lambda cl: statistics.mean(c.xc for c in cl))
    if not clusters:
        return None
    centres = [statistics.mean(c.xc for c in cl) for cl in clusters]
    roles: list[str | None]
    if header_numeric:
        match = _best_match(centres, [x for x, _ in header_numeric])
        roles = [header_numeric[i][1] if i >= 0 else None for i in match]
        if "balance" in {r for _, r in header_numeric} and roles and roles[-1] != "balance" and len(clusters) >= 2:
            pass  # trust the header ordering; reconciliation will flag mismatches
    else:
        n = len(clusters)
        if n == 1:
            roles = ["amount"]
        elif n == 2:
            roles = ["amount", "balance"]
        else:
            roles = [None] * (n - 3) + ["debit", "credit", "balance"]
    numeric = []
    for cl, role in zip(clusters, roles):
        numeric.append({"role": role, "edge": statistics.mean(_edge(c, best_align) for c in cl), "n": len(cl)})
    edges = sorted(n["edge"] for n in numeric)
    sep = min((b - a for a, b in zip(edges, edges[1:])), default=6 * u)
    return best_align, numeric, max(1.5 * u, min(sep / 2, 4 * u))


def _detect_start(line: Line, page_w: float) -> tuple[int, tuple, str] | None:
    """If the line begins a transaction return (date cell index, date parts, trailing text)."""
    for idx, cell in enumerate(line.cells[:3]):
        if idx > 0 and not all(re.fullmatch(r"\d{1,4}\.?", c.text) for c in line.cells[:idx]):
            return None
        if cell.x0 > 0.55 * page_w:
            return None
        if cell.text.upper().startswith("PEND") and len(cell.text) <= 6:
            return idx, (None, None, None), ""
        if cell.text.rstrip(".").lower() in MONTHS:
            return idx, (None, None, None), ""
        m = _match_date(cell.text)
        if m:
            parts, end = m
            return idx, parts, cell.text[end:].strip()
    return None


# --------------------------------------------------------------------------- page processing


@dataclass
class State:
    header_numeric: list[tuple[float, str]] = field(default_factory=list)
    layout: Layout | None = None
    has_rows: bool = False
    printed: dict[str, float] = field(default_factory=dict)


def _process_page(words: list[Word], page_w: float, page_no: int, state: State) -> tuple[list[ExtractedRow], dict, str]:
    diag: dict[str, Any] = {"page": page_no, "rows": 0}
    if not words:
        return [], diag, ""
    u = _median_height(words)
    lines = _group_lines(words, u)
    page_text = "\n".join(l.text for l in lines)
    band = _summary_band(lines)
    if band:
        state.printed.update(band)

    header = _find_header(lines, u)
    first = 0
    if header:
        first = header[1] + 1
        state.header_numeric = header[2]
        # A new header can describe a different table (or a different bank in
        # a concatenated PDF); never apply the previous table's x-coordinates.
        state.layout = None
    diag["header"] = bool(header)

    starts: dict[int, tuple[int, tuple, str]] = {}
    for i in range(first, len(lines)):
        s = _detect_start(lines[i], page_w)
        if s:
            starts[i] = s
    if not starts:
        # maybe only an opening-balance line on this page
        pass
    first_start = min(starts) if starts else None

    # region = header .. first stop line after the first start
    end = len(lines)
    if first_start is not None:
        for i in range(first_start + 1, len(lines)):
            if i not in starts and STOP_RE.match(lines[i].text):
                end = i
                break
    region = list(range(first, end))

    # ---- numeric layout
    date_x1 = max((lines[i].cells[starts[i][0]].x1 for i in starts), default=0.0)
    money_cells = [c for i in region for c in lines[i].cells if c.money and c.x0 > date_x1]
    layout = state.layout
    learned = _learn_numeric(money_cells, state.header_numeric, u) if money_cells else None
    if learned and (len(starts) >= 3 or layout is None):
        align, numeric, tol = learned
        layout = Layout(align, numeric, tol, [])
    elif layout is None:
        return [], diag, page_text
    align, numeric, tol = layout.align, layout.numeric, layout.tol

    def numeric_for(cell: Cell) -> dict | None:
        if not (cell.money or cell.is_int):
            return None
        e = _edge(cell, align)
        best = min(numeric, key=lambda n: abs(n["edge"] - e), default=None)
        if best is None:
            return None
        limit = tol if cell.money else 0.5 * u
        return best if abs(best["edge"] - e) <= limit else None

    for i in region:
        for c in lines[i].cells:
            n = numeric_for(c)
            c.role = n["role"] if n else None
            if n and not c.money:
                c.money = True  # plain integer sitting in a money column

    # ---- text columns (learned from start lines)
    n_start = len(starts)
    text_cells: list[Cell] = []
    date_starts: list[float] = []
    for i, (di, _p, _r) in starts.items():
        date_starts.append(lines[i].cells[di].x0)
        text_cells += [c for j, c in enumerate(lines[i].cells) if j > di and not (c.money and c.role)]
    text_cols: list[dict] = []
    if layout.text_cols and n_start < 3:
        text_cols = layout.text_cols
    elif date_starts:
        thr = 1 if n_start <= 2 else max(2, math.ceil(0.15 * n_start))
        clusters = [c for c in _cluster(text_cells, lambda c: c.x0, 0.8 * u) if len(c) >= thr]
        date_start = statistics.median(date_starts)
        cols = [{"start": statistics.median(c.x0 for c in cl), "chars": sum(len(c.text) for c in cl)} for cl in clusters]
        cols = [c for c in cols if c["start"] > date_start + 0.8 * u]
        if cols:
            desc = max(cols, key=lambda c: c["chars"])
            for c in cols:
                c["role"] = "desc" if c is desc else ("ref" if c["start"] < desc["start"] else "extra")
        text_cols = [{"start": date_start, "role": "date"}] + sorted(cols, key=lambda c: c["start"])
        layout.text_cols = text_cols
    state.layout = layout

    def text_role(cell: Cell) -> str:
        role = "other"
        for col in text_cols:
            if col["start"] - 1.0 * u <= cell.x0:
                role = col["role"]
        return role

    # ---- assemble rows
    rows: list[ExtractedRow] = []
    cur: ExtractedRow | None = None
    cur_y = 0.0
    closed = True
    desc_parts: list[str] = []
    ref_parts: list[str] = []
    extra_parts: list[str] = []
    raw_parts: list[str] = []

    def commit() -> None:
        nonlocal cur
        if cur is None:
            return
        cur.description = _clean_text(" ".join(desc_parts))
        cur.reference = _clean_text(" ".join(ref_parts))
        cur.extra = _clean_text(" ".join(extra_parts))
        cur.raw_text = _clean_text(" ".join(raw_parts))
        narrative_direction = _description_direction(cur.description)
        if cur.pending_mutasi is not None and narrative_direction:
            if cur.pending_dir and cur.pending_dir != narrative_direction:
                cur.validation.append("direction_corrected")
            cur.pending_dir = narrative_direction
        rows.append(cur)
        cur = None

    def put_numeric(row: ExtractedRow, cell: Cell, direction: str | None) -> None:
        amt = parse_amount(cell.text)
        if amt is None:
            return
        role = cell.role
        if role == "balance":
            if row.balance is not None:
                row.validation.append("multiple_balances")
                return
            norm = _norm_money(cell.text)
            overdrawn = norm.startswith(("-", "(")) or norm.endswith(("DB", "DR", "-"))  # overdraft marker
            row.balance = -abs(amt.value) if overdrawn else amt.value
        elif role in ("debit", "credit"):
            if amt.value == 0:
                return
            if getattr(row, role) is not None:
                row.validation.append("multiple_amounts")
                return
            setattr(row, role, abs(amt.value))
        elif role == "amount":
            if amt.value == 0:
                return
            if row.pending_mutasi is not None:
                row.validation.append("multiple_amounts")
                return
            row.pending_mutasi = abs(amt.value)
            row.pending_dir = amt.direction or direction

    for i in region:
        line = lines[i]
        if i in starts:
            commit()
            di, parts, rest = starts[i]
            cur = ExtractedRow(page=page_no, raw_text="", date_parts=parts if parts[0] else None)
            if parts[0] is None:
                cur.validation.append("pending_transaction")
            desc_parts, ref_parts, extra_parts, raw_parts = [], [], [], []
            closed = False
            if rest and not _PURE_TIME_RE.match(rest):
                desc_parts.append(rest)
            cells = [c for j, c in enumerate(line.cells) if j > di]
            raw_parts.append(line.cells[di].text)
        else:
            text = line.text
            is_opening = OPENING_RE.match(text) is not None
            if is_opening and not state.has_rows and not rows and first_start is not None and i < first_start:
                bal = [c for c in line.cells if c.money and c.role == "balance"] or [c for c in line.cells if c.money][-1:]
                if bal:
                    amt = parse_amount(bal[-1].text)
                    if amt:
                        rows.append(ExtractedRow(page=page_no, raw_text=_clean_text(text), description=_clean_text(" ".join(c.text for c in line.cells if not c.money)) or "SALDO AWAL", balance=amt.value, is_opening=True))
                continue
            if cur is None or closed:
                continue
            if STOP_RE.match(text) or line.y - cur_y > 2.6 * u + 2:
                closed = True
                continue
            cells = line.cells
        line_direction = _line_direction(cells)
        cur_y = line.y
        raw_parts.append(line.text if i not in starts else " ".join(c.text for c in cells))
        for c in cells:
            if c.money and c.role:
                put_numeric(cur, c, line_direction)
                continue
            if c.money and c.role is None:
                # Keep a valid amount even when OCR/layout learning misses a
                # column on one sparse transaction row.
                amount = parse_amount(c.text)
                if amount and cur.pending_mutasi is None:
                    cur.pending_mutasi = abs(amount.value)
                    cur.pending_dir = amount.direction or line_direction
                    cur.validation.append("amount_column_inferred")
                    continue
            role = text_role(c)
            if role == "desc":
                desc_parts.append(c.text)
            elif role == "ref":
                ref_parts.append(c.text)
            elif role == "extra":
                extra_parts.append(c.text)
            elif role == "date":
                if not _PURE_TIME_RE.match(c.text):
                    ref_parts.append(c.text)
    commit()

    # rows with no money at all are page noise (footers with a date etc.)
    kept = [r for r in rows if r.is_opening or r.balance is not None or r.debit or r.credit or r.pending_mutasi]
    diag["dropped_noise_rows"] = len(rows) - len(kept)
    diag["rows"] = len(kept)
    diag["columns"] = [n["role"] for n in numeric]
    if kept:
        state.has_rows = True
    return kept, diag, page_text


# --------------------------------------------------------------------------- reconciliation

_TOL = 0.51


def _signed(row: ExtractedRow) -> float | None:
    if row.debit or row.credit:
        return (row.credit or 0.0) - (row.debit or 0.0)
    return None


def _magnitude(row: ExtractedRow) -> float:
    s = _signed(row)
    return abs(s) if s else (row.pending_mutasi or 0.0)


def _chain_matches(seq: list[ExtractedRow]) -> tuple[int, int, int]:
    """(magnitude matches, sign-correct matches, sign-inverted matches)."""
    mag_ok = ok = inv = 0
    for a, b in zip(seq, seq[1:]):
        if a.balance is None or b.balance is None:
            continue
        mag = _magnitude(b)
        if not mag:
            continue
        d = b.balance - a.balance
        if abs(abs(d) - mag) < _TOL:
            mag_ok += 1
            s = _signed(b)
            if s is not None:
                if abs(d - s) < _TOL:
                    ok += 1
                elif abs(d + s) < _TOL:
                    inv += 1
    return mag_ok, ok, inv


def _reconcile(rows: list[ExtractedRow], opening_hint: float | None = None) -> dict[str, Any]:
    info: dict[str, Any] = {"order": "as_printed", "debit_credit_swapped": False}
    for r in rows:
        if not r.is_opening and OPENING_RE.match(r.description or "") and not (r.debit or r.credit or r.pending_mutasi) and r.balance is not None:
            r.is_opening = True
    openings = [r for r in rows if r.is_opening]
    body = [r for r in rows if not r.is_opening]

    fwd = _chain_matches(openings[:1] + body)
    rev = _chain_matches(openings[:1] + body[::-1])
    seq_body = body
    if rev[0] > fwd[0]:
        seq_body = body[::-1]
        info["order"] = "newest_first"
    seq = openings[:1] + seq_body

    _, ok, inv = _chain_matches(seq)
    if inv >= 2 and inv > 2 * ok:
        for r in body:
            r.debit, r.credit = r.credit, r.debit
        info["debit_credit_swapped"] = True

    prev: float | None = openings[0].balance if openings else opening_hint
    if prev is None and seq_body:
        first = seq_body[0]
        first_signed = _signed(first)
        if first_signed is None and first.pending_mutasi is not None:
            direction = first.pending_dir or _hint_direction(first.description)
            if direction == "credit":
                first_signed = first.pending_mutasi
            elif direction == "debit":
                first_signed = -first.pending_mutasi
        if first.balance is not None and first_signed is not None:
            # Many statements print no explicit opening row.  The first
            # running balance still gives an unambiguous opening value.
            prev = first.balance - first_signed
            opening_hint = prev
            info["inferred_opening_balance"] = prev

    for row in seq_body:
        signed = _signed(row)
        if row.pending_mutasi is not None and signed is None:
            mag, hinted = row.pending_mutasi, row.pending_dir or _hint_direction(row.description)
            direction, evidence = _choose_direction(
                prev, row.balance, mag, hinted, row.description
            )
            if direction is None:
                direction = "debit"
                row.validation.extend(["direction_guessed", "direction_unknown"])
            elif hinted and evidence == "balance_delta" and hinted != direction:
                row.validation.append("direction_corrected")
            if direction == "credit":
                row.credit = mag
            else:
                row.debit = mag
        elif signed is not None and row.balance is not None and prev is not None:
            d = row.balance - prev
            if abs(d - signed) >= _TOL:
                if abs(d + signed) < _TOL:
                    row.debit, row.credit = row.credit, row.debit
                    row.validation.append("direction_corrected")
                else:
                    row.validation.append("balance_mismatch")
        elif signed is None and row.pending_mutasi is None and row.balance is not None and prev is not None:
            d = row.balance - prev
            if abs(d) >= _TOL:
                if d > 0:
                    row.credit = d
                else:
                    row.debit = -d
                row.validation.append("amount_derived")
        if row.balance is None and prev is not None:
            row.balance = prev + (row.credit or 0.0) - (row.debit or 0.0)
            row.validation.append("saldo_calculated")
        if row.balance is not None:
            prev = row.balance

    penalties = {"balance_mismatch": 0.5, "amount_derived": 0.35, "saldo_calculated": 0.2, "direction_guessed": 0.3,
                 "direction_corrected": 0.05, "date_invalid": 0.3, "multiple_amounts": 0.3, "multiple_balances": 0.3,
                 "pending_transaction": 0.1}
    for r in rows:
        r.confidence = max(0.0, 1.0 - sum(penalties.get(v, 0.2) for v in r.validation))
    if opening_hint is not None and not openings:
        info["opening_inferred"] = True
    return info


def _apply_dates(rows: Sequence[ExtractedRow], year: int | None) -> None:
    for row in rows:
        if row.date_parts is None:
            continue
        day, month, row_year = row.date_parts
        row_year = row_year or year
        if row_year is None:
            row.date = f"{day:02d}/{month:02d}"
        else:
            try:
                row.date = date(row_year, month, day)
            except ValueError:
                row.validation.append("date_invalid")
                row.date = f"{day:02d}/{month:02d}/{row_year}"


def _printed_totals(text: str) -> dict[str, float]:
    """Totals printed by the bank in the footer ('Total Kredit : 148.636' style)."""
    out = {}
    for key, pat in (("credit", r"(?:TOTAL\s+)?(?:TRANSAKSI\s+)?(?:KREDIT|CREDIT|INCOMING|DEPOSIT(?:S)?)[^\d\n]{0,20}([\d.,]+)"),
                     ("debit", r"(?:TOTAL\s+)?(?:TRANSAKSI\s+)?(?:DEBET|DEBIT|OUTGOING|WITHDRAWAL(?:S)?)[^\d\n]{0,20}([\d.,]+)"),
                     ("opening", r"(?:SALDO\s+AWAL|OPENING\s+BALANCE|STARTING\s+BALANCE|BEGINNING\s+BALANCE)[^\d\n]{0,20}([\d.,]+)"),
                     ("closing", r"(?:SALDO\s+AKHIR|CLOSING\s+BALANCE|ENDING\s+BALANCE|ACCOUNT\s+CLOSING\s+BALANCE|SALDO\s+REKENING)[^\d\n]{0,20}([\d.,]+)")):
        m = re.search(pat, text.upper())
        if m and (a := parse_amount(m.group(1))):
            out[key] = a.value
    return out


def _summary_band(lines: list[Line]) -> dict[str, float]:
    """Footer band: labels (Saldo Awal | Total Debet | Total Kredit | Saldo Akhir) with 4 numbers below."""
    for i, line in enumerate(lines):
        t = line.text.upper()
        if (re.search(r"SALDO\s+AWAL|OPENING\s+BALANCE", t) and re.search(r"DEBE?T|DEBIT", t)
                and re.search(r"KREDIT|CREDIT", t) and re.search(r"SALDO\s+AKHIR|CLOSING", t)):
            for nxt in lines[i + 1:i + 4]:
                nums = [c for c in nxt.cells if c.money]
                if len(nums) == 4:
                    vals = [parse_amount(c.text) for c in nums]
                    if all(vals):
                        return dict(zip(("opening", "debit", "credit", "closing"), (v.value for v in vals)))
    return {}


# --------------------------------------------------------------------------- public API


def extract_statement(pdf_path: str | Path, dpi: int = 220) -> dict[str, Any]:
    input_path = Path(pdf_path)
    if input_path.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
        raise ValueError(f"Unsupported file format. Needs: {', '.join(sorted(SUPPORTED_INPUT_SUFFIXES))}")

    rows: list[ExtractedRow] = []
    pages: list[dict] = []
    all_text: list[str] = []
    state = State()
    for number, words, page_w, source in _iter_pages(input_path, dpi):
        page_rows, diag, page_text = _process_page(words, page_w, number, state)
        diag["source"] = source
        diag["words"] = len(words)
        rows.extend(page_rows)
        pages.append(diag)
        all_text.append(page_text)

    full_text = "\n".join(all_text)
    explicit = [r.date_parts[2] for r in rows if r.date_parts and r.date_parts[2]]
    if not explicit:
        explicit = [int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", full_text)]
    explicit = [y for y in explicit if 2000 <= y <= 2100]
    year = Counter(explicit).most_common(1)[0][0] if explicit else None

    _apply_dates(rows, year)
    printed = {**_printed_totals(full_text), **state.printed}
    has_opening_row = any(r.is_opening for r in rows)
    info = _reconcile(rows, None if has_opening_row else printed.get("opening"))

    body = [r for r in rows if not r.is_opening]
    opening = next(
        (r.balance for r in rows if r.is_opening),
        printed.get("opening", info.get("inferred_opening_balance")),
    )
    balances = [r.balance for r in (body[::-1] if info["order"] == "newest_first" else body) if r.balance is not None]
    total_debit = sum(r.debit or 0.0 for r in body)
    total_credit = sum(r.credit or 0.0 for r in body)
    closing = balances[-1] if balances else printed.get("closing")
    summary: dict[str, Any] = {
        **info,
        "opening_balance": opening, "closing_balance": closing,
        "total_debit": total_debit, "total_credit": total_credit,
        "rows_flagged": sum(bool(r.validation) for r in rows),
        "chain_reconciles": (abs(opening + total_credit - total_debit - closing) < 1.0
                             if opening is not None and closing is not None else None),
    }
    if printed:
        summary["printed_totals"] = printed
        computed = {"debit": total_debit, "credit": total_credit, "closing": closing, "opening": opening}
        summary["printed_totals_match"] = all(
            computed[k] is not None and abs(printed[k] - computed[k]) < 1.0 for k in printed if k in computed
        )
    return {"pages": pages, "rows": rows, "document_year": year, "summary": summary}


def _category(description: str, debit: float | None, credit: float | None) -> str:
    if not description:
        return "Uncategorized"
    for name, pattern in _CATEGORY_RES:
        if pattern.search(description.upper()):
            return name
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Indonesian bank statements to Excel.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--output", type=Path, default=Path("output"))
    args = parser.parse_args()

    result = extract_statement(args.file)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "transactions.xlsx").write_bytes(excel_bytes(result))
    print(f"Extracted {len(result['rows'])} transaction rows to {args.output}")
    print(result["summary"])


if __name__ == "__main__":
    main()
