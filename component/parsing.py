from __future__ import annotations

import calendar
from decimal import Decimal, InvalidOperation
import re
from datetime import date, datetime
from typing import Any, Optional, Sequence

from component.core import (
    ACCOUNT_RE,
    ALPHA_DATE_RE,
    Amount,
    BANKING_NOISE_RE,
    CATEGORY_RES,
    CREDIT_HINT_RE,
    Cell,
    DEBIT_HINT_RE,
    DIRECTION_MARKER_RE,
    EXTRACTION_CONFIDENCE_CONFLICT,
    GLUED_TIME_RE,
    INT_RE,
    MONEY_RE,
    MONTH_MAP,
    MONTHS,
    NON_BANK_HEADER_WORDS,
    NUM_DATE_RE,
    PAGE_NUM_RE,
    PERIOD_MONTH_YEAR_RE,
    PERIOD_RANGE_ALPHA_RE,
    PERIOD_RANGE_NUM_RE,
    PURE_TIME_RE,
    PageFingerprint,
    STRICT_ID_DECIMAL_RE,
    STRICT_INTL_DECIMAL_RE,
    STRICT_PLAIN_INT_RE,
    STRICT_SINGLE_DEC_RE,
    STRICT_THOUSANDS_ONLY_RE,
    TIME_RE,
    TRAILING_TIMESTAMP_RE,
    WEEKDAY_RE,
    Word,
    band_words,
)


# ---------------------------------------------------------------------------
# Money Parsing & Normalization
# ---------------------------------------------------------------------------


def norm_money(text: str) -> str:
    cleaned = re.sub(r"\s+", "", str(text).upper())
    cleaned = re.sub(r"^([+\-]?)(?:RP\.?|IDR)([+\-]?)", lambda m: m.group(1) or m.group(2), cleaned)
    cleaned = re.sub(r"^([+\-]?)[DCO](?=[.,]\d{2}(?:[DKC]|$))", r"\g<1>0", cleaned)
    cleaned = re.sub(r"(?<=[\d.,])[OQ](?=[\d.,])", "0", cleaned)
    cleaned = re.sub(r"([.,])(?:[DCO][O0]{1,2}|0[O][O0]?)([DKC]?)$", r"\g<1>00\2", cleaned)
    cleaned = re.sub(r"(\.[0]{2})[XM4]$", r"\1K", cleaned)
    return "0.00" if cleaned == "00.0" else cleaned


def has_alpha_contamination(text: str) -> bool:
    if not text:
        return False
    norm = norm_money(text)
    if MONEY_RE.match(norm) or INT_RE.match(norm):
        return False
    compact = re.sub(r"^[+\-(]*(?:RP\.?|IDR)?", "", norm)
    compact = re.sub(r"^[+\-(]+|[)\-+]+$", "", compact)
    compact = re.sub(r"(?:DB|DR|CR|D|K|C)$", "", compact)
    if not re.search(r"\d{1,3}[.,]\d{2,3}", compact):
        return False
    return bool(re.search(r"[A-Z]", compact))


def is_money(text: str) -> bool:
    return bool(MONEY_RE.match(norm_money(text)))


def is_int_amount(text: str) -> bool:
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
        if not STRICT_PLAIN_INT_RE.match(num) or (len(num) > 1 and num.startswith("0")):
            return None
        normalized = num

    try:
        return float(Decimal(normalized))
    except (InvalidOperation, ValueError):
        return None


def validate_financial_grammar(text: str) -> tuple[bool, Amount | None, str | None]:
    if not text or not str(text).strip():
        return False, None, None
    contaminated = has_alpha_contamination(text)
    conflict_flag = EXTRACTION_CONFIDENCE_CONFLICT if contaminated else None

    raw = re.sub(r"(?<=\d)\s+(?=[\d.,])", "", norm_money(text).strip(".,;:"))
    match = re.fullmatch(r"([+\-]?)(\()?(\d[\d.,]*)(\))?(DB|DR|CR|D|K|C|[+\-])?", raw)
    if not match and contaminated:
        stripped = re.sub(r"[A-Z]+", "", raw)
        match = re.fullmatch(r"([+\-]?)(\()?(\d[\d.,]*)(\))?(DB|DR|CR|D|K|C|[+\-])?", stripped)
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
    return True, Amount(detected_amount, direction), conflict_flag


def parse_amount(text: str) -> Amount | None:
    ok, amount, _ = validate_financial_grammar(text)
    if ok and amount is not None:
        return amount
    return None


