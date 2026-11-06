# Indonesian Bank Statement OCR - Changelog

## Update-004
**Description:** Multi-Signal Consensus, Digital PDF Forensics, Stateless Page Extraction, Checkpoint Bucket Reconciler & Callable Audit UI (Spec 003)
**Date:** 2026-11-05 15:45 WIB
**Changelog:**
- **Global Variable & Schema Humanization (`component/structures.py`, `component/parser.py`, `component/export.py`):** Standardized accounting and domain field names (`detected_amount`, `assigned_column`, `printed_balance`, `computed_balance`, `balance_discrepancy`, `is_reconciled`, `has_printed_balance`, `row_top_y`, `checkpoint_balance`, `clean_description`, `counterparty_name`, `transaction_index`) with backward-compatible property aliases, plus `extract_clean_description` and `extract_counterparty_name` helpers.
- **Stateless Page Extraction, Dynamic X-Density Projection & 4-Tier Date Cascade (`component/ocr.py`, `component/parser.py`, `component/layout.py`):** Added native `PyMuPDF` vector word/span extraction (`words_pymupdf`, `spans_pymupdf`) with cold `RapidOCR` fallback, per-page header/footer fingerprinting (`PageFingerprint`, `extract_page_fingerprint`), 1D X-axis density histogram (`build_x_density_histogram`), date-anchor row banding (`build_date_row_bands`), and the 4-tier date inheritance cascade (`apply_dates`) resolving full dates, partial `DD/MM` dates (with Dec $\rightarrow$ Jan rollover), look-ahead dates for dateless `SALDO AWAL` rows (`YYYY-MM-01`), and look-behind dates for continuation rows.
- **Multi-Signal Consensus Extraction (`component/consensus.py`):** Created `component/consensus.py` evaluating Signal 1 Geometric ($x_1$ right-edge lock & vertical row band), Signal 2 Structural (PDF vector span continuity, direction markers, dominant font profile via `build_dominant_font_profile`, and `detect_overlaid_spans`), and Signal 3 Lexical (`validate_financial_grammar`, `has_alpha_contamination`) into `HIGH`, `MEDIUM`, `EXTRACTION_CONFLICT`, or `OVERLAID_TEXT`.
- **Multi-Account Partitioning, Page Deduplication & Topological Re-Ordering (`component/extractor.py`):** Partitioned pages by `account_number`, deduplicated repeated pages via transaction tuple hashing (`DUPLICATE_PAGE_DROPPED`), and topologically sorted pages and rows by `(statement_year, statement_month, printed_page_number, pdf_page_index, row_top_y)` before assigning sequential `transaction_index` ($0 \dots N$).
- **Checkpoint & Bucket Reconciler & Digital PDF Forensics (`component/audit.py`, `component/forensics.py`, `component/balancecalc.py`):** Created `component/audit.py` (`audit_ledger`) with bucket accumulation (`VERIFIED_BY_BUCKET`) for BCA intermediate rows missing `printed_balance`, non-destructive `BALANCE_MISMATCH` logging at `transaction_index`, `computed_balance = printed_balance` reset after discrepancies so a single bad row never cascades, and cross-month continuity checks (`MISSING_PERIOD_OR_CONTINUITY_GAP`). Created `component/forensics.py` (`run_forensic_audit`) detecting `SUSPICIOUS_PDF_PRODUCER`, `METADATA_MODIFIED`, `FONT_ANOMALY`, and `OVERLAID_TEXT`.
- **Callable Audit State, Single-Line Logger & Streamlit Audit UI (`component/structures.py`, `app.py`):** Added `AuditIssue` and `StatementAuditResult` (`issues_df`, `summary_line()`), replaced noisy per-row logs with a single tab-aligned console summary line per statement stream, and updated `app.py` with a top-level reconciliation status banner and collapsible `View Audit Issues & Discrepancies` table.

**Artifacts & Documentation Index:**
- [Spec 003 Overview](./specs/003_consensus_forensic_reconciler/README.md)
- [Implementation Plan](./specs/003_consensus_forensic_reconciler/plan.md)
- [Task Checklist](./specs/003_consensus_forensic_reconciler/task.md)
- [Verification Walkthrough](./specs/003_consensus_forensic_reconciler/walkthrough.md)

