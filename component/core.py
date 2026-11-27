from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Sequence

import pandas as pd


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


AUDIT_TOL = float(os.getenv("BANK_OCR_AUDIT_TOL", "0.05"))
TOL = AUDIT_TOL

DEFAULT_DPI = int(os.getenv("BANK_OCR_DPI", "220"))
HEADER_DPI = int(os.getenv("BANK_OCR_HEADER_DPI", "180"))
HEADER_RATIO = float(os.getenv("BANK_OCR_HEADER_RATIO", "0.3"))
MAX_UPLOAD_MB = int(os.getenv("BANK_OCR_MAX_UPLOAD_MB", "50"))

EXTRACTION_CONFIDENCE_HIGH = "HIGH"
EXTRACTION_CONFIDENCE_MEDIUM = "MEDIUM"
EXTRACTION_CONFIDENCE_LOW = "LOW"
EXTRACTION_CONFIDENCE_CONFLICT = "EXTRACTION_CONFLICT"
EXTRACTION_CONFIDENCE_OVERLAID = "OVERLAID_TEXT"

SUSPICIOUS_PDF_EDITORS = (
    "canva",
    "acrobat",
    "sejda",
    "ilovepdf",
    "foxit",
    "nitro",
    "smallpdf",
    "pdfescape",
    "phantompdf",
    "libreoffice",
    "wps",
    "pdf24",
    "sodapdf",
    "master pdf",
    "inkscape",
    "illustrator",
    "coreldraw",
    'python'
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
SUPPORTED_INPUT_SUFFIXES = {".pdf"} | IMAGE_SUFFIXES
ALLOWED_EXTS = [ext.lstrip(".") for ext in sorted(SUPPORTED_INPUT_SUFFIXES)]

DETAIL_COLS = ["Tanggal", "Kredit", "Debit", "Saldo", "Deskripsi", "Pihak Terkait", "Kategori Transaksi"]
REVIEW_COLS = ["Halaman", "Issue", "Full detected text"]

MONTH_MAP = {
    "JAN": 1, "PEB": 2, "FEB": 2, "MAR": 3, "APR": 4, "MEI": 5, "MAY": 5,
    "JUN": 6, "JUL": 7, "AGU": 8, "AGT": 8, "AUG": 8, "SEP": 9,
    "OKT": 10, "OCT": 10, "NOP": 11, "NOV": 11, "DES": 12, "DEC": 12,
}

MONTHS = {
    "jan": 1, "januari": 1, "january": 1,
    "peb": 2, "feb": 2, "pebruari": 2, "februari": 2, "february": 2,
    "mar": 3, "maret": 3, "march": 3,
    "apr": 4, "april": 4,
    "mei": 5, "may": 5,
    "jun": 6, "juni": 6, "june": 6,
    "jul": 7, "juli": 7, "july": 7,
    "agu": 8, "agt": 8, "agustus": 8, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "okt": 10, "oktober": 10, "oct": 10, "october": 10,
    "nop": 11, "nov": 11, "nopember": 11, "november": 11,
    "des": 12, "desember": 12, "dec": 12, "december": 12,
}

MONTH_NAMES_ID = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
    7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember",
}

MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))

WEEKDAY_RE = re.compile(
    r"^(?:sen(?:in)?|sel(?:asa)?|rab(?:u)?|kam(?:is)?|jum(?:'?at)?|sab(?:tu)?|min(?:ggu)?|"
    r"mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)[,.\s]+",
    re.IGNORECASE,
)

GLUED_TIME_RE = re.compile(
    r"^(\d{1,4}[/\-.\s]*(?:\d{1,2}|[A-Za-z]{3,9})\.?[/\-.\s,]*((?:19|20)\d{2}|\d{2})?|"
    r"[A-Za-z]{3,9}\.?[\s/\-.,]*\d{1,2}(?:[\s/\-.,]+(?:(?:19|20)\d{2}|\d{2}))?)"
    r"([01]\d|2[0-3]|\d)(?=[:.][0-5]\d|\b)"
)

TRAILING_TIMESTAMP_RE = re.compile(
    r"\s+\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:\s*(?:AM|PM|WIB|WITA|WIT))?",
    re.IGNORECASE,
)

