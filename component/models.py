"""Data structures for bank statement extraction."""

from dataclasses import dataclass, field
from datetime import date

@dataclass
class ExtractedRow:
    page: int
    raw_text: str
    date: date | str | None = None
    description: str = ""
    debit: float | None = None
    credit: float | None = None
    balance: float | None = None
    pending_mutasi: float | None = None
    is_opening: bool = False
    validation: list[str] = field(default_factory=list)
    date_parts: tuple | None = field(default=None, repr=False)
    reference: str = ""
    extra: str = ""
    confidence: float = 1.0
    pending_dir: str | None = field(default=None, repr=False)

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

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return max(self.y1 - self.y0, 1e-6)

@dataclass
class Cell:
    text: str
    x0: float
    x1: float
    num_x1: float
    words: list[Word]
    money: bool = False
    role: str | None = None
    is_int: bool = False

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

@dataclass
class Line:
    cells: list[Cell]
    y: float

    @property
    def text(self) -> str:
        return " ".join(c.text for c in self.cells)

@dataclass
class Layout:
    align: str
    numeric: list[dict]
    tol: float
    text_cols: list[dict]

@dataclass
class State:
    header_numeric: list[tuple[float, str]] = field(default_factory=list)
    layout: Layout | None = None
    has_rows: bool = False
    printed: dict[str, float] = field(default_factory=dict)
