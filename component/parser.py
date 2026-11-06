import calendar
from datetime import date
import re
from decimal import Decimal, InvalidOperation
from typing import Sequence

from .constants import (
    ACCOUNT_RE,
    ALPHA_DATE_RE,
    CREDIT_HINT_RE,
    DEBIT_HINT_RE,
    EXTRACTION_CONFIDENCE_CONFLICT,
    GLUED_TIME_RE,
    INT_RE,
    MONEY_RE,
    MONTH_ALT,
    MONTH_MAP,
    MONTHS,
    NUM_DATE_RE,
    PURE_TIME_RE,
    TIME_RE,
    TRAILING_TIMESTAMP_RE,
    WEEKDAY_RE,
)
from .structures import Amount, Cell, PageFingerprint, Word

STRICT_ID_DECIMAL_RE = re.compile(r"^\d{1,3}(?:\.\d{3})+,\d{1,2}$")
STRICT_INTL_DECIMAL_RE = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d{1,3},\d{6})\.\d{1,2}$")
STRICT_SINGLE_DEC_RE = re.compile(r"^\d+[.,]\d{1,2}$")
STRICT_THOUSANDS_ONLY_RE = re.compile(r"^\d{1,4}(?:([.,])\d{3})+(?:\1\d{1,2})?$")
STRICT_PLAIN_INT_RE = re.compile(r"^\d{1,15}$")

PAGE_NUM_RE = re.compile(
    r"\b(?:HALAMAN|HAL\.?|PAGE|PG\.?)\s*[:.]?\s*(\d{1,4})(?:\s*(?:DARI|OF|/|-)\s*\d{1,4})?\b",
    re.IGNORECASE,
)

PERIOD_RANGE_NUM_RE = re.compile(
    r"(\d{1,2})[/\-.](\d{1,2})[/\-.]((?:19|20)\d{2}|\d{2})\s*(?:-|S[/.]?D\.?|SAMPAI(?:\s+DENGAN)?|TO)\s*"
    r"(\d{1,2})[/\-.](\d{1,2})[/\-.]((?:19|20)\d{2}|\d{2})",
    re.IGNORECASE,
)

PERIOD_RANGE_ALPHA_RE = re.compile(
    rf"(\d{{1,2}})[\s/\-.]+({MONTH_ALT})[\s/\-.,]+((?:19|20)\d{{2}}|\d{{2}})?\s*"
    rf"(?:-|S[/.]?D\.?|SAMPAI(?:\s+DENGAN)?|TO)\s*"
    rf"(\d{{1,2}})[\s/\-.]+({MONTH_ALT})[\s/\-.,]+((?:19|20)\d{{2}}|\d{{2}})",
    re.IGNORECASE,
)

PERIOD_MONTH_YEAR_RE = re.compile(
    rf"(?:PERIODE|PERIOD|BULAN|STATEMENT\s+DATE|TANGGAL\s+LAPORAN)?[\s:.\-]*"
    rf"\b({MONTH_ALT})\b[\s,/\-.]+((?:19|20)\d{{2}})\b",
    re.IGNORECASE,
)

BANKING_NOISE_RE = re.compile(
    r"\b(?:TRSF\s+E-?BANKING(?:\s+(?:DB|CR))?|TRANSFER\s+E-?BANKING|BI-?FAST(?:\s+(?:DB|CR))?|"
    r"SWITCHING(?:\s+(?:DB|CR))?|MCM\s+INHOUSE\s+TRF|OVERBOOKING|OB\s+|"
    r"KARTU\s+DEBIT|QRIS|EDC|ATM|TANGGAL\s*[:.]?\s*\d{1,2}[/\-]\d{1,2}|"
    r"\d{2}[/\-]\d{2}(?:[/\-]\d{2,4})?|\d{6,})\b",
    re.IGNORECASE,
)