TIME_RE = re.compile(
    r"^(?:\s*([01]?\d|2[0-3])[:.]([0-5]\d)(?:[:.]([0-5]\d))?(?:\s*(AM|PM|WIB|WITA|WIT))?|\s+([01]\d|2[0-3])(?=\s*$))\b",
    re.IGNORECASE,
)

PURE_TIME_RE = re.compile(
    r"^([01]?\d|2[0-3])[:.]([0-5]\d)(?:[:.]([0-5]\d))?(?:\s*(AM|PM|WIB|WITA|WIT))?$",
    re.IGNORECASE,
)

ALPHA_DATE_RE = re.compile(
    rf"^(?:(\d{{1,4}})[\s/\-.,]*({MONTH_ALT})(?![A-Za-z])\.?(?:[\s/\-.,]*(\d{{1,4}}(?![\d:.])))?|"
    rf"({MONTH_ALT})(?![A-Za-z])\.?[\s/\-.,]*(\d{{1,4}}(?![\d:.]))(?:[\s/\-.,]+(\d{{1,4}}(?![\d:.])))?)(?![\d,])"
    r"(?:\s+\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:\s*(?:AM|PM|WIB|WITA|WIT))?)?",
    re.IGNORECASE,
)

NUM_DATE_RE = re.compile(
    r"^(\d{1,4})([/\-.])(\d{1,2})(?:\2(\d{1,4}))?(?![\d,])"
    r"(?:\s+\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:\s*(?:AM|PM|WIB|WITA|WIT))?)?",
    re.IGNORECASE,
)

ACCOUNT_RE = re.compile(
    r"(?:NO(?:MOR|\.)?\s*(?:REK(?:ENING|\.)?|ACC(?:OUNT|\.)?|AC\b)|"
    r"REK(?:ENING|\.)?(?:\s*(?:NO(?:MOR|\.)?|GIRO|TABUNGAN|KORAN|SIMPANAN|TAHAPAN|BRITAMA|SIMPEDES|TAPLUS))?|"
    r"ACC(?:OUNT|\.)?\s*(?:NO(?:MOR|\.)?|NUMBER|#)?|"
    r"A\s*/\s*C(?:\s*(?:NO(?:MOR|\.)?|NUMBER|#))?|"
    r"NOMOR\s+PRODUK)"
    r"(?:\s*/\s*(?:ACCOUNT\s*(?:NUMBER|NO)?|NO\.?\s*REK(?:ENING)?|A/C\s*(?:NO)?))?"
    r"(?:[\s:.\-/]*[A-Za-z]{0,12}){0,2}?[\s:.\-]*"
    r"(?:(?:IDR|USD|SGD|EUR|AUD|JPY|GBP|CNY|RP\.?)\s*)?"
    r"(\d[\d\-. ]{4,24}\d)",
    re.IGNORECASE,
)

OPENING_RE = re.compile(
    r"(?:^|[\s\W\d])(SALDO\s*AWAL|SAK\s*AWAL|OPENING\s*BALANCE|BEGINNING\s*BALANCE|SALDO\s*SEBELUMNYA|BALANCE\s*B/?F|BROUGHT\s*FORWARD)\b",
    re.IGNORECASE,
)

STOP_RE = re.compile(
    r"^\W*(TOTAL\s+(?:MUTASI|DEBET|DEBIT|KREDIT|CREDIT|SALDO|TRANSAKSI|SETORAN|PENARIKAN)\b|"
    r"JUMLAH\s+(?:MUTASI|DEBET|DEBIT|KREDIT|CREDIT)|"
    r"SALDO\s+AWAL|OPENING\s+BALANCE|HALAMAN\b|PAGE\b|BERSAMBUNG|CONTINUED|CREATED\s+BY|TERBILANG|"
    r"APABILA|DICETAK|PRINTED|MUTASI\s+(?:DEBET|DEBIT|KREDIT|CREDIT)|\*+\s*(?:AKHIR|END)|"
    r"AKHIR\s+LAPORAN\b|END\s+OF\s+STATEMENT\b)",
    re.IGNORECASE,
)

