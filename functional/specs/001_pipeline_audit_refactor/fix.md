# Fix & Refactoring Summary (Spec 001)

## 1. Affected Components
- `component/ocr.py`
- `component/layout.py`
- `component/extractor.py`
- `component/parser.py`
- `component/balancecalc.py`
- `component/constants.py`
- `component/export.py`
- `app.py`
- `requirements.txt`, `README.md`

## 2. Fixes Applied

### Fix 1: Remove `pdfplumber` / `pypdfium2` from `ocr.py` (Speed & Camera-PDF Fix)
- Removed `pdfplumber` and `pypdfium2` completely from `component/ocr.py` and `requirements.txt`.
- `iter_pages()` goes directly from `words_pymupdf(page)` (`< 8` horizontal words) to `render_page()` + `preprocess(deskew=True)` + `ocr_words()`, restoring fast OCR execution and ensuring camera-photo PDFs always undergo deskew + `RapidOCR`.
- Kept `header_lines_text()` with `0.6 * height` tolerance in `component/ocr.py`.

### Fix 2: First-Page-Only Bank & Account Detection (850-Page Account Split Fix)
- **In `component/layout.py` (`process_page`):** Restricted `detect_account(meta_text)` and `detect_bank(meta_text)` strictly to `if page_no == 1:` and removed `detect_account(non_row_text)` / `detect_bank(non_row_text)` so transaction description continuation lines and pages `2..N` can never overwrite `state.account` or `state.bank`.
- **In `component/extractor.py` (`extract_statement`):** Set `doc_acc = hdr_acc or state.account or ""` and `doc_bank = hdr_bank or state.bank or detect_bank(first_page_text) or ""` from Page 1 only, stamping all rows in that document with the single `(doc_bank, doc_acc)` pair.

### Fix 3: Restore Proven Direction & Reconciliation Heuristics (`component/constants.py`, `component/parser.py`, `component/balancecalc.py`)
- Restored `DEBIT_HINT_RE`, `CREDIT_HINT_RE`, `explicit_direction`, `description_direction`, `hint_direction`, `marker_direction`, and `line_direction` so camera-photo scanned PDFs retain full direction and balance-delta recovery accuracy.

### Fix 4: Deduplicated Export & UI Helpers (`component/export.py`, `app.py`)
- Centralized `rows_df`, `review_df`, `format_date`, `serialize_row`, and `write_top5_block` in `component/export.py` and shared them with `app.py`.
