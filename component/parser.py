import re
from typing import Sequence
from .constants import (
    WEEKDAY_RE, GLUED_TIME_RE, ALPHA_DATE_RE, NUM_DATE_RE, TIME_RE, PURE_TIME_RE,
    MONTHS, DEBIT_HINT_RE, CREDIT_HINT_RE, MONEY_RE, INT_RE, ACCOUNT_RE, BANK_RES
)
from .structures import Amount, Cell

def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()

def norm_money(text: str) -> str:
    return re.sub(r"\s+", "", str(text).upper()).replace("RP.", "").replace("RP", "").replace("IDR", "")

def is_money(text: str) -> bool:
    return bool(MONEY_RE.match(norm_money(text)))

def is_int_amount(text: str) -> bool:
    return bool(INT_RE.match(norm_money(text)))

def detect_account(text: str) -> str | None:
    for m in ACCOUNT_RE.finditer(text or ""):
        raw = m.group(1).strip()
        raw = re.split(r"\s{2,}|\n", raw)[0]
        if NUM_DATE_RE.match(raw) or ALPHA_DATE_RE.match(raw):
            continue
        digits = re.sub(r"\D", "", raw)
        if 5 <= len(digits) <= 20:
            return digits
    return None

def detect_bank(text: str) -> str | None:
    if not text:
        return None
    for name, pat in BANK_RES:
        if pat.search(text):
            return name
    return None

def parse_time(text: str) -> tuple[int, int, int] | None:
    m = PURE_TIME_RE.match(text.strip()) or TIME_RE.match(text)
    if not m:
        return None
    groups = m.groups()
    if len(groups) == 5 and groups[4] is not None:
        return (int(groups[4]), 0, 0)
    if groups[0] is None:
        return None
    h, mi, s, ampm = int(groups[0]), int(groups[1] or 0), int(groups[2] or 0), (groups[3] or "").upper()
    if ampm == "PM" and h < 12:
        h += 12
    elif ampm == "AM" and h == 12:
        h = 0
    return (h, mi, s)

def parse_amount(text: str) -> Amount | None:
    if not text:
        return None
    s = re.sub(r"(?<=\d)\s+(?=[\d.,])", "", norm_money(text).strip(".,;:"))
    m = re.fullmatch(r"([+\-]?)(\()?(\d[\d.,]*)(\))?(DB|DR|CR|D|K|C|[+\-])?", s)
    if not m:
        return None
    sign, lparen, num, _rparen, suffix = m.groups()
    num = num.rstrip(".,")
    has_dot, has_comma = "." in num, "," in num
    if has_dot and has_comma:
        decimal = "," if num.rfind(",") > num.rfind(".") else "."
        num = num.replace("." if decimal == "," else ",", "")
        parts = num.split(decimal)
        num = ("".join(parts[:-1]) + "." + parts[-1]) if len(parts[-1]) <= 2 else "".join(parts)
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = num.split(sep)
        if len(parts) > 2:
            num = ("".join(parts[:-1]) + "." + parts[-1]) if len(parts[-1]) <= 2 else num.replace(sep, "")
        elif len(parts[-1]) == 3 and parts[0] not in ("0", "00") and len(parts[0]) <= 3:
            num = num.replace(sep, "")
        else:
            num = num.replace(sep, ".")
    try:
        value = float(num)
    except ValueError:
        return None
    direction = (
        "debit" if (sign == "-" or lparen or suffix in ("DB", "DR", "D", "-"))
        else "credit" if (sign == "+" or suffix in ("CR", "C", "K", "+"))
        else None
    )
    return Amount(value, direction)

def norm_year(y: int | None) -> int | None:
    if y is None:
        return None
    if y < 100:
        return (1900 + y) if y >= 70 else (2000 + y)
    return y if 1900 <= y <= 2100 else None

