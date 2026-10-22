# Implementation Plan (Spec 001)

## 1. Configuration & Hygiene Standardization
- Centralize all tunable parameters (`DEFAULT_DPI`, `HEADER_DPI`, `HEADER_RATIO`, `MIN_DIGITAL_WORDS`, `TOL`, `MAX_UPLOAD_MB`, `ALLOWED_EXTS`, `DETAIL_COLS`, `REVIEW_COLS`) in `component/constants.py` with `os.getenv` overrides.
- Create `.env.example` and update `.gitignore` per Rule 10.
- Remove unused `pypdfium2` from `requirements.txt` and clean stale `.pyc` files from `component/__pycache__/`.

## 2. Module Deduplication & Bug Fixes
- **`component/constants.py` & `component/parser.py`:**
  - Fix `DEBIT_HINT_RE` by removing bare `TRSF`, `TRF`, `TRANSAKSI`, `KE` and keeping directional patterns (`TRANSFER KE`, `TRF KE`, `TRSF KE`, `TRANSFER OUT`, `TRSF OUT`).
  - Consolidate 5 direction functions into `marker_direction(text, strict=...)`, `hint_direction(text)`, and `line_direction(cells)`.
- **`component/ocr.py`:**
  - Extract `render_page(page, dpi, clip=None)` helper.
  - Reuse `group_lines` and `median_height` from `component/layout.py` inside `words_to_text()`.
  - Use `words_pdfplumber` as an active digital fallback when `words_pymupdf` yields fewer than `MIN_DIGITAL_WORDS` before triggering image OCR.
  - Remove unused `extract_logo_text` and dead `pypdfium2` branches.
- **`component/balancecalc.py` & `component/extractor.py`:**
  - Extract `ensure_chronological(rows)` in `balancecalc.py` and pass `reversed_already` into `reconcile()` so `order: "newest_first"` is accurately reported without redundant work.
  - Attach `doc_year` to `row.date_parts` per document and execute `apply_dates()` once per merged account group.
  - Include `row.time` in `row_date_key` and replace the cryptic opening-row comprehension with `dedupe_openings()`.
  - Add `validate_inputs()` to check file format, existence, and `MAX_UPLOAD_MB`.
- **`component/export.py` & `app.py`:**
  - Share `format_date`, `rows_df`, `review_df`, `serialize_row`, `month_label`, `saldo_mengendap`, and `top5_monthly` from `component/export.py` directly in `app.py`.
  - Factor out `write_top5_block()`, `write_date_cell()`, and `write_money_cell()` in `component/export.py`.