DEBIT_HINT_RE = re.compile(
    r"\b(TARIK|TARIKAN|PENARIKAN|WITHDRAWAL|OUTGOING|BIAYA|ADM|ADMIN|FEE|PAJAK|"
    r"PEMBAYARAN|BAYAR|PEMBELIAN|TRANSAKSI|DEBIT|DB|TRANSFER KE|TRF KE|TRSF KE|"
    r"TRANSFER OUT|TRSF OUT|(?:TRANSFER|TRF|TRSF)(?!\s+(?:DARI|IN|MASUK|CR|KR))|KE)\b"
)
CREDIT_HINT_RE = re.compile(
    r"\b(SETOR|SETORAN|BUNGA|INTEREST|JASA GIRO|BAGI HASIL|KREDIT|CREDIT|KR|CR|"
    r"INCOMING|DEPOSIT|RECEIVED|REFUND|GAJI|SALARY|TRANSFER DARI|TRSF DARI|TRF DARI|"
    r"TRANSFER IN|TRSF IN|DARI)\b"
)

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
CATEGORY_RES = [(name, re.compile(rf"\b(?:{pattern})\b")) for name, pattern in CATEGORY_RULES]

ROLE_KEYS = [
    ("balance", {"saldo", "balance", "saldoakhir", "sudo", "saido", "sado"}),
    ("debit", {"debet", "debit", "db", "dr", "keluar", "pengeluaran", "penarikan", "withdrawal", "withdrawals", "tarik"}),
    ("credit", {"kredit", "credit", "cr", "masuk", "pemasukan", "deposit", "deposits", "deposito", "setor"}),
    ("desc", {"keterangan", "uraian", "deskripsi", "description", "remark", "remarks", "berita", "narasi", "details", "detail", "particulars", "transaksi:", "information", "informasi"}),
    ("date", {"tanggal", "tgl", "date", "waktu", "tanggal/waktu"}),
    ("amount", {"jumlah", "mutasi", "nominal", "amount", "nilai"}),
]
NUMERIC_ROLES = {"balance", "debit", "credit", "amount"}
ALL_ROLE_WORDS = sorted({k for _, keys in ROLE_KEYS for k in keys if len(k) >= 5})

MONEY_RE = re.compile(
    r"^[+\-]?\(?(?:\d{1,4}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d{1,3}[.,]\d{6}[.,]\d{1,2}|\d+[.,]\d{1,2})\)?(?:DB|DR|CR|D|K|C|[+\-])?$"
    r"|^[+\-]\d{4,15}(?:DB|DR|CR|D|K|C)?$"
)
INT_RE = re.compile(r"^[+\-]?(?:0|[1-9]\d{0,14})(?:DB|DR|CR|D|K|C)?$")

DIRECTION_MARKER_RE = re.compile(r"^(DB|DR|CR|D|K|C|\+|-)$", re.IGNORECASE)

TABLE_HEADER_KEYWORDS = {
    "TANGGAL", "TGL", "DATE", "WAKTU", "TANGGAL/WAKTU", "TGL/WAKTU", "VALUTA",
    "URAIAN", "KETERANGAN", "DESKRIPSI", "DESCRIPTION", "REMARK", "REMARKS",
    "NARASI", "BERITA", "PARTICULARS", "DETAIL", "DETAILS", "INFORMATION", "INFORMASI",
    "MUTASI", "JUMLAH", "NOMINAL", "AMOUNT", "NILAI",
    "DEBET", "DEBIT", "KELUAR", "PENGELUARAN", "PENARIKAN", "WITHDRAWAL", "WITHDRAWALS",
    "KREDIT", "CREDIT", "MASUK", "PEMASUKAN", "DEPOSIT", "DEPOSITS", "DEPOSITO",
    "SALDO", "BALANCE", "SAIDO", "SUDO", "SADO",
}

SUMMARY_INTERCEPT_TOKENS = (
    "MUTASIDEBET",
    "MUTASIDEBIT",
    "MUTASIKREDIT",
    "MUTASICREDIT",
    "TOTALMUTASI",
    "JUMLAHMUTASI",
    "TOTALDEBET",
    "TOTALDEBIT",
    "TOTALKREDIT",
    "TOTALCREDIT",
    "JUMLAHDEBET",
    "JUMLAHDEBIT",
    "JUMLAHKREDIT",
    "JUMLAHCREDIT",
    "SUBTOTAL",
    "TOTAL",
    "JUMLAH",
)