def match_date(text: str) -> tuple[tuple, int] | None:
    raw = text.strip()
    lead = len(text) - len(text.lstrip())
    wk = WEEKDAY_RE.match(raw)
    wk_len = wk.end() if wk else 0
    sub = raw[wk_len:]

    split_at: int | None = None
    if gm := GLUED_TIME_RE.match(sub):
        tail = sub[gm.end(1):]
        if re.match(r"^(?:[01]?\d|2[0-3]):[0-5]\d", tail) \
                    or (gm.group(2) is not None and len(gm.group(2)) == 2 \
                    and not sub[gm.start(2):gm.end(1) + 2].startswith(("19", "20")) \
                    and re.match(r"^(?:[01]\d|2[0-3])\b", tail)):
            split_at = gm.end(1)
            sub = sub[:split_at] + " " + sub[split_at:]

    parts: tuple | None = None
    end = 0
    if am := ALPHA_DATE_RE.match(sub):
        n_pre, mon1, n_post, mon2, m_n1, m_n2 = am.groups()
        mon_s = mon1 or mon2
        nums = [int(x) for x in (n_pre, n_post, m_n1, m_n2) if x is not None]
        month = MONTHS[mon_s.lower()]
        if len(nums) == 1 and 1 <= nums[0] <= 31:
            parts, end = (nums[0], month, None), am.end()
        elif len(nums) == 2:
            n0, n1 = nums
            if n0 >= 100 or n0 > 31 or n0 == 0:
                day, yr = n1, norm_year(n0)
            else:
                day, yr = n0, norm_year(n1)
            if 1 <= day <= 31 and yr is not None:
                parts, end = (day, month, yr), am.end()
    elif nm := NUM_DATE_RE.match(sub):
        s1, sep, s2, s3 = nm.groups()
        if s3 is None:
            if sep != ".":
                a, b = int(s1), int(s2)
                if 1 <= a <= 31 and 1 <= b <= 31 and min(a, b) <= 12:
                    parts, end = (a, b, None, True), nm.end()
        else:
            a, b, c = int(s1), int(s2), int(s3)
            if len(s1) == 4 or a > 31 or a == 0:
                yr, x, y, iso = norm_year(a), b, c, True
            else:
                yr, x, y, iso = norm_year(c), a, b, False
            if yr is not None and 1 <= x <= 31 and 1 <= y <= 31 and min(x, y) <= 12:
                parts = ((y, x, yr) if x <= 12 else (x, y, yr)) if iso else (x, y, yr, True)
                end = nm.end()

    if parts is None:
        return None
    if tm := TIME_RE.match(sub[end:]):
        end += tm.end()
    if split_at is not None and end > split_at:
        end -= 1
    return parts, lead + wk_len + end

def hint_direction(description: str) -> str | None:
    text = description.upper()
    if re.search(r"\b(?:KR|CR)\b", text):
        return "credit"
    debit, credit = bool(DEBIT_HINT_RE.search(text)), bool(CREDIT_HINT_RE.search(text))
    return "debit" if debit and not credit else "credit" if credit and not debit else None

def explicit_direction(text: str) -> str | None:
    tokens = re.findall(r"[A-Z]+", text.upper())
    if any(token in {"CR", "CREDIT"} for token in tokens):
        return "credit"
    if any(token in {"DB", "DEBIT"} for token in tokens):
        return "debit"
    return None

def description_direction(description: str) -> str | None:
    text = description.upper()
    if re.search(r"\b(KR|CR|KREDIT|CREDIT)\b", text):
        return "credit"
    if re.search(r"\b(TARIK|TARIKAN|PENARIKAN|TRANSAKSI|DEBIT|DB|TRSF|TRF)\b", text):
        return "debit"
    return None

def marker_direction(text: str) -> str | None:
    tokens = re.findall(r"[A-Z]+", text.upper())
    if any(token in {"DB", "DR", "D", "DEBIT", "DEBET", "OUT", "KELUAR"} for token in tokens):
        return "debit"
    if any(token in {"CR", "C", "K", "CREDIT", "KREDIT", "IN", "MASUK"} for token in tokens):
        return "credit"
    return None

def line_direction(cells: Sequence[Cell]) -> str | None:
    for cell in cells:
        if cell.money and (amount := parse_amount(cell.text)) and amount.direction:
            return amount.direction
        if direction := marker_direction(cell.text):
            return direction
    return None