from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd


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
    reference: str = ""
    extra: str = ""
    confidence: float = 1.0
    extraction_confidence: str = "HIGH"
    pending_dir: str | None = field(default=None, repr=False)
    account: str = ""
    bank: str = ""
    time: tuple[int, int, int] | None = field(default=None, repr=False)
    numeric_profiles: list[NumericTokenProfile] = field(default_factory=list, repr=False)
    math_discrepancy: dict[str, Any] | None = None

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