COUNTERPARTY_MARKER_RE = re.compile(
    r"\b(?:TRANSFER\s+DARI|TRSF\s+DARI|TRF\s+DARI|TRANSFER\s+KE|TRSF\s+KE|TRF\s+KE|"
    r"KEPADA|DARI|TO|FROM|Bapak|Ibu|Sdr|PT\.?|CV\.?|UD\.?|YAYASAN)\s+([A-Z][A-Z0-9 .,&'\-]{2,45})",
    re.IGNORECASE,
)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def norm_money(text: str) -> str:
    cleaned = re.sub(r"\s+", "", str(text).upper())
    cleaned = re.sub(r"^([+\-]?)(?:RP\.?|IDR)([+\-]?)", lambda m: m.group(1) or m.group(2), cleaned)
    cleaned = re.sub(r"([.,])(?:[DCO][O0]{1,2}|0[O][O0]?)([DKC]?)$", r"\g<1>00\2", cleaned)
    cleaned = re.sub(r"(\.[0]{2})[XM4]$", r"\1K", cleaned)
    return "0.00" if cleaned == "00.0" else cleaned


def has_alpha_contamination(text: str) -> bool:
    if not text:
        return False
    compact = re.sub(r"\s+", "", str(text).upper())
    compact = re.sub(r"^[+\-(]*(?:RP\.?|IDR)?", "", compact)
    compact = re.sub(r"^[+\-(]+|[)\-+]+$", "", compact)
    compact = re.sub(r"(?:DB|DR|CR|D|K|C)$", "", compact)
    if not re.search(r"\d{1,3}[.,]\d{2,3}", compact):
        return False
    return bool(re.search(r"[A-Z]", compact))


def is_money(text: str) -> bool:
    if has_alpha_contamination(text):
        return False
    return bool(MONEY_RE.match(norm_money(text)))


def is_int_amount(text: str) -> bool:
    if has_alpha_contamination(text):
        return False
    return bool(INT_RE.match(norm_money(text)))


def normalize_financial_float(num_str: str) -> float | None:
    num = re.sub(r"^[+\-\s]+", "", num_str.strip()).rstrip(".,")
    if not num:
        return None
    has_dot = "." in num
    has_comma = "," in num

    if has_dot and has_comma:
        if STRICT_ID_DECIMAL_RE.match(num):
            normalized = num.replace(".", "").replace(",", ".")
        elif STRICT_INTL_DECIMAL_RE.match(num):
            normalized = num.replace(",", "")
        else:
            decimal_sep = "," if num.rfind(",") > num.rfind(".") else "."
            thousands_sep = "." if decimal_sep == "," else ","
            parts = num.split(decimal_sep)
            if len(parts) < 2:
                return None
            head = "".join(parts[:-1]).replace(thousands_sep, "")
            tail = parts[-1]
            if not (head.isdigit() and tail.isdigit()):
                return None
            if len(tail) <= 2:
                normalized = f"{head}.{tail}"
            elif decimal_sep == "." and tail == "000":
                normalized = f"{head}.00"
            else:
                return None
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = num.split(sep)
        if not all(p.isdigit() for p in parts if p != ""):
            return None
        if len(parts) > 2:
            if len(parts[-1]) <= 2 and all(len(p) == 3 for p in parts[1:-1]):
                normalized = "".join(parts[:-1]) + "." + parts[-1]
            elif all(len(p) == 3 for p in parts[1:]):
                normalized = "".join(parts)
            else:
                return None
        elif len(parts) == 2:
            if len(parts[1]) == 3 and parts[0] not in ("0", "00") and len(parts[0]) <= 3:
                normalized = "".join(parts)
            elif len(parts[1]) <= 2:
                normalized = f"{parts[0]}.{parts[1]}"
            else:
                return None
        else:
            return None
    else:
        if not STRICT_PLAIN_INT_RE.match(num):
            return None
        normalized = num

    try:
        return float(Decimal(normalized))
    except (InvalidOperation, ValueError):
        return None


