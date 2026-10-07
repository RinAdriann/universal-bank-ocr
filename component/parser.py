"""Parsing routines for text, numbers, dates and basic direction hints."""

import re
from typing import Sequence
from .constants import _DATE_PATTERNS, _TIME_RE, MONTHS, DEBIT_HINT_RE, CREDIT_HINT_RE
from .models import Amount, Cell

def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).replace(" ", " ")).strip()

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
    if not text:
        return None
    s = _norm_money(text).strip(".,;:")
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
    """Return cash-flow direction from narrative text, not a column name.

    ``CR`` means incoming money in BCA narratives. It is deliberately mapped
    to the internal credit side only after the parser has identified the
    amount column; it must never decide which numeric column is saldo.
    """
    text = description.upper()
    # BCA uses KR/CR as an incoming marker. Check these first because a row
    # can also contain generic words such as TRSF or TRANSAKSI.
    if re.search(r"\b(?:KR|CR)\b", text):
        return "credit"
    debit, credit = bool(DEBIT_HINT_RE.search(text)), bool(CREDIT_HINT_RE.search(text))
    return "debit" if debit and not credit else "credit" if credit and not debit else None

def _description_direction(description: str) -> str | None:
    text = description.upper()
    if re.search(r"\b(KR|CR|KREDIT|CREDIT)\b", text):
        return "credit"
    if re.search(r"\b(TARIK|TARIKAN|PENARIKAN|TRANSAKSI|DEBIT|DB|TRSF|TRF)\b", text):
        return "debit"
    return None

def _marker_direction(text: str) -> str | None:
    tokens = re.findall(r"[A-Z]+", text.upper())
    if any(token in {"DB", "DR", "D", "DEBIT", "DEBET", "OUT", "KELUAR"} for token in tokens):
        return "debit"
    if any(token in {"CR", "C", "K", "CREDIT", "KREDIT", "IN", "MASUK"} for token in tokens):
        return "credit"
    return None

def _line_direction(cells: Sequence[Cell]) -> str | None:
    for cell in cells:
        if cell.money:
            amount = parse_amount(cell.text)
            if amount and amount.direction:
                return amount.direction
        direction = _marker_direction(cell.text)
        if direction:
            return direction
    return None