## Update-003
**Description:** Camera-Photo Detection, Illumination Normalization, Strip-Slope Dewarping & Blurry-OCR Resilience (Spec 002)
**Date:** 2026-10-22 08:20 WIB
**Changelog:**
- Added fast (`< 1 ms`) `is_camera_photo(gray)` detector, morphological background division (`normalize_illumination`), and 5-band vertical strip-slope row rectification (`dewarp_rows`) in `component/ocr.py`, routing camera photos (including those embedded inside PDFs) through illumination and angle normalization while keeping clean scans and digital PDFs on the fast path.
- Disabled `RapidOCR` 180° box orientation classifier (`engine.use_angle_cls = False`) so already-deskewed numeric cells (e.g. `3,000.00D`) are not flipped upside-down (`000000'c`).
- Added blurry-OCR date/amount recovery in `component/constants.py`, `component/parser.py`, `component/layout.py`, and `component/balancecalc.py` (recovering missing date slashes, `.00D`/`.00K` suffix noise, balance-verified `1000x` `.00D -> .000` scaling, and 2-step balance bridge repair).
- Removed `BANK_PATTERNS` / `BANK_RES` hardcoded bank regex list from `component/constants.py`, relying purely on Page 1 coordinate/height detection (`detect_bank`).

**Artifacts & Documentation Index:**
- [Spec 002 Overview](./specs/002_camera_photo_preprocess/README.md)
- [Implementation Plan](./specs/002_camera_photo_preprocess/plan.md)
- [Task Checklist](./specs/002_camera_photo_preprocess/task.md)
- [Verification Walkthrough](./specs/002_camera_photo_preprocess/walkthrough.md)

## Update-002
**Description:** Fix Scanned PDF Latency, Camera-Photo PDF Extraction, and 850-Page Account Splitting (Spec 001)
**Date:** 2026-10-21 16:15 WIB
**Changelog:**
- Removed `pdfplumber` fallback from `component/ocr.py` (`iter_pages`), eliminating the multi-second `pdfminer` bottleneck on scanned/hybrid PDFs and preventing skewed embedded text layers on camera-photo PDFs from bypassing `preprocess(deskew=True)` + `RapidOCR`.
- Restored proven direction heuristics (`DEBIT_HINT_RE`, `explicit_direction`, `description_direction`, `hint_direction`) in `component/constants.py`, `component/parser.py`, `component/layout.py`, and `component/balancecalc.py` for noisy camera-photo scans.
- Fixed the 850-page single-account splitting bug by restricting bank and account number detection strictly to Page 1 (`extract_header_info` top 30% + Page 1 pre-table `meta_text` in `process_page`), removing `non_row_text` and `full_text` account scanning across pages `2..N`.

**Artifacts & Documentation Index:**
- [Pipeline Audit & Root Cause Analysis](./specs/001_pipeline_audit_refactor/analysis.md)
- [Fix Summary](./specs/001_pipeline_audit_refactor/fix.md)
- [Verification Walkthrough](./specs/001_pipeline_audit_refactor/walkthrough.md)

## Update-001
**Description:** Core Extraction Pipeline Audit, Deduplication, Bug Fixes & Code Humanization (Spec 001)
**Date:** 2026-10-21 15:40 WIB
**Changelog:**
- Established `functional/` governance structure (`product_requirement.md`, `changelog.md`, and `specs/001_pipeline_audit_refactor/`).
- Consolidated duplicate DataFrame/record builders (`rows_df`, `review_df`, `format_date`, `serialize_row`) in `component/export.py` and reused them in `app.py`.
- Removed unused `pypdfium2` and `pdfplumber` dependencies from `requirements.txt`.
- Humanized variable names and control flow across `app.py` and `component/*.py`.

**Artifacts & Documentation Index:**
- [Spec 001 Overview](./specs/001_pipeline_audit_refactor/README.md)
- [Implementation Plan](./specs/001_pipeline_audit_refactor/plan.md)
- [Task Checklist](./specs/001_pipeline_audit_refactor/task.md)
