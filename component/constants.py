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
MONTH_NAMES_ID = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
    7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember",
}
_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
_WEEKDAY_RE = re.compile(
    r"^(?:sen(?:in)?|sel(?:asa)?|rab(?:u)?|kam(?:is)?|jum(?:'?at)?|sab(?:tu)?|min(?:ggu)?|"
    r"mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)[,.\s]+",
    re.IGNORECASE,
)
_GLUED_TIME_RE = re.compile(
    r"^(\d{1,4}[/\-.\s]*(?:\d{1,2}|[A-Za-z]{3,9})\.?[/\-.\s,]*((?:19|20)\d{2}|\d{2})?|[A-Za-z]{3,9}\.?[\s/\-.,]*\d{1,2}(?:[\s/\-.,]+(?:(?:19|20)\d{2}|\d{2}))?)"
    r"([01]\d|2[0-3]|\d)(?=:[0-5]\d|\b)"
)
_TIME_RE = re.compile(
    r"^(?:\s*([01]?\d|2[0-3])[:.]([0-5]\d)(?:[:.]([0-5]\d))?(?:\s*(AM|PM|WIB|WITA|WIT))?|\s+([01]\d|2[0-3])(?=\s*$))\b",
    re.IGNORECASE,
)
_PURE_TIME_RE = re.compile(r"^([01]?\d|2[0-3])[:.]([0-5]\d)(?:[:.]([0-5]\d))?(?:\s*(AM|PM|WIB|WITA|WIT))?$", re.IGNORECASE)
_ALPHA_DATE_RE = re.compile(
    rf"^(?:(\d{{1,4}})[\s/\-.,]*({_MONTH_ALT})(?![A-Za-z])\.?(?:[\s/\-.,]*(\d{{1,4}}(?![\d:.])))?|"
    rf"({_MONTH_ALT})(?![A-Za-z])\.?[\s/\-.,]*(\d{{1,4}}(?![\d:.]))(?:[\s/\-.,]+(\d{{1,4}}(?![\d:.])))?)(?![\d,])",
    re.IGNORECASE,
)
_NUM_DATE_RE = re.compile(r"^(\d{1,4})([/\-.])(\d{1,2})(?:\2(\d{1,4}))?(?![\d,])")

ACCOUNT_RE = re.compile(
    r"(?:NO(?:MOR|\.)?\s*(?:REK(?:ENING|\.)?|ACC(?:OUNT|\.)?)|REK(?:ENING|\.)?\s*(?:NO(?:MOR|\.)?|GIRO|TABUNGAN)|"
    r"ACC(?:OUNT|\.)?\s*(?:NO(?:MOR|\.)?|NUMBER|#)|A/C\s*(?:NO|NUMBER)?)"
    r"(?:\s*/\s*(?:ACCOUNT\s*(?:NUMBER|NO)|NO\.?\s*REK(?:ENING)?))?"
    r"[\s:.\-]*(?:(?:IDR|USD|SGD|EUR|RP)\s*)?(\d+(?:[\-.]\d+|\s+\d+(?![/\-.\d:]))*)",
    re.IGNORECASE,
)

BANK_PATTERNS = [
    ("BCA", r"BANK\s+CENTRAL\s+ASIA|\bBCA\b|KLIKBCA|MYBCA|TAHAPAN\s+BCA|TAHAPAN\s+XPRESI"),
    ("MANDIRI", r"BANK\s+MANDIRI|\bMANDIRI\b|LIVIN|KOPRA\s+BY\s+MANDIRI"),
    ("BRI", r"BANK\s+RAKYAT\s+INDONESIA|\bBRI\b|BRIMO|BRITAMA|SIMPEDES"),
    ("BNI", r"BANK\s+NEGARA\s+INDONESIA|\bBNI\b|BNI\s+TAPLUS"),
    ("BSI", r"BANK\s+SYARIAH\s+INDONESIA|\bBSI\b"),
    ("CIMB", r"CIMB\s+NIAGA|\bCIMB\b|OCTO\s+MOBILE|OCTO\s+CLICKS"),
    ("PERMATA", r"PERMATA\s*BANK|BANK\s+PERMATA|\bPERMATA\b|PERMATAMOBILE"),
    ("DANAMON", r"BANK\s+DANAMON|\bDANAMON\b|D-BANK"),
    ("BTN", r"BANK\s+TABUNGAN\s+NEGARA|\bBTN\b"),
    ("OCBC", r"OCBC\s+NISP|BANK\s+OCBC|\bOCBC\b"),
    ("MAYBANK", r"\bMAYBANK\b"),
    ("PANIN", r"PANIN\s*BANK|BANK\s+PANIN|\bPANIN\b"),
    ("SEABANK", r"\bSEABANK\b|SEA\s+BANK"),
    ("JAGO", r"BANK\s+JAGO|\bJAGO\b"),
    ("JENIUS", r"\bJENIUS\b|\bBTPN\b|\bSMBC\b"),
    ("MEGA", r"BANK\s+MEGA"),
    ("SINARMAS", r"BANK\s+SINARMAS|\bSINARMAS\b"),
    ("MUAMALAT", r"BANK\s+MUAMALAT|\bMUAMALAT\b"),
    ("BJB", r"BANK\s+BJB|\bBJB\b"),
    ("DBS", r"BANK\s+DBS|\bDBS\b|DIGIBANK"),
    ("UOB", r"BANK\s+UOB|\bUOB\b|\bTMRW\b"),
    ("HSBC", r"\bHSBC\b"),
]
_BANK_RES = [(name, re.compile( pat, re.IGNORECASE)) for name, pat in BANK_PATTERNS]

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
_MONEY_RE = re.compile(r"^[+\-]?\(?(?:\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+[.,]\d{1,2})\)?(?:DB|DR|CR|D|K|C|[+\-])?$")
_INT_RE = re.compile(r"^[+\-]?\d{1,13}(?:DB|DR|CR|D|K|C)?$")