FOOTER_BOILERPLATE_RE = re.compile(
    r"^\W*(HALAMAN\b|PAGE\b|BERSAMBUNG|CONTINUED|CREATED\s+BY|TERBILANG|"
    r"APABILA|DICETAK|PRINTED|\*+\s*(?:AKHIR|END)|"
    r"(?:TEL(?:P|EPON)?\.?|FAX\.?|CALL\s+CENTER|CUSTOMER\s+SERVICE|CONTACT\s+CENTER)\s*[:.(0-9]|"
    r"PT\.?\s+BANK\b.*\b(?:TERDAFTAR|DIAWASI|OTORITAS\s+JASA\s+KEUANGAN|OJK)\b)",
    re.IGNORECASE,
)

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

NON_BANK_HEADER_WORDS: set[str] = {
    "REKENING", "KORAN", "LAPORAN", "MUTASI", "TRANSAKSI", "RINGKASAN",
    "SALDO", "BULAN", "LALU", "AWAL", "AKHIR", "PERIODE", "PERIOD",
    "ACCOUNT", "STATEMENT", "SUMMARY", "CREATED", "PAGE", "HALAMAN",
    "DARI", "OF", "NO", "NOMOR", "NAMA", "NAME", "ALIAS", "CURRENCY",
    "MATA", "UANG", "BRANCH", "CABANG", "KCP", "KCU", "IDR", "USD",
    "RP", "TANGGAL", "DATE", "TIME", "DEBIT", "DEBET", "CREDIT",
    "KREDIT", "BALANCE", "OPENING", "CLOSING", "TOTAL", "AMOUNT",
    "DEBITED", "CREDITED", "REMARK", "REFERENCE", "DETAIL", "REFF",
    "TRANSFER", "REMITTANCE", "LINK", "INFORMASI", "ESTATEMENT", "TAHAPAN",
    "KEPADA", "YTH", "TRANSACTION", "INQUIRY"
}

PENALTIES: dict[str, float] = {
    "MATH_DISCREPANCY_ALERT": 0.55,
    "MISSING_PERIOD_OR_CONTINUITY_GAP": 0.55,
    "SESSION_CONTINUITY_GAP": 0.55,
    "TOTAL_MISMATCH": 0.55,
    "EXTRACTION_CONFLICT": 0.5,
    "OVERLAID_TEXT": 0.6,
    "FONT_ANOMALY": 0.45,
    "balance_mismatch": 0.5,
    "direction_unknown": 0.35,
    "date_invalid": 0.3,
    "multiple_amounts": 0.3,
    "multiple_balances": 0.3,
    "pending_transaction": 0.1,
    "amount_column_inferred": 0.15,
}

STREAM_ERROR_PATTERNS: tuple[str, ...] = (
    "corrupt object stream",
    "zlib error",
    "incorrect header check",
    "syntax error",
    "damaged",
    "format error",
    "read error",
)


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------


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
    block_no: int = 0
    line_no: int = 0
    word_no: int = 0
    xc: float = field(init=False)
    yc: float = field(init=False)
    h: float = field(init=False)

    def __post_init__(self) -> None:
        self.xc = (self.x0 + self.x1) / 2
        self.yc = (self.y0 + self.y1) / 2
        self.h = max(self.y1 - self.y0, 1e-6)


@dataclass
class Span:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font: str = ""
    size: float = 0.0
    color: int = 0
    flags: int = 0
    origin_x: float = 0.0
    origin_y: float = 0.0
    xc: float = field(init=False)
    yc: float = field(init=False)
    h: float = field(init=False)
    subset_prefix: str = field(init=False)
    font_family: str = field(init=False)

    def __post_init__(self) -> None:
        self.xc = (self.x0 + self.x1) / 2
        self.yc = (self.y0 + self.y1) / 2
        self.h = max(self.y1 - self.y0, 1e-6)
        if "+" in self.font and len(self.font.split("+", 1)[0]) == 6:
            self.subset_prefix, self.font_family = self.font.split("+", 1)
        else:
            self.subset_prefix, self.font_family = "", self.font


