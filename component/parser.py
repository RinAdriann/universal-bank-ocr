import re
from typing import Sequence
from .constants import (
    ACCOUNT_RE, ALPHA_DATE_RE, CREDIT_HINT_RE, DEBIT_HINT_RE,
    GLUED_TIME_RE, INT_RE, MONEY_RE, MONTHS, NUM_DATE_RE, PURE_TIME_RE,
    TIME_RE, WEEKDAY_RE,
)
from .structures import Amount, Cell, Word


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def norm_money(text: str) -> str:
    cleaned = re.sub(r"\s+", "", str(text).upper())
    cleaned = cleaned.replace("RP.", "").replace("RP", "").replace("IDR", "")
    cleaned = re.sub(r"([.,])(?:[DCO][O0]{1,2}|0[O][O0]?)([DKC]?)$", r"\g<1>00\2", cleaned)
    cleaned = re.sub(r"(\.[0]{2})[XM4]$", r"\1K", cleaned)
    return "0.00" if cleaned == "00.0" else cleaned


def is_money(text: str) -> bool:
    return bool(MONEY_RE.match(norm_money(text)))


def is_int_amount(text: str) -> bool:
    return bool(INT_RE.match(norm_money(text)))


def detect_account(text: str) -> str | None:
    ascii_text = re.sub(r"[^\x00-\x7F]+", " ", text or "")
    for match in ACCOUNT_RE.finditer(ascii_text):
        raw = match.group(1).strip()
        raw = re.split(r"\s{2,}|\n", raw)[0]
        if NUM_DATE_RE.match(raw) or ALPHA_DATE_RE.match(raw):
            continue
        digits = re.sub(r"\D", "", raw)
        if 5 <= len(digits) <= 20:
            return digits
    return None


def detect_bank(words: Sequence[Word]) -> str:
    if not words:
        return ""
    alpha = [w for w in words if len(re.sub(r"[^A-Za-z]", "", w.text)) >= 2]
    if not alpha:
        return ""
    tallest = max(alpha, key=lambda w: w.h)
    same_line = [w for w in alpha if abs(w.yc - tallest.yc) < 0.6 * tallest.h and w.h >= 0.7 * tallest.h]
    same_line.sort(key=lambda w: w.x0)
    return clean_text(" ".join(re.sub(r"[^A-Za-z0-9]", "", w.text) for w in same_line)).upper()


def parse_time(text: str) -> tuple[int, int, int] | None:
    match = PURE_TIME_RE.match(text.strip()) or TIME_RE.match(text)
    if not match:
        return None
    groups = match.groups()
    if len(groups) == 5 and groups[4] is not None:
        return (int(groups[4]), 0, 0)
    if groups[0] is None:
        return None
    hour = int(groups[0])
    minute = int(groups[1] or 0)
    sec = int(groups[2] or 0)
    ampm = (groups[3] or "").upper()
    if ampm == "PM" and hour < 12:
        hour += 12
    elif ampm == "AM" and hour == 12:
        hour = 0
    return (hour, minute, sec)


def parse_amount(text: str) -> Amount | None:
    if not text:
        return None
    raw = re.sub(r"(?<=\d)\s+(?=[\d.,])", "", norm_money(text).strip(".,;:"))
    match = re.fullmatch(r"([+\-]?)(\()?(\d[\d.,]*)(\))?(DB|DR|CR|D|K|C|[+\-])?", raw)
    if not match:
        return None
    sign, lparen, num, _, suffix = match.groups()
    num = num.rstrip(".,")
    has_dot = "." in num
    has_comma = "," in num

    if has_dot and has_comma:
        decimal = "," if num.rfind(",") > num.rfind(".") else "."
        num = num.replace("." if decimal == "," else ",", "")
        parts = num.split(decimal)
        if len(parts[-1]) <= 2:
            num = "".join(parts[:-1]) + "." + parts[-1]
        elif decimal == "." and parts[-1] == "000":
            num = "".join(parts[:-1]) + ".00"
            if not suffix:
                suffix = "D"
        else:
            num = "".join(parts)
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = num.split(sep)
        if len(parts) > 2:
            if len(parts[-1]) <= 2:
                num = "".join(parts[:-1]) + "." + parts[-1]
            elif len(parts) >= 4 and parts[-1] == "000":
                num = "".join(parts[:-1]) + ".00"
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

    if sign == "-" or lparen or suffix in ("DB", "DR", "D", "-"):
        direction = "debit"
    elif sign == "+" or suffix in ("CR", "C", "K", "+"):
        direction = "credit"
    else:
        direction = None
    return Amount(value, direction)


def norm_year(year: int | None) -> int | None:
    if year is None:
        return None
    if year < 100:
        return (1900 + year) if year >= 70 else (2000 + year)
    return year if 1900 <= year <= 2100 else None