def validate_financial_grammar(text: str) -> tuple[bool, Amount | None, str | None]:
    if not text or not str(text).strip():
        return False, None, None
    if has_alpha_contamination(text):
        return False, None, EXTRACTION_CONFIDENCE_CONFLICT

    raw = re.sub(r"(?<=\d)\s+(?=[\d.,])", "", norm_money(text).strip(".,;:"))
    match = re.fullmatch(r"([+\-]?)(\()?(\d[\d.,]*)(\))?(DB|DR|CR|D|K|C|[+\-])?", raw)
    if not match:
        if re.search(r"\d", raw) and re.search(r"[A-Z]", raw):
            return False, None, EXTRACTION_CONFIDENCE_CONFLICT
        return False, None, None

    sign, lparen, num, rparen, suffix = match.groups()
    if bool(lparen) != bool(rparen) and lparen:
        return False, None, None

    auto_debit_from_ocr_tail = False
    if "." in num and "," in num:
        if num.rfind(".") > num.rfind(",") and num.endswith(".000") and not suffix:
            auto_debit_from_ocr_tail = True

    detected_amount = normalize_financial_float(num)
    if detected_amount is None:
        return False, None, EXTRACTION_CONFIDENCE_CONFLICT

    if auto_debit_from_ocr_tail and not suffix:
        suffix = "D"

    if sign == "-" or lparen or suffix in ("DB", "DR", "D", "-"):
        direction = "debit"
    elif sign == "+" or suffix in ("CR", "C", "K", "+"):
        direction = "credit"
    else:
        direction = None
    return True, Amount(detected_amount, direction), None


def parse_amount(text: str) -> Amount | None:
    ok, amount, _ = validate_financial_grammar(text)
    if ok and amount is not None:
        return amount
    return None


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


def norm_year(year: int | None) -> int | None:
    if year is None:
        return None
    if year < 100:
        return (1900 + year) if year >= 70 else (2000 + year)
    return year if 1900 <= year <= 2100 else None


def parse_printed_page_number(text: str) -> int | None:
    if not text:
        return None
    matches = list(PAGE_NUM_RE.finditer(text))
    if not matches:
        return None
    try:
        val = int(matches[-1].group(1))
        return val if 1 <= val <= 5000 else None
    except ValueError:
        return None


def parse_statement_period(text: str) -> tuple[date | None, date | None, int | None, int | None]:
    if not text:
        return None, None, None, None

    m_num = PERIOD_RANGE_NUM_RE.search(text)
    if m_num:
        d1, mo1, y1_raw, d2, mo2, y2_raw = m_num.groups()
        y1 = norm_year(int(y1_raw))
        y2 = norm_year(int(y2_raw))
        m1, day1 = int(mo1), int(d1)
        m2, day2 = int(mo2), int(d2)
        if m1 > 12 and day1 <= 12:
            day1, m1 = m1, day1
        if m2 > 12 and day2 <= 12:
            day2, m2 = m2, day2
        try:
            start_dt = date(y1, m1, day1) if y1 else None
            end_dt = date(y2, m2, day2) if y2 else None
            if start_dt:
                return start_dt, end_dt, start_dt.month, start_dt.year
        except ValueError:
            pass

    m_alpha = PERIOD_RANGE_ALPHA_RE.search(text)
    if m_alpha:
        d1, mon1_s, y1_raw, d2, mon2_s, y2_raw = m_alpha.groups()
        y2 = norm_year(int(y2_raw))
        y1 = norm_year(int(y1_raw)) if y1_raw else y2
        m1 = MONTHS.get(mon1_s.lower())
        m2 = MONTHS.get(mon2_s.lower())
        if y1 and m1:
            try:
                start_dt = date(y1, m1, int(d1))
                end_dt = date(y2, m2, int(d2)) if (y2 and m2) else None
                return start_dt, end_dt, start_dt.month, start_dt.year
            except ValueError:
                pass

    m_my = PERIOD_MONTH_YEAR_RE.search(text)
    if m_my:
        mon_s, yr_s = m_my.groups()
        mo = MONTHS.get(mon_s.lower())
        yr = norm_year(int(yr_s))
        if mo and yr:
            last_day = calendar.monthrange(yr, mo)[1]
            return date(yr, mo, 1), date(yr, mo, last_day), mo, yr

    return None, None, None, None


def extract_page_fingerprint(
    page_text: str,
    header_text: str,
    pdf_page_index: int,
    fallback_account: str = "",
    fallback_bank: str = "",
) -> PageFingerprint:
    combined_header = f"{header_text}\n{page_text}"
    acc = detect_account(header_text) or fallback_account or "UNKNOWN_ACCOUNT"
    start_dt, end_dt, st_month, st_year = parse_statement_period(combined_header)
    printed_page = parse_printed_page_number(page_text)
    return PageFingerprint(
        pdf_page_index=pdf_page_index,
        account_number=acc,
        bank_name=fallback_bank,
        statement_period_start=start_dt,
        statement_period_end=end_dt,
        statement_month=st_month,
        statement_year=st_year,
        printed_page_number=printed_page,
    )