@dataclass
class NumericTokenProfile:
    assigned_column: str
    text: str
    detected_amount: float
    x0: float
    y0: float
    x1: float
    y1: float
    font: str = ""
    font_family: str = ""
    subset_prefix: str = ""
    size: float = 0.0
    color: int = 0
    flags: int = 0
    contiguous: bool = True
    signal1_ok: bool = True
    signal2_ok: bool = True
    signal3_ok: bool = True
    confidence: str = "HIGH"

    @property
    def role(self) -> str:
        return self.assigned_column

    @property
    def value(self) -> float:
        return self.detected_amount


@dataclass
class ConsensusResult:
    detected_amount: float | None
    assigned_column: str | None
    direction: str | None
    extraction_confidence: str
    signal1_geometric: bool
    signal2_structural: bool
    signal3_lexical: bool
    width_hesitation: bool = False
    contiguous_span: bool = True
    baseline_match: bool = True
    flags: list[str] = field(default_factory=list)
    profile: NumericTokenProfile | None = None

    @property
    def value(self) -> float | None:
        return self.detected_amount

    @property
    def role(self) -> str | None:
        return self.assigned_column


@dataclass
class ColumnInterval:
    assigned_column: str
    x0: float
    x1: float
    edge_x1: float = 0.0
    tol: float = 0.0
    date_density: float = 0.0
    numeric_density: float = 0.0
    token_count: int = 0

    @property
    def role(self) -> str:
        return self.assigned_column

    @role.setter
    def role(self, val: str) -> None:
        self.assigned_column = val

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)


@dataclass
class PageFingerprint:
    pdf_page_index: int
    account_number: str = "UNKNOWN_ACCOUNT"
    bank_name: str = ""
    statement_period_start: date | None = None
    statement_period_end: date | None = None
    statement_month: int | None = None
    statement_year: int | None = None
    printed_page_number: int | None = None


@dataclass
class RowBand:
    index: int
    row_top_y: float
    y_end: float
    date_parts: tuple | None
    row_time: tuple[int, int, int] | None
    rest_text: str = ""
    date_word: Word | None = None
    words: list[Word] = field(default_factory=list)
    spans: list[Span] = field(default_factory=list)
    lines: list["Line"] = field(default_factory=list)
    eod_balance: float | None = None

    @property
    def y_start(self) -> float:
        return self.row_top_y


@dataclass
class ExtractedRow:
    page: int
    raw_text: str
    transaction_index: int = 0
    date: date | str | None = None
    description: str = ""
    clean_description: str = ""
    counterparty_name: str = ""
    debit: float | None = None
    credit: float | None = None
    balance: float | None = None
    printed_balance: float | None = None
    computed_balance: float | None = None
    balance_discrepancy: float | None = None
    checkpoint_balance: float | None = None
    is_reconciled: bool = True
    has_printed_balance: bool = False
    reconciliation_status: str = "UNVERIFIED"
    row_top_y: float = 0.0
    statement_year: int | None = None
    statement_month: int | None = None
    printed_page_number: int | None = None
    pdf_page_index: int = 1
    pending_mutasi: float | None = None
    is_opening: bool = False
    validation: list[str] = field(default_factory=list)
    date_parts: tuple | None = field(default=None, repr=False)
    extra: str = ""
    confidence: float = 1.0
    extraction_confidence: str = "HIGH"
    pending_dir: str | None = field(default=None, repr=False)
    account: str = ""
    bank: str = ""
    time: tuple[int, int, int] | None = field(default=None, repr=False)
    numeric_profiles: list[NumericTokenProfile] = field(default_factory=list, repr=False)
    math_discrepancy: dict[str, Any] | None = None
    single_amount_col: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if self.printed_balance is None and self.balance is not None:
            self.printed_balance = self.balance
        elif self.balance is None and self.printed_balance is not None:
            self.balance = self.printed_balance
        self.has_printed_balance = self.printed_balance is not None
        if self.pdf_page_index == 1 and self.page != 1:
            self.pdf_page_index = self.page


@dataclass
class AuditIssue:
    transaction_index: int
    pdf_page: int
    date: str
    issue_type: str
    severity: str
    printed_value: float | None
    expected_value: float | None
    discrepancy: float | None
    description: str