def match_date(text: str) -> tuple[tuple, int] | None:
    raw = text.strip()
    lead = len(text) - len(text.lstrip())
    wk = WEEKDAY_RE.match(raw)
    wk_len = wk.end() if wk else 0
    sub = raw[wk_len:]

    split_at = None
    glued = GLUED_TIME_RE.match(sub)
    if glued:
        tail = sub[glued.end(1):]
        has_time = bool(re.match(r"^(?:[01]?\d|2[0-3]):[0-5]\d", tail))
        has_short_year = (
            glued.group(2) is not None
            and len(glued.group(2)) == 2
            and not sub[glued.start(2):glued.end(1) + 2].startswith(("19", "20"))
            and bool(re.match(r"^(?:[01]\d|2[0-3])\b", tail))
        )
        if has_time or has_short_year:
            split_at = glued.end(1)
            sub = sub[:split_at] + " " + sub[split_at:]

    parts = None
    end = 0
    alpha_m = ALPHA_DATE_RE.match(sub)
    num_m = None if alpha_m else NUM_DATE_RE.match(sub)

    if alpha_m:
        n_pre, mon1, n_post, mon2, m_n1, m_n2 = alpha_m.groups()
        mon_s = mon1 or mon2
        nums = [int(x) for x in (n_pre, n_post, m_n1, m_n2) if x is not None]
        month = MONTHS[mon_s.lower()]
        if len(nums) == 1 and 1 <= nums[0] <= 31:
            parts, end = (nums[0], month, None), alpha_m.end()
        elif len(nums) == 2:
            n0, n1 = nums
            if n0 >= 100 or n0 > 31 or n0 == 0:
                day, yr = n1, norm_year(n0)
            else:
                day, yr = n0, norm_year(n1)
            if 1 <= day <= 31 and yr is not None:
                parts, end = (day, month, yr), alpha_m.end()
    elif num_m:
        s1, sep, s2, s3 = num_m.groups()
        if s3 is None:
            if sep != ".":
                a, b = int(s1), int(s2)
                if 1 <= a <= 31 and 1 <= b <= 31 and min(a, b) <= 12:
                    parts, end = (a, b, None, True), num_m.end()
        else:
            a, b, c = int(s1), int(s2), int(s3)
            if len(s1) == 4 or a > 31 or a == 0:
                yr, x, y, iso = norm_year(a), b, c, True
            else:
                yr, x, y, iso = norm_year(c), a, b, False
            if yr is not None and 1 <= x <= 31 and 1 <= y <= 31 and min(x, y) <= 12:
                if iso:
                    parts = (y, x, yr) if x <= 12 else (x, y, yr)
                else:
                    parts = (x, y, yr, True)
                end = num_m.end()

    if parts is None:
        fixed = re.sub(r"^[ODCQ](?=\d)", "0", sub)
        for pat, build in (
            (r"^([0-3]\d)[/\-.]([01]\d)(\d{2})(?=\s|$|\d{2})", lambda m: (int(m[1]), int(m[2]), int(m[3]))),
            (r"^([0-3]\d)([01]\d)[/\-.](\d{2})(?=\s|$|\d{2})", lambda m: (int(m[1]), int(m[2]), int(m[3]))),
            (r"^([0-3]\d)[/\-.]1(\d{2})(?=\s|$|\d{2})", lambda m: (int(m[1]), 10 + int(m[2][0]), int(m[2]))),
            (r"^([0-3]\d)([01]\d)(\d{2})(?=\s+\d|\d{6})", lambda m: (int(m[1]), int(m[2]), int(m[3]))),
            (r"^([0-3]\d)([1-9])(\d{2})(?=\d{6})", lambda m: (int(m[1]), int(m[2]), int(m[3]))),
        ):
            m = re.match(pat, fixed)
            if m:
                x, y, c = build(m)
                yr = norm_year(c)
                if yr is not None and 1 <= x <= 31 and 1 <= y <= 31 and min(x, y) <= 12:
                    parts, end = (x, y, yr, True), m.end()
                    break
        if parts is None:
            return None
    time_m = TIME_RE.match(sub[end:])
    if time_m:
        end += time_m.end()
    if split_at is not None and end > split_at:
        end -= 1
    return parts, lead + wk_len + end


def hint_direction(description: str) -> str | None:
    text = description.upper()
    if re.search(r"\b(?:KR|CR)\b", text):
        return "credit"
    debit = bool(DEBIT_HINT_RE.search(text))
    credit = bool(CREDIT_HINT_RE.search(text))
    if debit and not credit:
        return "debit"
    if credit and not debit:
        return "credit"
    return None


def explicit_direction(text: str) -> str | None:
    tokens = re.findall(r"[A-Z]+", text.upper())
    if any(t in {"CR", "CREDIT"} for t in tokens):
        return "credit"
    if any(t in {"DB", "DEBIT"} for t in tokens):
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
    if any(t in {"DB", "DR", "D", "DEBIT", "DEBET", "OUT", "KELUAR"} for t in tokens):
        return "debit"
    if any(t in {"CR", "C", "K", "CREDIT", "KREDIT", "IN", "MASUK"} for t in tokens):
        return "credit"
    return None


def line_direction(cells: Sequence[Cell]) -> str | None:
    for cell in cells:
        if cell.money:
            amount = parse_amount(cell.text)
            if amount and amount.direction:
                return amount.direction
        direction = marker_direction(cell.text)
        if direction:
            return direction
    return None