def extract_clean_description(description: str) -> str:
    if not description:
        return ""
    cleaned = BANKING_NOISE_RE.sub(" ", description)
    cleaned = re.sub(r"\b[0-9A-Z]*\d{4,}[0-9A-Z]*\b", " ", cleaned)
    cleaned = re.sub(r"[:;/\\|_]+", " ", cleaned)
    return clean_text(cleaned)


def extract_counterparty_name(description: str) -> str:
    if not description:
        return ""
    match = COUNTERPARTY_MARKER_RE.search(description)
    if match:
        candidate = clean_text(match.group(1))
        candidate = re.split(r"\b(?:REF|TGL|TANGGAL|ID|NO|NOMOR|RP|IDR|\d{4,})\b", candidate)[0]
        return clean_text(candidate).strip(" -.,:/")
    clean_desc = extract_clean_description(description)
    tokens = [
        t for t in clean_desc.split()
        if len(t) >= 2 and t.isalpha() and t.upper() not in {
            "TRSF", "TRF", "TRANSFER", "DEBIT", "KREDIT", "CREDIT", "SALDO", "AWAL",
            "BIAYA", "ADMIN", "ADM", "BUNGA", "PAJAK", "TARIK", "TUNAI", "SETOR",
            "PEMBAYARAN", "PEMBELIAN", "DARI", "KE", "VIA", "BANK", "Bapak", "Ibu",
        }
    ]
    if len(tokens) >= 2:
        return " ".join(tokens[-3:])
    return ""


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
        has_time = bool(re.match(r"^(?:[01]?\d|2[0-3])[:.][0-5]\d", tail))
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
        month = MONTH_MAP.get(mon_s.upper()[:3], MONTHS.get(mon_s.lower()))
        if month is not None:
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
                if 1 <= a <= 31 and 1 <= b <= 12:
                    parts, end = (a, b, None), num_m.end()
                elif 1 <= b <= 31 and 1 <= a <= 12:
                    parts, end = (b, a, None), num_m.end()
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
                    day, month = (x, y) if y <= 12 else (y, x)
                    parts = (day, month, yr)
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
                    day, month = (x, y) if y <= 12 else (y, x)
                    parts, end = (day, month, yr), m.end()
                    break
        if parts is None:
            return None
    while True:
        time_m = TIME_RE.match(sub[end:]) or TRAILING_TIMESTAMP_RE.match(sub[end:])
        if not time_m:
            break
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
    if any(t in {"DB", "DR", "DEBIT"} for t in tokens):
        return "debit"
    return None


def description_direction(description: str) -> str | None:
    text = description.upper()
    if re.search(r"\b(KR|CR|KREDIT|CREDIT|TRANSFER DARI|TRSF DARI|TRF DARI|TRANSFER IN|TRSF IN)\b", text):
        return "credit"
    if re.search(r"\b(TARIK|TARIKAN|PENARIKAN|TRANSAKSI|DEBIT|DB|TRANSFER KE|TRF KE|TRSF KE|TRSF|TRF)\b", text):
        return "debit"
    return None


def marker_direction(text: str) -> str | None:
    tokens = re.findall(r"[A-Z]+|[+\-]", text.upper())
    if any(t in {"DB", "DR", "D", "DEBIT", "DEBET", "OUT", "KELUAR", "-"} for t in tokens):
        return "debit"
    if any(t in {"CR", "C", "K", "CREDIT", "KREDIT", "IN", "MASUK", "+"} for t in tokens):
        return "credit"
    return None


def line_direction(cells: Sequence[Cell]) -> str | None:
    for cell in cells:
        if cell.money:
            amount = parse_amount(cell.text)
            if amount and amount.direction:
                return amount.direction
        if len(cell.text.strip()) <= 6:
            direction = marker_direction(cell.text)
            if direction:
                return direction
    return None