@dataclass
class StatementAuditResult:
    account_number: str
    transactions_df: pd.DataFrame
    issues: list[AuditIssue]
    is_clean: bool
    total_pages: int = 1
    bank_name: str = ""

    @property
    def issues_df(self) -> pd.DataFrame:
        cols = [
            "transaction_index",
            "pdf_page",
            "date",
            "issue_type",
            "severity",
            "printed_value",
            "expected_value",
            "discrepancy",
            "description",
        ]
        if not self.issues:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame([vars(i) for i in self.issues], columns=cols)

    def summary_line(self) -> str:
        counts = Counter(i.issue_type for i in self.issues)
        issue_breakdown = " | ".join(f"{k}: {v}" for k, v in counts.items()) or "None"
        status = "PASS" if self.is_clean else "FLAGGED"
        acct_label = self.account_number or "UNKNOWN_ACCOUNT"
        return (
            f"[{status}] Acct: {acct_label} \t| "
            f"Pages: {self.total_pages} \t| "
            f"Txns: {len(self.transactions_df)} \t| "
            f"Issues ({len(self.issues)}): {issue_breakdown}"
        )


@dataclass
class Cell:
    text: str
    x0: float
    x1: float
    num_x1: float
    words: list[Word]
    money: bool = False
    assigned_column: str | None = None
    is_int: bool = False
    contaminated: bool = False

    @property
    def role(self) -> str | None:
        return self.assigned_column

    @role.setter
    def role(self, val: str | None) -> None:
        self.assigned_column = val

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def y0(self) -> float:
        return min((w.y0 for w in self.words), default=0.0)

    @property
    def y1(self) -> float:
        return max((w.y1 for w in self.words), default=0.0)


@dataclass
class Line:
    cells: list[Cell]
    y: float

    @property
    def text(self) -> str:
        return " ".join(c.text for c in self.cells)

    @property
    def y0(self) -> float:
        return min((c.y0 for c in self.cells), default=self.y)

    @property
    def y1(self) -> float:
        return max((c.y1 for c in self.cells), default=self.y)


@dataclass
class Layout:
    align: str
    numeric: list[dict]
    tol: float
    text_cols: list[dict]
    column_intervals: list[ColumnInterval] = field(default_factory=list)


@dataclass
class StatementSession:
    session_index: int
    account_number: str = "UNKNOWN_ACCOUNT"
    bank_name: str = ""
    statement_month: int | None = None
    statement_year: int | None = None
    period_start: date | None = None
    period_end: date | None = None
    opening_balance: float | None = None
    closing_balance: float | None = None
    transactions: list[ExtractedRow] = field(default_factory=list)
    reported_totals: dict[str, float] = field(default_factory=dict)
    is_sealed: bool = False
    pages: list[int] = field(default_factory=list)


@dataclass
class State:
    header_numeric: list[tuple[float, str]] = field(default_factory=list)
    layout: Layout | None = None
    has_rows: bool = False
    printed: dict[str, float] = field(default_factory=dict)
    account: str = ""
    bank: str = ""
    forensics: list[dict[str, Any]] = field(default_factory=list)
    active_date: date | str | None = None
    active_month: int | None = None
    active_year: int | None = None
    is_sealed: bool = False
    has_pending_summary_table: bool = False
    balance: float | None = None
    last_page_w: float = 0.0
    last_is_ocr: bool | None = None


# ---------------------------------------------------------------------------
# Shared Helpers
# ---------------------------------------------------------------------------


def iso_date(dt: Any) -> str:
    """Format date/datetime as ISO 8601 string, or str(dt) if already formatted/unknown."""
    if dt is None:
        return ""
    if isinstance(dt, (date, datetime)):
        return dt.isoformat()
    return str(dt)


def add_flag(target: Any, flag: str) -> None:
    """Idempotently add a validation flag to a row or validation list."""
    if hasattr(target, "validation"):
        if flag not in target.validation:
            target.validation.append(flag)
    elif isinstance(target, list):
        if flag not in target:
            target.append(flag)


