import re

TOL = 0.51

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
SUPPORTED_INPUT_SUFFIXES = {".pdf"} | IMAGE_SUFFIXES
ALLOWED_EXTS = [ext.lstrip(".") for ext in sorted(SUPPORTED_INPUT_SUFFIXES)]

DETAIL_COLS = ["Tanggal", "Kredit", "Debit", "Saldo", "Deskripsi", "Kategori Transaksi"]
REVIEW_COLS = ["Halaman", "Validation flags", "Full detected text"]

MONTHS = {
    "jan": 1, "januari": 1, "january": 1,
    "feb": 2, "pebruari": 2, "februari": 2, "february": 2,
    "mar": 3, "maret": 3, "march": 3,
    "apr": 4, "april": 4,
    "mei": 5, "may": 5,
    "jun": 6, "juni": 6, "june": 6,
    "jul": 7, "juli": 7, "july": 7,
    "agu": 8, "agt": 8, "agustus": 8, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "okt": 10, "oktober": 10, "oct": 10, "october": 10,
    "nov": 11, "nopember": 11, "november": 11,
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
    r"([01]\d|2[0-3]|\d)(?=:[0-5]\d|\b)"
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
    rf"({MONTH_ALT})(?![A-Za-z])\.?[\s/\-.,]*(\d{{1,4}}(?![\d:.]))(?:[\s/\-.,]+(\d{{1,4}}(?![\d:.])))?)(?![\d,])",
    re.IGNORECASE,
)

NUM_DATE_RE = re.compile(r"^(\d{1,4})([/\-.])(\d{1,2})(?:\2(\d{1,4}))?(?![\d,])")

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
    r"^\W*(SALDO\s+AWAL|SAK\s+AWAL|OPENING\s+BALANCE|BEGINNING\s+BALANCE|SALDO\s+SEBELUMNYA|BALANCE\s+B/?F|BROUGHT\s+FORWARD)\b",
    re.IGNORECASE,
)

STOP_RE = re.compile(
    r"^\W*(TOTAL\b|JUMLAH\s+(?:MUTASI|DEBET|DEBIT|KREDIT|CREDIT)|SALDO\s+AKHIR|CLOSING\s+BALANCE|ENDING\s+BALANCE|"
    r"SALDO\s+AWAL|OPENING\s+BALANCE|SALDO\s+REKENING|HALAMAN\b|PAGE\b|BERSAMBUNG|CONTINUED|CREATED\s+BY|TERBILANG|"
    r"APABILA|DICETAK|PRINTED|MUTASI\s+(?:DEBET|DEBIT|KREDIT|CREDIT)|\*+\s*(?:AKHIR|END))",
    re.IGNORECASE,
)

DEBIT_HINT_RE = re.compile(
    r"\b(TARIK|TARIKAN|PENARIKAN|WITHDRAWAL|OUTGOING|BIAYA|ADM|ADMIN|FEE|PAJAK|"
    r"PEMBAYARAN|BAYAR|PEMBELIAN|TRANSAKSI|DEBIT|DB|TRANSFER KE|TRF KE|TRSF KE|TRF|TRSF|KE)\b"
)
CREDIT_HINT_RE = re.compile(
    r"\b(SETOR|SETORAN|BUNGA|INTEREST|JASA GIRO|BAGI HASIL|KREDIT|CREDIT|KR|CR|"
    r"INCOMING|DEPOSIT|RECEIVED|REFUND|GAJI|SALARY|TRANSFER DARI|TRSF DARI|TRF DARI|DARI)\b"
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
    ("debit", {"debet", "debit", "db", "dr", "keluar", "pengeluaran", "withdrawal", "withdrawals", "tarik"}),
    ("credit", {"kredit", "credit", "cr", "masuk", "pemasukan", "deposit", "deposits", "setor"}),
    ("desc", {"keterangan", "uraian", "deskripsi", "description", "remark", "remarks", "berita", "narasi", "details", "detail", "particulars", "transaksi:"}),
    ("date", {"tanggal", "tgl", "date", "waktu", "tanggal/waktu"}),
    ("amount", {"jumlah", "mutasi", "nominal", "amount", "nilai"}),
]
NUMERIC_ROLES = {"balance", "debit", "credit", "amount"}
ALL_ROLE_WORDS = sorted({k for _, keys in ROLE_KEYS for k in keys if len(k) >= 5})

MONEY_RE = re.compile(
    r"^[+\-]?\(?(?:\d{1,4}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d{1,3}[.,]\d{6}[.,]\d{1,2}|\d+[.,]\d{1,2})\)?(?:DB|DR|CR|D|K|C|[+\-])?$"
)
INT_RE = re.compile(r"^[+\-]?\d{1,13}(?:DB|DR|CR|D|K|C)?$")
