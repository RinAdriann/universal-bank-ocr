## C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/__init__.py
**Imports:** from .core import CATEGORY_RES, CATEGORY_RULES, AuditIssue, StatementAuditResult, StatementSession; from .extractor import extract_statement; from .extractor import extract_statement as extract_statements; from .ingest import load_and_sanitize_pdf, run_forensic_audit; from .ledger import audit_ledger, chain_matches, compute_running_balances, reconcile_balances, slice_into_sessions, sort_transactions_chronologically; from .ledger import reconcile_balances as reconcile; from .ner import TransactionNER, get_transaction_ner; from .parsing import calculate_category_breakdown, category, description_direction, explicit_direction, flow, hint_direction, line_direction, marker_direction; from .report import build_portfolio_overview_df, calculate_account_counterparty_recap, calculate_counterparty_breakdown, calculate_monthly_metrics, calculate_top_counterparties, create_memo_sheet, excel_bytes, get_account_holder_name; from .report import create_memo_sheet as create_sheet; from .table import find_table_header_band, intercept_summary_line

## C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/core.py
**Imports:** from __future__ import annotations; import os; import re; from collections import Counter; from dataclasses import dataclass, field; from datetime import date, datetime; from typing import Any, Sequence; import pandas as pd
- `@dataclass(frozen=True)` (3L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/parsing.py
- `@dataclass` (17L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/parsing.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `@dataclass` (26L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `@dataclass` (26L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `@dataclass` (21L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `@dataclass` (21L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `@dataclass` (9L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/parsing.py
- `@dataclass` (16L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `@dataclass` (46L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ledger.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `@dataclass` (10L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ledger.py
- `@dataclass` (36L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `@dataclass` (30L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `@dataclass` (15L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `@dataclass` (6L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `@dataclass` (14L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ledger.py
- `@dataclass` (16L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `def iso_date(dt: Any) -> str:` (7L) — Format date/datetime as ISO 8601 string, or str(dt) if already formatted/unknown. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ledger.py
- `def add_flag(target: Any, flag: str) -> None:` (8L) — Idempotently add a validation flag to a row or validation list. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ledger.py
- `def drop_flag(target: Any, flag: str) -> None:` (6L) — Remove all occurrences of a validation flag from a row or validation list. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ledger.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def apply_confidence(row: Any) -> float:` (5L) — Calculate and assign row confidence after validation penalties. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ledger.py
- `def make_issue(transaction_index: int | None=None, pdf_page: int | None=None, date: str | None=None, issue_type: str='', severity: str='MEDIUM', printed_value: float | None=None, expected_value: float | None=None, discrepancy: float | None=None, description: str='', row: Any=None) -> AuditIssue:` (31L) — Construct an AuditIssue dataclass with optional row attribute fallback. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ledger.py
- `def emit_gap_pair(row: Any, severity: str, description: str, target_list: list[AuditIssue], transaction_index: int | None=None, pdf_page: int | None=None, date: str | None=None, printed_value: float | None=None, expected_value: float | None=None, discrepancy: float | None=None) -> None:` (44L) — Emit the paired SESSION_CONTINUITY_GAP and MISSING_PERIOD_OR_CONTINUITY_GAP issues in order. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def normalize_reported_totals(totals: dict[str, Any] | None) -> dict[str, float]:` (13L) — Normalizes reported summary totals (e.g. closing_balance -> closing). | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def band_words(words: Sequence[Word], tol_fn: Any) -> list[list[Word]]:` (16L) — Group words into horizontal line bands based on vertical proximity using a tolerance value or function. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/parsing.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py

## C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
**Imports:** from __future__ import annotations; import argparse; from collections import Counter; from datetime import date, datetime; from pathlib import Path; import re; from typing import Any, Sequence; from component.core import AUDIT_TOL, AuditIssue, DEFAULT_DPI, ExtractedRow, SUPPORTED_INPUT_SUFFIXES, State, StatementAuditResult, StatementSession, apply_confidence, emit_gap_pair, iso_date, make_issue, normalize_reported_totals; from component.report import excel_bytes, rows_df; from component.ingest import detect_overlaid_streams, extract_header_info, get_pdf_sanitize_report, iter_pages, load_and_sanitize_pdf, ocr_fallback_single_page, run_forensic_audit; from component.page import apply_dates, process_page; from component.table import printed_totals; from component.parsing import indonesia_date_key, months_between, parse_statement_period, row_month_pair; from component.ledger import AccountBucket, audit_ledger, dedupe_repeated_balance_rows, dedupe_same_month_openings, forward_fill_session_balances, group_key_label, has_internal_bucket_discrepancy, order_page_bundles, page_signature_hash, slice_into_sessions
- `def summarize_account(rows: list[ExtractedRow], printed: dict[str, float], paths: Sequence[Path] | None=None, overlaid_findings: Sequence[dict[str, Any]] | None=None, duplicate_issues: Sequence[AuditIssue] | None=None, stream_issues: Sequence[AuditIssue] | None=None, account_number: str='UNKNOWN_ACCOUNT', bank_name: str='', total_pages: int=1, sessions: list[StatementSession] | None=None) -> tuple[dict[str, Any], StatementAuditResult]:` (293L) — Phase 5: Two-Tier Post-Extraction Math Audit (Local Session Audit -> Global Continuity). | **Callers:** local
- `def extract_statement(pdf_path: str | Path | Sequence[str | Path], dpi: int=DEFAULT_DPI) -> dict[str, Any]:` (344L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py
- `def main() -> None:` (8L) | **Callers:** local

## C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py
**Imports:** from __future__ import annotations; from collections import Counter; from functools import lru_cache; import io; from pathlib import Path; from typing import Any, Iterator, Sequence, Union; import cv2; import numpy as np; import pymupdf as fitz; from rapidocr_onnxruntime import RapidOCR; from component.core import AuditIssue, EXTRACTION_CONFIDENCE_OVERLAID, ExtractedRow, HEADER_DPI, HEADER_RATIO, IMAGE_SUFFIXES, NumericTokenProfile, STREAM_ERROR_PATTERNS, SUSPICIOUS_PDF_EDITORS, Span, Word, add_flag, band_words, iso_date, make_issue; from component.table import detect_overlaid_spans, find_table_header_band, group_lines, median_height; from component.parsing import clean_text, detect_account, detect_bank, match_date
- `def has_mupdf_stream_error(warnings_text: str | None) -> bool:` (5L) | **Callers:** local
- `def _default_sanitize_report() -> dict[str, Any]:` (8L) | **Callers:** local
- `def get_pdf_sanitize_report(pdf_source: Union[str, Path, Any]) -> dict[str, Any]:` (5L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def load_and_sanitize_pdf(path_or_bytes: Union[str, Path, bytes, io.BytesIO]) -> Any | None:` (66L) | **Callers:** local
- `def words_pymupdf(page: Any, raw_words: list | None=None) -> list[Word]:` (48L) | **Callers:** local
- `def spans_pymupdf(page: Any) -> list[Span]:` (32L) | **Callers:** local
- `@lru_cache(maxsize=1)` (4L) | **Callers:** local
- `def is_camera_photo(gray: np.ndarray) -> bool:` (13L) | **Callers:** local
- `def normalize_illumination(gray: np.ndarray) -> np.ndarray:` (8L) | **Callers:** local
- `def dewarp_rows(gray: np.ndarray) -> np.ndarray:` (44L) | **Callers:** local
- `def preprocess(image: np.ndarray, deskew: bool=True) -> np.ndarray:` (8L) | **Callers:** local
- `def ocr_words(bgr: np.ndarray) -> list[Word]:` (29L) | **Callers:** local
- `def render_page(page: Any, dpi: int, clip: Any=None) -> np.ndarray:` (5L) | **Callers:** local
- `def spans_from_words(words: list[Word]) -> list[Span]:` (17L) | **Callers:** local
- `def ocr_page_from_pixmap(page: Any, dpi: int=200) -> tuple[list[Word], float, list[Span]]:` (18L) | **Callers:** local
- `def ocr_fallback_single_page(path: Union[str, Path], page_no: int, dpi: int=200) -> tuple[list[Word], float, list[Span]]:` (18L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def _iter_pages_raw(path: Path, dpi: int, include_spans: bool=False) -> Iterator[tuple[int, list[Word], float, str, list[Span]]]:` (59L) | **Callers:** local
- `def iter_pages(path: Path, dpi: int, include_spans: bool=False) -> Iterator[tuple]:` (7L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def header_lines_text(words: list[Word]) -> str:` (7L) | **Callers:** local
- `def _find_first_date_amount_y0(words: list[Word]) -> float | None:` (12L) | **Callers:** local
- `def extract_header_info(path: Path, dpi: int=HEADER_DPI) -> tuple[str, str]:` (105L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def _empty_metadata_check(file_name: str) -> dict[str, Any]:` (9L) | **Callers:** local
- `def check_document_metadata(path: Path | str) -> dict[str, Any]:` (61L) | **Callers:** local
- `def dominant_profile_for_tokens(profiles: Sequence[NumericTokenProfile]) -> dict[str, Any]:` (16L) | **Callers:** local
- `def detect_font_anomalies(rows: Sequence[ExtractedRow]) -> list[dict[str, Any]]:` (61L) | **Callers:** local
- `def detect_overlaid_streams(page_no: int, spans: Sequence[Span], rows: Sequence[ExtractedRow] | None=None, overlaid_pairs: Sequence[tuple[Span, Span]] | None=None) -> list[dict[str, Any]]:` (47L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def run_forensic_audit(paths: Sequence[Path | str], rows: Sequence[ExtractedRow], page_overlaid: Sequence[dict[str, Any]] | None=None) -> dict[str, Any]:` (99L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py

## C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ledger.py
**Imports:** from __future__ import annotations; import re; from collections.abc import Iterator; from dataclasses import dataclass, field; from datetime import date, datetime; from pathlib import Path; from typing import Any, Sequence; from component.core import AUDIT_TOL, AuditIssue, EXTRACTION_CONFIDENCE_CONFLICT, EXTRACTION_CONFIDENCE_OVERLAID, ExtractedRow, OPENING_RE, StatementSession, add_flag, apply_confidence, drop_flag, iso_date, make_issue; from component.parsing import hint_direction, indonesia_date_key, months_between, row_month_pair
- `def signed_amount(row: ExtractedRow) -> float | None:` (4L) | **Callers:** local
- `def magnitude(row: ExtractedRow) -> float:` (3L) | **Callers:** local
- `def delta_direction(prev: float | None, bal: float | None, amt: float | None) -> str | None:` (9L) | **Callers:** local
- `def choose_direction(prev: float | None, bal: float | None, amt: float, explicit: str | None, desc: str) -> tuple[str | None, str]:` (16L) | **Callers:** local
- `def infer_seed_balance(openings: list[ExtractedRow], body: list[ExtractedRow], hint: float | None=None, printed: dict[str, float] | None=None) -> float | None:` (24L) — Infers initial/seed balance from opening rows, hints, printed totals, or first body checkpoint. | **Callers:** local
- `def resolve_pending(row: ExtractedRow, prev: float | None, tol: float=AUDIT_TOL, prefer: str='debit') -> str | None:` (44L) — Resolve remaining pending_mutasi via running balance delta when available. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def walk_balance_chain(seq: Sequence[ExtractedRow], seed_bal: float | None=None, only_printed_balance: bool=False, allow_zero_mag_checkpoint: bool=False) -> Iterator[tuple[float, float, float, bool]]:` (46L) — Walk a sequence of transaction rows and yield (diff, acc_signed, acc_mag, has_pending) per checkpoint. | **Callers:** local
- `def chain_matches(seq: list[ExtractedRow]) -> tuple[int, int, int]:` (10L) | **Callers:** local
- `def _chain_score(seq: list[ExtractedRow], seed_bal: float | None=None) -> tuple[int, int, int]:` (12L) — Score (signed_matches, mag_matches, transitions_checked) from an optional seed balance. | **Callers:** local
- `def has_internal_bucket_discrepancy(page_rows: Sequence[ExtractedRow], prev_closing_balance: float | None=None) -> bool:` (17L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def _transition_matches(prev_bal: float, row: ExtractedRow) -> bool:` (10L) | **Callers:** local
- `def _advance_balance(prev_bal: float | None, rows: list[ExtractedRow]) -> float | None:` (10L) | **Callers:** local
- `def _greedy_balance_chain(rows: list[ExtractedRow], seed_bal: float) -> list[ExtractedRow]:` (24L) — Reorder ambiguous rows via balance continuity only when strictly improving chain matches. | **Callers:** local
- `def _order_same_day_rows(day_rows: list[ExtractedRow], seq_map: dict[int, int], seed_bal: float | None, reverse_untimed: bool) -> list[ExtractedRow]:` (107L) | **Callers:** local
- `def sort_transactions_chronologically(rows: list[ExtractedRow], opening_hint: float | None=None, printed: dict[str, float] | None=None) -> tuple[list[ExtractedRow], str]:` (85L) — Sort rows by Indonesia date (DD/MM/YYYY), optional local 24h time, balance fit, then detection order. | **Callers:** local
- `def compute_running_balances(rows: list[ExtractedRow], initial_balance: float | None=None) -> list[float | None]:` (29L) — Calculate running balances purely from numeric transaction flows. | **Callers:** local
- `def reconcile_balances(rows: list[ExtractedRow], opening_hint: float | None=None, printed: dict[str, float] | None=None) -> dict[str, Any]:` (152L) — Pure numeric balance progression, pending direction resolution, and math delta calculations. | **Callers:** local
- `def audit_ledger(rows: list[ExtractedRow], opening_hint: float | None=None, printed: dict[str, float] | None=None) -> dict[str, Any]:` (282L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `@dataclass` (18L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def group_key_label(bank: str, acc: str) -> str:` (5L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def page_signature_hash(page_rows: Sequence[ExtractedRow]) -> int | None:` (14L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def topological_sort_key(row: ExtractedRow, fallback_year: int | None) -> tuple:` (23L) | **Callers:** local
- `def order_page_bundles(page_bundles: list[tuple[int, dict, list[ExtractedRow]]], fallback_year: int | None) -> list[tuple[int, dict, list[ExtractedRow]]]:` (34L) — Order page bundles when explicit printed page numbers indicate out-of-order pages within a statement. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def dedupe_same_month_openings(rows: list[ExtractedRow]) -> list[ExtractedRow]:` (11L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def dedupe_repeated_balance_rows(rows: list[ExtractedRow], opening_hint: float | None=None, printed: dict[str, float] | None=None) -> list[ExtractedRow]:` (121L) — Remove duplicate zero-mutasi or leaked opening/carry-forward rows sharing a printed balance. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def slice_into_sessions(page_bundles: Sequence[tuple[int, dict, list[ExtractedRow]]], default_account: str='UNKNOWN_ACCOUNT') -> list[StatementSession]:` (141L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def forward_fill_session_balances(session: StatementSession, opening_hint: float | None=None) -> None:` (47L) — Forward-fill missing/empty balance (saldo) cells sequentially per session
using running_balance = running_balance + credit - debit seeded from Saldo Awal. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py

## C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ner.py
**Imports:** from __future__ import annotations; import importlib.util; import logging; import os; from pathlib import Path; import re; import threading; from typing import Any, Sequence
- `def _empty_result() -> dict[str, Any]:` (9L) | **Callers:** local
- `class TransactionNER:` (119L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py
- `def get_transaction_ner() -> TransactionNER:` (5L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py

## C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
**Imports:** from __future__ import annotations; import math; import os; import re; import statistics; from collections import Counter; from datetime import date; from typing import Any, Sequence; from component.core import ALL_ROLE_WORDS, Cell, ColumnInterval, EXTRACTION_CONFIDENCE_CONFLICT, EXTRACTION_CONFIDENCE_HIGH, EXTRACTION_CONFIDENCE_MEDIUM, EXTRACTION_CONFIDENCE_OVERLAID, ExtractedRow, FOOTER_BOILERPLATE_RE, Layout, Line, MONTH_MAP, MONTHS, NUMERIC_ROLES, OPENING_RE, PURE_TIME_RE, PageFingerprint, ROLE_KEYS, RowBand, STOP_RE, SUMMARY_INTERCEPT_TOKENS, Span, State, TABLE_HEADER_KEYWORDS, Word, drop_flag, normalize_reported_totals; from component.ledger import resolve_pending; from component.parsing import clean_text, description_direction, detect_account, explicit_direction, extract_clean_description, extract_counterparty_name, extract_page_fingerprint, has_alpha_contamination, hint_direction, is_int_amount, is_money, line_direction, match_date, norm_money, parse_amount, parse_time; from component.table import _FORMATTED_THOUSANDS_RE, _UNFORMATTED_ECHO_AMT_RE, _extract_cell_time, _extract_leading_time, _parse_four_totals, _stitch_wrapped_numeric_cells, best_match, build_date_row_bands, build_dominant_font_profile, cluster, detect_overlaid_spans, detect_start, discover_column_intervals, edge, filter_time_only_starts, filter_numeric_column_spans, find_header, find_table_header_band, get_text_role, group_lines, intercept_summary_line, learn_numeric, median_height, resolve_numeric_consensus, summary_band
- `def apply_dates(rows: Sequence[ExtractedRow], year: int | None, statement_month: int | None=None, period_start: date | None=None, state: State | None=None) -> None:` (156L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def build_opening_rows(lines: Sequence[Line], region: Sequence[int], first_start: int | None, height: float, page_no: int, fingerprint: PageFingerprint, state: State) -> tuple[list[ExtractedRow], list[str]]:` (47L) | **Callers:** local
- `def _in_gutter_x(cell: Cell, col: dict[str, Any] | None, target_cell: Cell, tol: float, height: float) -> bool:` (13L) | **Callers:** local
- `def _resolve_band_adjacent_markers(band: RowBand, numeric: Sequence[dict[str, Any]], tol: float, height: float) -> tuple[dict[int, str], set[int], set[int]]:` (57L) — Bind adjacent-line CR/DB gutter markers to amount cells and above-line '-' signs to balance cells. | **Callers:** local
- `def _finalize_band_row(cur: ExtractedRow, band: RowBand) -> None:` (43L) — Apply EOD balance checkpoint, opening-row detection/credit-echo suppression, and pending direction. | **Callers:** local
- `def build_row_from_band(band: RowBand, page_no: int, fingerprint: PageFingerprint, state: State, pre_desc: Sequence[str], date_x1: float, height: float, time_col_ranges: Sequence[tuple[float, float]], numeric: Sequence[dict[str, Any]], col_by_role: dict[str, dict[str, Any]], tol: float, numeric_table_spans: Sequence[Span], dominant_font_profile: Any, overlaid_pairs: Any, text_cols: Sequence[dict[str, Any]]) -> ExtractedRow:` (227L) | **Callers:** local
- `def postprocess_rows(rows: list[ExtractedRow], state: State, fingerprint: PageFingerprint, numeric: Sequence[dict[str, Any]]) -> list[ExtractedRow]:` (95L) | **Callers:** local
- `def process_page(words: list[Word], page_w: float, page_no: int, state: State, spans: list[Span] | None=None) -> tuple[list[ExtractedRow], dict, str]:` (697L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py

## C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/parsing.py
**Imports:** from __future__ import annotations; import calendar; from decimal import Decimal, InvalidOperation; import re; from datetime import date, datetime; from typing import Any, Optional, Sequence; from component.core import ACCOUNT_RE, ALPHA_DATE_RE, Amount, BANKING_NOISE_RE, CATEGORY_RES, CREDIT_HINT_RE, Cell, DEBIT_HINT_RE, DIRECTION_MARKER_RE, EXTRACTION_CONFIDENCE_CONFLICT, GLUED_TIME_RE, INT_RE, MONEY_RE, MONTH_MAP, MONTHS, NON_BANK_HEADER_WORDS, NUM_DATE_RE, PAGE_NUM_RE, PERIOD_MONTH_YEAR_RE, PERIOD_RANGE_ALPHA_RE, PERIOD_RANGE_NUM_RE, PURE_TIME_RE, PageFingerprint, STRICT_ID_DECIMAL_RE, STRICT_INTL_DECIMAL_RE, STRICT_PLAIN_INT_RE, STRICT_SINGLE_DEC_RE, STRICT_THOUSANDS_ONLY_RE, TIME_RE, TRAILING_TIMESTAMP_RE, WEEKDAY_RE, Word, band_words
- `def norm_money(text: str) -> str:` (8L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `def has_alpha_contamination(text: str) -> bool:` (12L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `def is_money(text: str) -> bool:` (2L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `def is_int_amount(text: str) -> bool:` (2L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `def normalize_financial_float(num_str: str) -> float | None:` (58L) | **Callers:** local
- `def validate_financial_grammar(text: str) -> tuple[bool, Amount | None, str | None]:` (39L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `def parse_amount(text: str) -> Amount | None:` (5L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `def normalize_time_ob(text: str, with_word_boundary: bool=False) -> str:` (4L) — Normalize OCR misread characters (O/B -> 0/8) in timestamp strings. | **Callers:** local
- `def parse_time(text: str) -> tuple[int, int, int] | None:` (21L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `def norm_year(year: int | None) -> int | None:` (8L) | **Callers:** local
- `def parse_printed_page_number(text: str) -> int | None:` (11L) | **Callers:** local
- `def parse_statement_period(text: str) -> tuple[date | None, date | None, int | None, int | None]:` (48L) | **Callers:** local
- `def match_date(text: str) -> tuple[tuple, int] | None:` (142L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `def clean_text(value: str) -> str:` (2L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def extract_clean_description(description: str) -> str:` (7L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def extract_counterparty_name(description: str) -> str:` (2L) | **Callers:** local
- `def _leading_tx_type_direction(text: str) -> str | None:` (29L) — Resolve explicit CR/KR vs DB/DR or TRANSFER DR/KE in the leading transaction-type segment. | **Callers:** local
- `def _prepare_direction_text(text: str) -> tuple[str, str | None]:` (6L) — Return cleaned uppercase text (DDR: stripped) and any TRANSFER DR/KE match direction. | **Callers:** local
- `def hint_direction(description: str) -> str | None:` (18L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ledger.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def explicit_direction(text: str) -> str | None:` (17L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def description_direction(description: str) -> str | None:` (12L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def marker_direction(text: str) -> str | None:` (7L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
- `def line_direction(cells: Sequence[Any], amount_parser: Any=None) -> str | None:` (13L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def detect_account(text: str) -> str | None:` (14L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def detect_bank(words: Sequence[Word], is_ocr: bool=False) -> str:` (144L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py
- `def extract_page_fingerprint(page_text: str, header_text: str, pdf_page_index: int, fallback_account: str='', fallback_bank: str='') -> PageFingerprint:` (21L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def months_between(a: tuple[int, int], b: tuple[int, int]) -> int:` (3L) — Computes month distance from (year1, month1) to (year2, month2): (b[0] - a[0]) * 12 + (b[1] - a[1]). | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ledger.py
- `def row_month_pair(row: Any) -> tuple[int, int] | None:` (6L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ledger.py
- `def indonesia_date_key(row: Any, default_year: int=0) -> tuple[int, int, int]:` (41L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ledger.py
- `def month_key(dt: Any) -> tuple[int, int] | None:` (8L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/report.py
- `def category(description: str, debit: float | None=None, credit: float | None=None) -> str:` (14L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/report.py
- `def flow(debit: float | None, credit: float | None) -> str:` (10L) | **Callers:** local
- `def categorize_row(row: Any) -> str:` (8L) | **Callers:** local
- `def categorize_rows(rows: Sequence[Any]) -> list[str]:` (2L) | **Callers:** local
- `def calculate_category_breakdown(rows: Sequence[Any]) -> dict[str, dict[str, Any]]:` (16L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/report.py

## C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/report.py
**Imports:** from __future__ import annotations; import io; import re; import statistics; from dataclasses import asdict; from datetime import date, datetime; from typing import Any, Callable, Sequence; import pandas as pd; from openpyxl.styles import Alignment, Border, Font, PatternFill, Side; from openpyxl.utils import get_column_letter; from component.core import DETAIL_COLS, MONTH_NAMES_ID, OPENING_RE, REVIEW_COLS; from component.parsing import category, month_key
- `def safe_str(val: Any) -> str:` (5L) | **Callers:** local
- `def format_date(dt: Any) -> str:` (4L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py
- `def transaction_record(row: Any, format_dt: bool=False, escape_excel: bool=True) -> dict[str, Any]:` (17L) | **Callers:** local
- `def rows_df(rows: list[Any], format_dt: bool=True, escape_excel: bool=False) -> pd.DataFrame:` (6L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def review_df(rows: list[Any], detailed: bool=True, escape_excel: bool=False, only_issues: bool=True, audit_issues: Sequence[Any] | None=None) -> pd.DataFrame:` (96L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py
- `def serialize_row(row: Any) -> dict[str, Any]:` (8L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py
- `def month_label(ym: tuple[int, int]) -> str:` (4L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py
- `def saldo_mengendap(rows: list[Any]) -> dict[tuple[int, int], list[tuple[Any, float]]]:` (97L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py
- `def top5_monthly(rows: list[Any]) -> dict[tuple[int, int], dict[str, list[Any]]]:` (18L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py
- `def _extract_account_groups(result: dict[str, Any]) -> list[tuple[str, str, str, list[Any], dict[str, Any]]]:` (30L) | **Callers:** local
- `def get_account_holder_name(acc_data: dict[str, Any] | Sequence[Any], default: str='') -> str:` (20L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py
- `def calculate_monthly_metrics(rows: Sequence[Any]) -> dict[tuple[int, int], dict[str, Any]]:` (49L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py
- `def _accumulate_counterparty_monthly(rows: Sequence[Any], amount_fn: Callable[[Any], float], include_zero_amount_months: bool=False) -> tuple[dict[str, dict[tuple[int, int], float]], dict[str, int], set[tuple[int, int]]]:` (31L) — Accumulate counterparty transaction volumes and occurrence counts across calendar months. | **Callers:** local
- `def calculate_counterparty_breakdown(rows: Sequence[Any]) -> dict[str, Any]:` (40L) | **Callers:** local
- `def calculate_account_counterparty_recap(rows: Sequence[Any], is_credit: bool=True, top_n: int | None=None) -> dict[str, Any]:` (60L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py
- `def calculate_top_counterparties(rows: Sequence[Any], is_credit: bool=True, top_n: int=8) -> dict[str, Any]:` (6L) | **Callers:** local
- `def compute_portfolio_averages(m_metrics: dict[tuple[int, int], dict[str, Any]]) -> tuple[float, float, float, float]:` (12L) — Computes (avg_md, avg_mk, avg_peng, last_sa) for account monthly metrics. | **Callers:** local
- `def build_portfolio_overview_df(result: dict[str, Any]) -> pd.DataFrame:` (33L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py
- `def _write_account_recap_block(ws: Any, cur_row: int, bank: str, acc: str, holder: str, acc_rows: Sequence[Any], is_credit: bool, styles: dict[str, Any], start_col: int=1, precomputed_recap: dict[str, Any] | None=None) -> int:` (136L) | **Callers:** local
- `def _excel_styles() -> dict[str, Any]:` (50L) | **Callers:** local
- `def autofit_columns(ws: Any, min_width: int=10, max_width: int=100, spacer_cols: set[int] | None=None, skip_rows: set[int] | None=None) -> None:` (56L) | **Callers:** local
- `def create_memo_sheet(wb: Any, result: dict[str, Any], title: str='00_RINGKASAN') -> Any:` (273L) | **Callers:** local
- `def sheet_title(prefix: str, bank: str, acc: str, used: set[str]) -> str:` (31L) | **Callers:** local
- `def write_date_cell(ws: Any, row_idx: int, col_idx: int, dt: Any) -> Any:` (5L) | **Callers:** local
- `def write_money_cell(ws: Any, row_idx: int, col_idx: int, val: float | str | None) -> Any:` (4L) | **Callers:** local
- `def collapse_saldo_streaks(items: Sequence[tuple[Any, float]]) -> list[tuple[Any, float, int]]:` (10L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py
- `def saldo_mengendap_sheet(ws: Any, rows: list[Any], bold_font: Font | None=None) -> None:` (113L) | **Callers:** local
- `def write_top5_block(ws: Any, start_row: int, col_start: int, title: str, amt_header: str, rows: list[Any], field: str, bold_font: Font | None=None) -> None:` (52L) | **Callers:** local
- `def top5_sheet(ws: Any, rows: list[Any], bold_font: Font | None=None) -> None:` (31L) | **Callers:** local
- `def _style_tabular_sheet(ws: Any, styles: dict[str, Any], center_cols: set[int] | None=None, right_cols: set[int] | None=None, date_cols: set[int] | None=None, money_cols: set[int] | None=None) -> None:` (51L) | **Callers:** local
- `def excel_bytes(result: dict[str, Any]) -> bytes:` (74L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py

## C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/table.py
**Imports:** from __future__ import annotations; import itertools; import math; import re; import statistics; from collections import Counter; from difflib import get_close_matches; from typing import Any, Sequence; import numpy as np; from component.core import ALL_ROLE_WORDS, Cell, ColumnInterval, ConsensusResult, DIRECTION_MARKER_RE, EXTRACTION_CONFIDENCE_CONFLICT, EXTRACTION_CONFIDENCE_HIGH, EXTRACTION_CONFIDENCE_LOW, EXTRACTION_CONFIDENCE_MEDIUM, EXTRACTION_CONFIDENCE_OVERLAID, FOOTER_BOILERPLATE_RE, Line, MONTHS, NUMERIC_ROLES, NumericTokenProfile, OPENING_RE, PURE_TIME_RE, ROLE_KEYS, RowBand, STOP_RE, STRICT_ID_DECIMAL_RE, STRICT_INTL_DECIMAL_RE, SUMMARY_INTERCEPT_TOKENS, Span, State, TABLE_HEADER_KEYWORDS, Word, band_words; from component.parsing import has_alpha_contamination, is_int_amount, is_money, marker_direction, match_date, norm_money, normalize_time_ob, parse_amount, parse_time, validate_financial_grammar
- `def median_height(words: Sequence[Word]) -> float:` (3L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def make_cell(words: list[Word]) -> Cell:` (17L) | **Callers:** local
- `def group_lines(words: Sequence[Word], height: float) -> list[Line]:` (55L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def cluster(items: list, key_fn, eps: float) -> list[list]:` (8L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def keep_big(clusters: list[list]) -> list[list]:` (6L) | **Callers:** local
- `def edge(cell: Cell, align: str='right') -> float:` (2L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def cell_role(text: str) -> str | None:` (15L) | **Callers:** local
- `def is_header_line(line: Line) -> bool:` (33L) | **Callers:** local
- `def _expand_header_span(lines: list[Line], i: int, height: float) -> tuple[int, int]:` (14L) | **Callers:** local
- `def find_table_header_band(words: Sequence[Word]) -> tuple[float, float] | None:` (39L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def find_header(lines: list[Line], height: float) -> tuple[int, int, list[tuple[float, str]]] | None:` (21L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def build_dominant_font_profile(spans: Sequence[Span], y_min: float=0.0, y_max: float=float('inf')) -> dict[str, Any]:` (26L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def bbox_overlap_ratio(a: Span, b: Span) -> float:` (11L) | **Callers:** local
- `def filter_numeric_column_spans(spans: Sequence[Span], numeric_cols: Sequence[dict[str, Any]], y_min: float=0.0, y_max: float=float('inf'), height: float=10.0) -> list[Span]:` (28L) — Confine spans vertically to the transaction table and horizontally to Debit/Credit/Balance/Amount columns. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def detect_overlaid_spans(spans: Sequence[Span], min_overlap: float=0.45) -> list[tuple[Span, Span]]:` (23L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/ingest.py, C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def find_cell_spans(cell: Cell, spans: Sequence[Span], height: float) -> tuple[list[Span], Span | None]:` (30L) | **Callers:** local
- `def evaluate_signal_1_geometric(cell: Cell, matched_col: dict[str, Any] | None, row_top_y: float, y_end: float, tol: float) -> tuple[bool, bool, str | None]:` (17L) | **Callers:** local
- `def evaluate_signal_2_structural(cell: Cell, spans: Sequence[Span], dominant_profile: dict[str, Any], overlaid_pairs: Sequence[tuple[Span, Span]], height: float) -> dict[str, Any]:` (74L) | **Callers:** local
- `def evaluate_signal_3_lexical(cell_text: str) -> tuple[bool, float | None, str | None, str | None]:` (5L) | **Callers:** local
- `def resolve_numeric_consensus(cell: Cell, matched_col: dict[str, Any] | None, y_start: float, y_end: float, tol: float, spans: Sequence[Span], dominant_profile: dict[str, Any], overlaid_pairs: Sequence[tuple[Span, Span]], height: float) -> ConsensusResult:` (79L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def _extract_cell_time(cell: Cell, date_x1: float, height: float, time_col_ranges: Sequence[tuple[float, float]]) -> tuple[int, int, int] | None:` (21L) — Safely extract standalone optional time from a non-numeric cell. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def _extract_leading_time(text: str) -> tuple[tuple[int, int, int] | None, str]:` (13L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def detect_start(line: Line, page_w: float, date_col_x1: float | None=None) -> tuple[int, tuple, str, tuple[int, int, int] | None] | None:` (29L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def filter_time_only_starts(lines: Sequence[Line], starts: dict[int, tuple[int, tuple, str, tuple[int, int, int] | None]]) -> dict[int, tuple[int, tuple, str, tuple[int, int, int] | None]]:` (27L) — Drop row starts whose date cell is really an HH.MM.SS timestamp.

Guard: only active when the page's dominant full-date separator is '/' or '-'
(>= 3 such starts and they outnumber dot-dates). Dot-date templates are untouched. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def _line_has_primary_flow(line: Line) -> bool:` (6L) — True when a line contains a numeric token in the Debit, Credit, or Amount gutter. | **Callers:** local
- `def _line_has_any_numeric_gutter(line: Line) -> bool:` (6L) — True when a line contains any numeric token in the Debit, Credit, Amount, or Balance gutter. | **Callers:** local
- `def _is_wrapped_numeric_fragment_line(line: Line) -> bool:` (15L) — True when a line has no balance cell and its numeric gutter tokens are only wrapped continuation fragments. | **Callers:** local
- `def intercept_summary_line(line: Line, has_date: bool, header_band: tuple[float, float] | None=None) -> tuple[bool, dict[str, float]]:` (116L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def check_summary_table_header(text: str) -> bool:` (8L) | **Callers:** local
- `def _parse_four_totals(cells: Sequence[Cell], check_balance: bool=False) -> dict[str, float] | None:` (25L) — Parse 4-number summary table row (opening, debit, credit, closing) with optional arithmetic consistency check. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def summary_band(lines: list[Line], state: State | None=None) -> dict[str, float]:` (15L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def printed_totals(text: str) -> dict[str, float]:` (25L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/extractor.py
- `def _is_echoed_reference_line(cand_line: Line, active_lines: Sequence[Line]) -> bool:` (25L) — True when a dateless continuation line only holds an unformatted echoed amount
matching the active row's formatted flow amount (e.g. KlikBCA / BCA PRI narration wrap). | **Callers:** local
- `def _extract_balance_checkpoint(line: Line, skip_cell_idx: int | None=None) -> float | None:` (32L) — Return first parsed amount when line has numeric cell(s), no narrative, and only balance-restatement labels. | **Callers:** local
- `def build_date_row_bands(lines: Sequence[Line], starts: dict[int, tuple[int, tuple, str, tuple[int, int, int] | None]], region: Sequence[int], words: Sequence[Word], spans: Sequence[Span], height: float, allow_dateless_start: bool=False, header_band: tuple[float, float] | None=None) -> tuple[list[RowBand], dict[str, float], bool]:` (285L) — Build transaction row bands using the Complete Row Seal state machine and Summary Quarantine. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def build_x_density_histogram(words: Sequence[Word], page_w: float) -> np.ndarray:` (10L) | **Callers:** local
- `def segment_x_intervals(hist: np.ndarray, min_gutter: int=2) -> list[tuple[float, float]]:` (25L) | **Callers:** local
- `def discover_column_intervals(region_lines: Sequence[Line], page_w: float, height: float) -> list[ColumnInterval]:` (64L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def best_match(centers: list[float], header: list[float]) -> list[int]:` (18L) | **Callers:** local
- `def learn_numeric(money_cells: list[Cell], header_numeric: list[tuple[float, str]], height: float, page_w: float=0.0) -> tuple[str, list[dict], float] | None:` (94L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def get_text_role(cell: Cell, text_cols: Sequence[dict[str, Any]], height: float, default_role: str='other') -> str:` (5L) | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py
- `def _can_stitch_wrapped_numeric(t1: str, t2: str) -> str | None:` (56L) — Return stitched amount text when t1 and t2 are wrapped halves of one split number, else None. | **Callers:** local
- `def _stitch_wrapped_numeric_cells(lines: Sequence[Line], region: Sequence[int], starts: dict[int, Any], numeric: Sequence[dict[str, Any]], date_x1: float, tol: float, height: float, header_band: tuple[float, float] | None=None) -> None:` (82L) — Merge multi-line wrapped numeric fragments inside the same column gutter before column assignment. | **Callers:** C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/component/page.py

## C:/Users/it.intern1/Downloads/VSCode/Intern/Work/bank-ocr/app.py
**Imports:** import json; import os; import tempfile; import time; from concurrent.futures import ThreadPoolExecutor; import pandas as pd; import streamlit as st; from component import excel_bytes, extract_statement; from component.core import ALLOWED_EXTS, StatementAuditResult; from component.report import build_portfolio_overview_df, calculate_account_counterparty_recap, calculate_monthly_metrics, collapse_saldo_streaks, format_date, get_account_holder_name, month_label, review_df, rows_df, saldo_mengendap, serialize_row, top5_monthly
- `def render_top_summary_banner(rows: list, summary: dict, audit_result: StatementAuditResult | None) -> None:` (22L) | **Callers:** local
- `def render_analytics(rows: list, suffix: str='') -> None:` (33L) | **Callers:** local
- `def render_memo_summary(result: dict) -> None:` (65L) | **Callers:** local
- `def render_account_view(rows: list, summary: dict, audit_result: StatementAuditResult | None=None, label: str='') -> None:` (25L) | **Callers:** local
- `@st.cache_resource` (3L) | **Callers:** local