def drop_flag(target: Any, flag: str) -> None:
    """Remove all occurrences of a validation flag from a row or validation list."""
    if hasattr(target, "validation"):
        target.validation = [f for f in target.validation if f != flag]
    elif isinstance(target, list):
        target[:] = [f for f in target if f != flag]


def apply_confidence(row: Any) -> float:
    """Calculate and assign row confidence after validation penalties."""
    conf = max(0.0, round(1.0 - sum(PENALTIES.get(v, 0.2) for v in getattr(row, "validation", [])), 3))
    row.confidence = conf
    return conf


def make_issue(
    transaction_index: int | None = None,
    pdf_page: int | None = None,
    date: str | None = None,
    issue_type: str = "",
    severity: str = "MEDIUM",
    printed_value: float | None = None,
    expected_value: float | None = None,
    discrepancy: float | None = None,
    description: str = "",
    row: Any = None,
) -> AuditIssue:
    """Construct an AuditIssue dataclass with optional row attribute fallback."""
    if row is not None:
        if transaction_index is None and hasattr(row, "transaction_index"):
            transaction_index = row.transaction_index
        if pdf_page is None and hasattr(row, "pdf_page_index"):
            pdf_page = row.pdf_page_index
        if date is None and hasattr(row, "date"):
            date = iso_date(row.date)
    return AuditIssue(
        transaction_index=transaction_index,
        pdf_page=pdf_page,
        date=date,
        issue_type=issue_type,
        severity=severity,
        printed_value=printed_value,
        expected_value=expected_value,
        discrepancy=discrepancy,
        description=description,
    )


def emit_gap_pair(
    row: Any,
    severity: str,
    description: str,
    target_list: list[AuditIssue],
    transaction_index: int | None = None,
    pdf_page: int | None = None,
    date: str | None = None,
    printed_value: float | None = None,
    expected_value: float | None = None,
    discrepancy: float | None = None,
) -> None:
    """Emit the paired SESSION_CONTINUITY_GAP and MISSING_PERIOD_OR_CONTINUITY_GAP issues in order."""
    add_flag(row, "SESSION_CONTINUITY_GAP")
    add_flag(row, "MISSING_PERIOD_OR_CONTINUITY_GAP")
    tx_idx = transaction_index if transaction_index is not None else getattr(row, "transaction_index", None)
    p_page = pdf_page if pdf_page is not None else getattr(row, "pdf_page_index", None)
    d_str = date if date is not None else iso_date(getattr(row, "date", None))
    target_list.append(
        make_issue(
            transaction_index=tx_idx,
            pdf_page=p_page,
            date=d_str,
            issue_type="SESSION_CONTINUITY_GAP",
            severity=severity,
            printed_value=printed_value,
            expected_value=expected_value,
            discrepancy=discrepancy,
            description=description,
        )
    )
    target_list.append(
        make_issue(
            transaction_index=tx_idx,
            pdf_page=p_page,
            date=d_str,
            issue_type="MISSING_PERIOD_OR_CONTINUITY_GAP",
            severity=severity,
            printed_value=printed_value,
            expected_value=expected_value,
            discrepancy=discrepancy,
            description=description,
        )
    )


def normalize_reported_totals(totals: dict[str, Any] | None) -> dict[str, float]:
    """Normalizes reported summary totals"""
    if not totals:
        return {}
    normalized: dict[str, float] = {}
    for k, v in totals.items():
        if v is None:
            continue
        if k == "closing_balance":
            normalized["closing"] = v
        elif k in ("debit", "credit", "opening", "closing"):
            normalized[k] = v
    return normalized


def band_words(words: Sequence[Word], tol_fn: Any) -> list[list[Word]]:
    """Group words into horizontal line bands based on vertical proximity using a tolerance value or function."""
    if not words:
        return []
    is_callable = callable(tol_fn)
    groups: list[list[Word]] = []
    group_y_sums: list[float] = []
    for word in sorted(words, key=lambda w: (w.yc, w.x0)):
        tol = tol_fn(word) if is_callable else tol_fn
        if groups and abs(word.yc - (group_y_sums[-1] / len(groups[-1]))) <= tol:
            groups[-1].append(word)
            group_y_sums[-1] += word.yc
        else:
            groups.append([word])
            group_y_sums.append(word.yc)
    return groups