# ---------------------------------------------------------------------------
# Temporal Parsing
# ---------------------------------------------------------------------------


TIME_NORM_OB_RE = re.compile(r"(?<=\d)[OB](?=[:.\d]|$)|(?<=[:.\d])[OB](?=\d)|^[OB](?=\d[:.])")
LEADING_TIME_NORM_OB_RE = re.compile(r"(?<=\d)[OB](?=[:.\d]|\b|$)|(?<=[:.\d])[OB](?=\d)|^[OB](?=\d[:.])")


def normalize_time_ob(text: str, with_word_boundary: bool = False) -> str:
    """Normalize OCR misread characters (O/B -> 0/8) in timestamp strings."""
    pat = LEADING_TIME_NORM_OB_RE if with_word_boundary else TIME_NORM_OB_RE
    return pat.sub(lambda m: "0" if m.group(0).upper() == "O" else "8", text)


def parse_time(text: str) -> tuple[int, int, int] | None:
    cleaned = normalize_time_ob(text.strip(), with_word_boundary=False)
    match = PURE_TIME_RE.match(cleaned)
    if not match and (":" in cleaned or "." in cleaned):
        m_rel = TIME_RE.match(cleaned)
        if m_rel and m_rel.group(1) is not None:
            match = m_rel
    if not match:
        return None
    groups = match.groups()
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
    if 2100 < year < 3000 and (year // 100) in (26, 28, 29):
        year = 2000 + (year % 100)
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


def match_date(text: str) -> tuple[tuple, int] | None:
    raw = text.strip()
    lead = len(text) - len(text.lstrip())
    wk = WEEKDAY_RE.match(raw)
    wk_len = wk.end() if wk else 0
    sub = raw[wk_len:]

    idx_m = re.match(
        r"^\d{1,3}(?=\d{2}-(?:JAN|FEB|MAR|APR|MAY|MEI|JUN|JUL|AUG|AGU|SEP|OCT|OKT|NOV|DEC|DES)-\d{4})",
        sub,
        re.IGNORECASE,
    )
    idx_len = idx_m.end() if idx_m else 0
    if idx_len:
        sub = sub[idx_len:]

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
    sub_norm = re.sub(r"^[ODCQ](?=\d)", "0", sub)
    sub_norm = re.sub(r"(?<=\b\d{4}),(?=\s|$)|(?<=\b\d{2}),(?=\s|$)", " ", sub_norm)
    alpha_m = ALPHA_DATE_RE.match(sub_norm)
    num_m = None if alpha_m else NUM_DATE_RE.match(sub_norm)

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
        m_iso8 = re.match(r"^((?:19|20)\d{2})(\d{2})(\d{2})(?![\d,])(?!\.\d)", fixed)
        if m_iso8:
            yr_v, mo_v, day_v = int(m_iso8.group(1)), int(m_iso8.group(2)), int(m_iso8.group(3))
            if 1990 <= yr_v <= 2099:
                try:
                    date(yr_v, mo_v, day_v)
                    parts, end = (day_v, mo_v, yr_v), m_iso8.end()
                except ValueError:
                    pass
        if parts is None:
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
            m_trunc = re.match(
                r"^([0-3]\d)[\s/\-.]+([A-Za-z]{2,4})[\s/\-.,]+((?:19|2\d)\d{2}|\d{2})(?=\b|[.,\s]|$)",
                fixed,
            )
            if m_trunc:
                day_v = int(m_trunc.group(1))
                mon_tok = m_trunc.group(2).lower()
                yr_v = norm_year(int(m_trunc.group(3)))
                trunc_map = {
                    "an": 1, "ja": 1,
                    "eb": 2, "fe": 2,
                    "ar": 3, "ma": 3,
                    "pr": 4, "ap": 4,
                    "ei": 5, "me": 5,
                    "un": 6, "ju": 6,
                    "ul": 7,
                    "ug": 8, "ag": 8, "ao": 8, "au": 8,
                    "ep": 9, "se": 9,
                    "ct": 10, "ok": 10, "oc": 10,
                    "ov": 11, "no": 11,
                    "ec": 12, "de": 12,
                }
                mon_v = MONTH_MAP.get(mon_tok.upper()[:3], MONTHS.get(mon_tok, trunc_map.get(mon_tok)))
                if mon_v is not None and 1 <= day_v <= 31 and yr_v is not None:
                    parts, end = (day_v, mon_v, yr_v), m_trunc.end()
        if parts is None:
            return None
    while end < len(sub) and sub[end] in ",.":
        end += 1
    while True:
        time_m = TIME_RE.match(sub[end:]) or TRAILING_TIMESTAMP_RE.match(sub[end:])
        if not time_m:
            break
        end += time_m.end()
    if split_at is not None and end > split_at:
        end -= 1
    return parts, lead + wk_len + idx_len + end


# ---------------------------------------------------------------------------
# Text Extraction & Cleansing
# ---------------------------------------------------------------------------


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def extract_clean_description(description: str) -> str:
    if not description:
        return ""
    cleaned = BANKING_NOISE_RE.sub(" ", description)
    cleaned = re.sub(r"\b[0-9A-Z]*\d{4,}[0-9A-Z]*\b", " ", cleaned)
    cleaned = re.sub(r"[:;/\\|_]+", " ", cleaned)
    return clean_text(cleaned)


def extract_counterparty_name(description: str) -> str:
    return ""


# ---------------------------------------------------------------------------
# Direction Inference
# ---------------------------------------------------------------------------


_BUNGA_DEBIT_QUALIFIER_RE = re.compile(
    r"\b(?:PAJAK|BIAYA|KOREKSI|DENDA|PINJAMAN|PAYMENT|LOAN)\b"
)
_TRF_DR_KE_RE = re.compile(r"\b(?:BIF\s+)?(?:TRANSFER|TRF)\s+(DR|KE)\b")


def _leading_tx_type_direction(text: str) -> str | None:
    """Resolve explicit CR/KR vs DB/DR or TRANSFER DR/KE in the leading transaction-type segment."""
    s = (text or "").strip().upper()
    if not s:
        return None
    m_dt = match_date(s)
    if m_dt:
        s = s[m_dt[1]:].lstrip()
    m_tm = TIME_RE.match(s)
    if m_tm and m_tm.group(1) is not None:
        s = s[m_tm.end():].lstrip()
    if not s:
        return None
    lead_seg = re.split(
        r"\b(?:TANGGAL|TGL|REF|REFF|NO\.?\s*REF|MID|TID|DDR|BERITA|KETERANGAN|REMARK)\b|"
        r"[:;]|(?<!\d)\d{4,}|\d{1,2}[/\-.]\d{1,2}|\d+[.,]\d{2}",
        s,
        maxsplit=1,
    )[0].strip()
    if not lead_seg:
        return None
    lead_seg = " ".join(lead_seg.split()[:6])
    m_trf = _TRF_DR_KE_RE.search(lead_seg)
    m_code = re.search(r"\b(CR|KR|DB|DR)\b", lead_seg)
    if m_trf and (not m_code or m_trf.start() <= m_code.start()):
        return "credit" if m_trf.group(1) == "DR" else "debit"
    if m_code:
        return "credit" if m_code.group(1) in ("CR", "KR") else "debit"
    return None


def _prepare_direction_text(text: str) -> tuple[str, str | None]:
    """Return cleaned uppercase text (DDR: stripped) and any TRANSFER DR/KE match direction."""
    clean_u = re.sub(r"\bDDR\s*:", " ", (text or "").upper())
    m_trf = _TRF_DR_KE_RE.search(clean_u)
    trf_dir = ("credit" if m_trf.group(1) == "DR" else "debit") if m_trf else None
    return clean_u, trf_dir


def hint_direction(description: str) -> str | None:
    lead_dir = _leading_tx_type_direction(description)
    if lead_dir:
        return lead_dir
    text, trf_dir = _prepare_direction_text(description)
    if trf_dir:
        return trf_dir
    if re.search(r"\b(?:KR|CR)\b", text):
        return "credit"
    if re.search(r"\bBUNGA\b", text) and not _BUNGA_DEBIT_QUALIFIER_RE.search(text):
        return "credit"
    debit = bool(DEBIT_HINT_RE.search(text))
    credit = bool(CREDIT_HINT_RE.search(text))
    if debit and not credit:
        return "debit"
    if credit and not debit:
        return "credit"
    return None


def explicit_direction(text: str) -> str | None:
    lead_dir = _leading_tx_type_direction(text)
    if lead_dir:
        return lead_dir
    clean_u, trf_dir = _prepare_direction_text(text)
    if trf_dir:
        return trf_dir
    tokens = re.findall(r"[A-Z]+", clean_u)
    if any(t in {"CR", "KR"} for t in tokens):
        return "credit"
    if any(t in {"DB", "DR"} for t in tokens):
        return "debit"
    if any(t == "CREDIT" for t in tokens):
        return "credit"
    if any(t == "DEBIT" for t in tokens):
        return "debit"
    return None


def description_direction(description: str) -> str | None:
    lead_dir = _leading_tx_type_direction(description)
    if lead_dir:
        return lead_dir
    text, trf_dir = _prepare_direction_text(description)
    if trf_dir:
        return trf_dir
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


def line_direction(cells: Sequence[Any], amount_parser: Any = None) -> str | None:
    for cell in cells:
        if getattr(cell, "money", False):
            parser_fn = amount_parser if amount_parser is not None else parse_amount
            amount = parser_fn(cell.text)
            if amount and getattr(amount, "direction", None):
                return amount.direction
        text = getattr(cell, "text", "") or ""
        if len(text.strip()) <= 6:
            direction = marker_direction(text)
            if direction:
                return direction
    return None


# ---------------------------------------------------------------------------
# Account & Bank Identity
# ---------------------------------------------------------------------------


_NON_BANK_HEADER_WORDS = NON_BANK_HEADER_WORDS


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
    curr_acct = re.search(r"\b(?:IDR|USD|SGD|EUR|CA|SA)\s*[-:]?\s*(\d{8,20})\b", ascii_text, re.I)
    if curr_acct:
        return curr_acct.group(1)
    return None


def detect_bank(words: Sequence[Word], is_ocr: bool = False) -> str:
    if not words:
        return ""
    alpha = [w for w in words if len(re.sub(r"[^A-Za-z]", "", w.text)) >= 2]
    if not alpha:
        return ""

    heights = [w.h for w in alpha]
    med_h = sorted(heights)[len(heights) // 2] if heights else 10.0

    raw_lines = band_words(words, lambda w: 0.6 * max(med_h, w.h))
    lines: list[list[Word]] = []
    for rln in raw_lines:
        rln.sort(key=lambda x: x.x0)
        rln_upper = clean_text(" ".join(w.text for w in rln)).upper()
        if re.search(r"\b(LAPORAN\s+REKENING|REKENING\s+KORAN|MUTASI\s+REKENING)\b", rln_upper) and not any(
            "BANK" in w.text.upper() for w in rln
        ):
            continue
        cur_seg: list[Word] = []
        for w in rln:
            if cur_seg and (w.x0 - cur_seg[-1].x1) > 2.5 * max(med_h, w.h, cur_seg[-1].h):
                lines.append(cur_seg)
                cur_seg = []
            cur_seg.append(w)
        if cur_seg:
            lines.append(cur_seg)

    full_joined = " ".join(clean_text(" ".join(w.text for w in ln)) for ln in lines)
    ojk_m = re.search(
        r"\bPT\.?\s+(BANK\s+[A-Z][A-Z\s]{2,40}?)(?:\s*,?\s*TBK|\s+TERDAFTAR|\s+DIAWASI|\s+PERSERO)",
        full_joined.upper(),
    )
    if ojk_m:
        return clean_text(ojk_m.group(1)).upper()

    kv_m = re.search(
        r"(?:\[\s*(?:BANK\s+NAME|NAMA\s+BANK)\s*\]|\b(?:BANK\s+NAME|NAMA\s+BANK)\s*[:\-])\s*([A-Z0-9][A-Z0-9\s.&\-]{1,30}?)(?=\s*(?:\[|\b(?:ACCOUNT|NO|NOMOR|CURRENCY|MATA|BRANCH|CABANG|PERIOD|PERIODE|DATE|TANGGAL)\b|$))",
        full_joined.upper(),
    )
    if kv_m:
        kv_val = clean_text(kv_m.group(1)).upper()
        if kv_val and kv_val not in _NON_BANK_HEADER_WORDS and kv_val != "BANK":
            return kv_val

    for ln in raw_lines:
        tokens = [re.sub(r"[^A-Za-z0-9]", "", w.text) for w in ln]
        tokens = [t for t in tokens if t]
        for idx, tok in enumerate(tokens[:-1]):
            if tok.lower() == "by" and idx + 1 < len(tokens):
                after = tokens[idx + 1]
                if len(after) >= 2 and after.upper() not in _NON_BANK_HEADER_WORDS:
                    return after.upper()

    meta_floor_y = float("inf")
    for ln in lines:
        ln_text = " ".join(w.text for w in ln)
        ln_upper = ln_text.upper()
        if re.search(
            r"\b(ACCOUNT\s+STATEMENT\s+SUMMARY|ACCOUNT\s+NO|NO\.?\s*REKENING|PERIODE\s+LAPORAN|"
            r"RINGKASAN\s+TRANSAKSI|SALDO\s+BULAN\s+LALU|OPENING\s+BALANCE)\b",
            ln_upper,
        ):
            meta_floor_y = min(meta_floor_y, min(w.y0 for w in ln))

    candidate_words: list[Word] = []
    for ln in lines:
        ln_y0 = min(w.y0 for w in ln)
        if ln_y0 >= meta_floor_y - 2.0:
            continue
        ln_text = clean_text(" ".join(w.text for w in ln))
        ln_upper = ln_text.upper()
        if any(is_money(w.text) for w in ln) or match_date(ln_text) is not None:
            continue
        if re.search(r"\b(KCP|KCU|CABANG|BRANCH|UNIT\s+KERJA)\b", ln_upper):
            continue
        if re.search(r"\b(CREATED|PERIODE|PERIOD|HALAMAN|PAGE|JL\.?|JALAN|DESA|KEL\.?|KEC\.?|SELALU\s+GUNAKAN|BUKA\s+REKENING)\b", ln_upper):
            continue
        if re.search(r"\b(LAPORAN\s+REKENING|REKENING\s+KORAN|ACCOUNT\s+STATEMENT|MUTASI\s+REKENING)\b", ln_upper):
            remaining = [
                w for w in ln
                if re.sub(r"[^A-Za-z]", "", w.text).upper() not in _NON_BANK_HEADER_WORDS
                and len(re.sub(r"[^A-Za-z]", "", w.text)) >= 2
            ]
            if not any("BANK" in w.text.upper() for w in remaining):
                continue
            candidate_words.extend(w for w in remaining if "BANK" in w.text.upper())
            continue

        ln_alpha = [
            w for w in ln
            if len(re.sub(r"[^A-Za-z]", "", w.text)) >= 2
            and re.sub(r"[^A-Za-z]", "", w.text).upper() not in _NON_BANK_HEADER_WORDS
            and not re.search(r"\d{3,}", w.text)
        ]
        if not ln_alpha or len(ln_alpha) > 5:
            continue
        candidate_words.extend(ln_alpha)

    if not candidate_words:
        return ""

    bank_kw = [
        w for w in candidate_words
        if "BANK" in w.text.upper()
        and (
            not re.fullmatch(r"[\[(]*BANK[\]):]*", w.text.strip(), re.I)
            or any(
                o is not w
                and abs(o.yc - w.yc) < 0.6 * max(w.h, o.h)
                and re.sub(r"[^A-Za-z0-9]", "", o.text).upper() != "BANK"
                for o in candidate_words
            )
        )
    ]
    # If not OCR (e.g. digital text from PyMuPDF), do not guess from tallest arbitrary words!
    # Return empty so caller falls back to OCR on the header logo image.
    if not is_ocr and not bank_kw:
        return ""

    def _select_from_pool(pool_words: Sequence[Word]) -> str:
        tallest = max(pool_words, key=lambda w: w.h)
        same_line = [
            w for w in candidate_words
            if abs(w.yc - tallest.yc) < 0.6 * tallest.h
            and w.h >= 0.7 * tallest.h
            and abs(w.x0 - tallest.x0) < 6.0 * tallest.h
        ]
        same_line.sort(key=lambda w: w.x0)
        return clean_text(" ".join(re.sub(r"[^A-Za-z0-9]", "", w.text) for w in same_line)).upper()

    pool = bank_kw if bank_kw else candidate_words
    res = _select_from_pool(pool)
    if res == "BANK":
        non_bank = [
            w for w in candidate_words
            if re.sub(r"[^A-Za-z0-9]", "", w.text).upper() != "BANK"
        ]
        if not non_bank:
            return ""
        res = _select_from_pool(non_bank)
    if res == "OOCBC":
        return "OCBC"
    return "" if re.sub(r"\s+", "", res) in ("BYOND", "BYONDBY", "BYOND8Y") else res


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


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def months_between(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Computes month distance from (year1, month1) to (year2, month2): (b[0] - a[0]) * 12 + (b[1] - a[1])."""
    return (b[0] - a[0]) * 12 + (b[1] - a[1])


def row_month_pair(row: Any) -> tuple[int, int] | None:
    if isinstance(row.date, (date, datetime)):
        return (row.date.year, row.date.month)
    if getattr(row, "statement_year", None) and getattr(row, "statement_month", None):
        return (row.statement_year, row.statement_month)
    return None


def indonesia_date_key(row: Any, default_year: int = 0) -> tuple[int, int, int]:
    dt = getattr(row, "date", None)
    if isinstance(dt, (date, datetime)):
        return (dt.year, dt.month, dt.day)
    if isinstance(dt, str) and dt.strip():
        s = dt.strip()
        m_id = re.match(r"^(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?$", s)
        if m_id:
            day = int(m_id.group(1))
            month = int(m_id.group(2))
            if m_id.group(3):
                yr = int(m_id.group(3))
                if yr < 100:
                    yr = (1900 + yr) if yr >= 70 else (2000 + yr)
            else:
                yr = getattr(row, "statement_year", None) or default_year or 0
            return (yr, month, day)
        m_iso = re.match(r"^(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})$", s)
        if m_iso:
            return (int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))

        m_res = match_date(s.replace(".", "/"))
        if m_res:
            parts, _ = m_res
            if len(parts) >= 2 and parts[0] and parts[1]:
                day = int(parts[0])
                month = int(parts[1])
                yr = int(parts[2]) if (len(parts) >= 3 and parts[2]) else (getattr(row, "statement_year", None) or default_year or 0)
                return (yr, month, day)

    dp = getattr(row, "date_parts", None)
    if dp and len(dp) >= 2:
        day = int(dp[0] or 0)
        month = int(dp[1] or 0)
        yr = int(dp[2]) if (len(dp) >= 3 and dp[2]) else (getattr(row, "statement_year", None) or default_year or 0)
        if month:
            return (yr, month, day)

    sy = getattr(row, "statement_year", None) or default_year or 0
    sm = getattr(row, "statement_month", None) or 0
    return (sy, sm, 0)


def month_key(dt: Any) -> tuple[int, int] | None:
    if isinstance(dt, (date, datetime)):
        return (dt.year, dt.month)
    if isinstance(dt, str):
        match = re.match(r"^(\d{2})/(\d{2})(?:/(\d{4}))?$", dt)
        if match:
            return (int(match.group(3) or 0), int(match.group(2)))
    return None


# ---------------------------------------------------------------------------
# Categorization
# ---------------------------------------------------------------------------


def category(description: str, debit: float | None = None, credit: float | None = None) -> str:
    if not description:
        return "Uncategorized"

    text = description.upper()
    for name, pattern in CATEGORY_RES:
        if pattern.search(text):
            return name

    if debit and debit > 0:
        return "Transfer Out / Debit"
    if credit and credit > 0:
        return "Transfer In / Credit"
    return "Uncategorized"


def flow(debit: float | None, credit: float | None) -> str:
    has_debit = bool(debit and debit > 0)
    has_credit = bool(credit and credit > 0)
    if has_debit and has_credit:
        return "Both"
    if has_credit:
        return "Money in"
    if has_debit:
        return "Money out"
    return "Unknown"


def categorize_row(row: Any) -> str:
    desc = getattr(row, "description", "") or ""
    deb = getattr(row, "debit", None)
    crd = getattr(row, "credit", None)
    cat = category(desc, deb, crd)
    row.category = cat
    row.flow = flow(deb, crd)
    return cat


def categorize_rows(rows: Sequence[Any]) -> list[str]:
    return [categorize_row(r) for r in rows]


def calculate_category_breakdown(rows: Sequence[Any]) -> dict[str, dict[str, Any]]:
    cat_summary: dict[str, dict[str, Any]] = {}
    for r in rows:
        if getattr(r, "is_opening", False):
            continue
        desc = getattr(r, "description", "") or ""
        deb = float(getattr(r, "debit", 0.0) or 0.0)
        crd = float(getattr(r, "credit", 0.0) or 0.0)
        c_name = getattr(r, "category", None) or category(desc, deb, crd)
        if c_name not in cat_summary:
            cat_summary[c_name] = {"category": c_name, "count": 0, "debit": 0.0, "credit": 0.0, "net": 0.0}
        cat_summary[c_name]["count"] += 1
        cat_summary[c_name]["debit"] = round(cat_summary[c_name]["debit"] + deb, 2)
        cat_summary[c_name]["credit"] = round(cat_summary[c_name]["credit"] + crd, 2)
        cat_summary[c_name]["net"] = round(cat_summary[c_name]["credit"] - cat_summary[c_name]["debit"], 2)
    return cat_summary
