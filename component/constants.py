import re

_TOL = 0.51

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

ROLE_KEYS = [
    ("balance", {"saldo", "balance", "saldoakhir"}),
    ("debit", {"debet", "debit", "db", "dr", "keluar", "pengeluaran", "withdrawal", "withdrawals", "tarik"}),
    ("credit", {"kredit", "credit", "cr", "masuk", "pemasukan", "deposit", "deposits", "setor"}),
    ("desc", {"keterangan", "uraian", "deskripsi", "description", "remark", "remarks", "berita", "narasi", "details", "detail", "particulars", "transaksi:"}),
    ("date", {"tanggal", "tgl", "date", "waktu", "tanggal/waktu"}),
    ("amount", {"jumlah", "mutasi", "nominal", "amount", "nilai"}),
]
NUMERIC_ROLES = {"balance", "debit", "credit", "amount"}
_ALL_ROLE_WORDS = sorted({k for _, ks in ROLE_KEYS for k in ks if len(k) >= 5})

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
SUPPORTED_INPUT_SUFFIXES = {".pdf"} | IMAGE_SUFFIXES

