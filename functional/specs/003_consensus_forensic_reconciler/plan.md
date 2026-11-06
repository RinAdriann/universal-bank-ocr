# Implementation Plan — Spec 003: Multi-Signal Consensus, Forensic Ledger Pipeline & Checkpoint Reconciler

## 1. Variable & Schema Humanization (`component/structures.py`, `component/parser.py`, `component/export.py`)
- Standardize accounting and domain names on `Cell` and `ExtractedRow`:
  - `detected_amount` (`float`), `assigned_column` (`"debit" | "credit" | "balance"`), `printed_balance` (`Optional[float]`), `computed_balance` (`Optional[float]`), `balance_discrepancy` (`Optional[float]`), `is_reconciled` (`bool`), `has_printed_balance` (`bool`), `row_top_y` (`float`), `checkpoint_balance` (`Optional[float]`), `clean_description` (`str`), `counterparty_name` (`str`), and `transaction_index` (`int`).
  - Maintain backward-compatible property aliases (`role`, `val_x1`, `stated_bal`, `comp_bal`, `delta_diff_val`) so legacy callers remain unbroken.
- Add `extract_clean_description` and `extract_counterparty_name` in `component/parser.py` to strip standard Indonesian banking noise prefixes (`TRSF E-BANKING DB/CR`, `BI-FAST`, `QRIS`, etc.) and isolate counterparty entities.

## 2. Stateless Page Extraction & 4-Tier Date Cascade (`component/ocr.py`, `component/parser.py`, `component/layout.py`)
- **Native Vector Stream & Cold OCR Fallback (`component/ocr.py`):**
  - Extract native `PyMuPDF` words (`words_pymupdf`) and rich font/color/bbox spans (`spans_pymupdf`) in $\sim 5\text{ ms}$ per page; invoke `RapidOCR` + camera-photo preprocessing only when `len(page.get_text("words")) == 0`.
- **Page Header Fingerprinting (`component/parser.py`):**
  - `extract_page_fingerprint` scans header/footer text per page to populate `PageFingerprint` (`account_number`, `statement_period_start`, `statement_period_end`, `statement_month`, `statement_year`, `printed_page_number`, `pdf_page_index`).
- **Dynamic X-Axis Projection & Date-Anchor Banding (`component/layout.py`):**
  - `build_x_density_histogram` and `discover_column_intervals` discover horizontal column intervals dynamically and lock numeric tokens by their right edge ($x_1$).
  - `build_date_row_bands` slices the table into `[Y_start, Y_end)` transaction bands anchored on date rows.
- **4-Tier Date Resolution Cascade (`apply_dates` in `component/layout.py`):**
  - **Tier 1:** Full row date (`DD/MM/YYYY` or `DD-MM-YYYY`).
  - **Tier 2:** Partial row date (`DD/MM`) combined with `statement_year` (with Dec $\rightarrow$ Jan cross-year rollover).
  - **Tier 3:** Look-ahead borrowing from `row[i + 1]` on the same page for dateless `SALDO AWAL` / opening rows (`YYYY-MM-01`), or `statement_period_start` if explicitly printed in the header.
  - **Tier 4:** Look-behind inheritance from `row[i - 1]` for secondary/continuation rows.

## 3. Multi-Signal Consensus Extraction (`component/consensus.py`)
- **`evaluate_signal_1_geometric`:** Checks $x_1$ right-edge alignment against column cluster center and detects abnormal bounding-box width expansion (`width_hesitation`).
- **`evaluate_signal_2_structural`:** Inspects underlying PDF vector spans, checks span continuity, captures attached or adjacent direction markers (`DB`, `CR`, `D`, `K`, `-`), compares font family/size/color against `build_dominant_font_profile`, and detects overlapping bounding boxes via `detect_overlaid_spans`.
- **`evaluate_signal_3_lexical` & `resolve_numeric_consensus`:** Validates strict financial grammar (`validate_financial_grammar`, `has_alpha_contamination`) and resolves all three signals into `HIGH`, `MEDIUM`, `EXTRACTION_CONFLICT`, or `OVERLAID_TEXT`.

## 4. Multi-Account Splitting, Page Deduplication & Topological Re-Ordering (`component/extractor.py`)
- Partition extracted page records by `account_number`.
- Deduplicate identical pages within an account stream by hashing the page's transaction tuple list (`_page_tx_hash`) and recording a `DUPLICATE_PAGE_DROPPED` `AuditIssue`.
- Sort pages and rows using the composite key `(statement_year, statement_month, printed_page_number, pdf_page_index, row_top_y)` and assign sequential 0-based `transaction_index` ($0, 1, \dots, N$).

## 5. Checkpoint & Bucket Reconciler & Digital PDF Forensics (`component/audit.py`, `component/forensics.py`)
- **Bucket Accumulation (`audit_ledger` in `component/audit.py`):**
  - Accumulate intermediate rows where `printed_balance is None` (`has_printed_balance = False`) in `pending_bucket`.
  - When a checkpoint row (`printed_balance is not None`) is reached, compare `computed_balance` against `printed_balance` within `AUDIT_TOL = 0.05`.
  - If reconciled, mark bucket rows `VERIFIED_BY_BUCKET` and the checkpoint row `RECONCILED`.
  - If mismatched, mark the checkpoint row `DISCREPANCY`, log a `BALANCE_MISMATCH` `AuditIssue` at `transaction_index` without overwriting `printed_balance`, and reset `computed_balance = printed_balance` so a single discrepancy never cascades to subsequent rows.
  - Check cross-month seam continuity (`MISSING_PERIOD_OR_CONTINUITY_GAP`) and statement summary invariants.
- **Digital PDF Forensics (`run_forensic_audit` in `component/forensics.py`):**
  - Check PDF metadata (`SUSPICIOUS_PDF_PRODUCER`, `METADATA_MODIFIED`), numeric token font deviations (`FONT_ANOMALY`), and overlapping text spans (`OVERLAID_TEXT`).

## 6. Callable Audit State, Single-Line Logger & Streamlit UI (`component/structures.py`, `app.py`)
- `StatementAuditResult` provides `issues_df` (DataFrame keyed by `transaction_index`) and `summary_line()` emitting a single tab-aligned console line per statement stream.
- `app.py` displays a top-level reconciliation status banner (`st.success` / `st.warning`) and a collapsible `View Audit Issues & Discrepancies` expander rendering `audit_result.issues_df`.
