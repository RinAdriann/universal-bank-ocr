# Pipeline Audit & Root Cause Analysis (Spec 001)

## 1. Issue Description
1. **Scanned / Hybrid PDF Speed Regression:** Scanned and half-scanned PDFs slowed down significantly (reaching ~40 seconds for 3 pages) when `pdfplumber` was triggered as a fallback on pages with `< 8` PyMuPDF words.
2. **Camera-Photo Scanned PDF Extraction Regression:** Statements originating from camera photos converted into PDFs extracted poorly when `words_pdfplumber` picked up skewed/garbage embedded OCR layers instead of running `preprocess(deskew=True)` + `RapidOCR`, and when `DEBIT_HINT_RE` / `explicit_direction` / `description_direction` rules were altered.
3. **Multi-Page (850-Page) Single-Account Splitting Bug:** A single 850-page bank statement split into multiple different accounts because `process_page()` in `component/layout.py` ran `detect_account(meta_text) or detect_account(non_row_text)` on **every page** (`1..850`) and on `non_row_text` (which includes multi-line transaction description continuation lines), and `extractor.py` ran `detect_account(full_text)` across all pages. Any transfer description mentioning a recipient's account number overwrote `state.account` mid-document.

## 2. Root Cause Analysis

### Root Cause 1: `pdfplumber` Fallback on Scanned Pages (`component/ocr.py`)
- Calling `pdfplumber.open()` and `page.extract_words()` whenever `words_pymupdf(page)` returned `< 8` words meant every scanned page parsed the entire PDF object/stream tree in pure Python (`pdfminer.six`), adding massive latency before `RapidOCR` even started.
- Worse, on camera photos converted to PDF with a skewed/broken embedded text layer, `words_pymupdf` intentionally rejected tilted lines (`abs(dy) > 0.1 or dx < 0.9`) so the page would go to `preprocess(deskew=True)` + `RapidOCR`. `words_pdfplumber` did not filter out tilted lines, so it returned `>= 8` skewed words and bypassed `RapidOCR` + deskew entirely.

### Root Cause 2: Direction Heuristic Changes (`component/constants.py`, `component/parser.py`)
- Camera-photo OCR often misses small `DB`/`CR` suffixes in the mutasi column, relying on `description_direction` and `DEBIT_HINT_RE` (`TRANSAKSI`, `TRSF`, `TRF`, `KE`) to infer direction and opening balances. Removing those tokens from `DEBIT_HINT_RE` and expanding `explicit_direction` caused direction and balance-delta inference regressions on noisy scans.

### Root Cause 3: Per-Page & Full-Text Account Detection (`component/layout.py`, `component/extractor.py`)
- In `layout.py`, `process_page()` ran `detect_account(meta_text) or detect_account(non_row_text)` on every page (`page_no = 1..850`), overwriting `state.account` whenever a continuation line of a transaction description matched `ACCOUNT_RE` (e.g., `"NO REK: ..."`).
- Bank name and account number detection must **only** happen on the first page (`page_no == 1`) of a document (top 30% header in `extract_header_info` and pre-table `meta_text` on page 1).
