# Indonesian Bank Statement OCR

**Document Type:** PRD & Implementation Roadmap
**Version:** 1.1
**Platform Strategy:** Modular Implementation (Antigravity Framework)

## P1. Executive Summary
Indonesian Bank Statement OCR is a local extraction, multi-signal consensus, forensic audit, reconciliation, and reporting pipeline that converts digital PDFs, scanned PDFs, and statement images from Indonesian banks into structured **Excel (`.xlsx`)** and **JSON** outputs. It automates stateless page fingerprinting, 4-tier date inheritance, multi-account partitioning, duplicate page dropping, topological re-sequencing, checkpoint/bucket balance reconciliation (including omitted intermediate `Saldo` rows), digital PDF tampering forensics, transaction categorization, and monthly analytics (`Saldo Mengendap` and `Top 5 CR/DB`).

## P2. Tech Stack Recommendation
* **Frontend / UI:** Streamlit (`app.py`)
* **Backend / Extraction Engine:** Python 3.12+ modular package (`component/`)
* **PDF & Text Layer:** `PyMuPDF` (`fitz`) native vector word & span stream extraction
* **OCR & Image Processing:** `rapidocr-onnxruntime` (CPU ONNX cold fallback), `opencv-python-headless`, `numpy`
* **Data & Export:** `pandas`, `openpyxl`
* **Infrastructure:** Docker (`python:3.12-slim`)

## P3. User Roles
1. **Credit Analyst / Forensic Reviewer:** Uploads single or multiple bank statement files (PDF/images), inspects the top-level reconciliation status banner and collapsible `View Audit Issues & Discrepancies` table (`issues_df`), reviews extracted transactions indexed by `transaction_index` and monthly analytics (`Saldo Mengendap` & `Top 5 CR/DB`), and exports reconciled Excel/JSON reports.
2. **Downstream Integration / CLI Consumer:** Invokes `extract_statement`, `audit_ledger`, `run_forensic_audit`, or CLI (`python -m component.extractor`) for automated batch extraction, single-line console audit summaries, and JSON/Excel consumption.

## P4. Specs Catalog (Spec-based Strategy)

| Spec | Fitur | Status | Folder |
| --- | --- | --- | --- |
| **001** | Core Extraction Pipeline Audit, Deduplication & Refactoring | Completed | `specs/001_pipeline_audit_refactor/` |
| **002** | Camera-Photo Detection, Illumination Normalization & Strip-Slope Dewarping | Completed | `specs/002_camera_photo_preprocess/` |
| **003** | Multi-Signal Consensus, Digital PDF Forensics, Checkpoint Bucket Reconciler & Callable Audit UI | Completed | `specs/003_consensus_forensic_reconciler/` |

### Spec 003 — Multi-Signal Consensus, Digital PDF Forensics, Checkpoint Bucket Reconciler & Callable Audit UI (`specs/003_consensus_forensic_reconciler/`)
**Fokus:** Upgrade the extraction and verification pipeline with humanized accounting schemas, stateless page extraction, 4-tier date resolution, 3-signal numeric consensus, out-of-order/duplicate page topological re-sequencing, checkpoint/bucket balance reconciliation (handling BCA omitted `Saldo`), digital PDF tampering forensics, single-line console logging, and a clean Streamlit audit UI.

#### Functional Requirements (Spec 003)
| ID | Fitur | Deskripsi Detail | Status |
| --- | --- | --- | --- |
| **FR-08** | Stateless Page Fingerprinting & 4-Tier Date Cascade | Extract `PageFingerprint` (`account_number`, `statement_period_start/end`, `statement_month/year`, `printed_page_number`, `pdf_page_index`) per page and resolve every row's `YYYY-MM-DD` date via Tier 1 (`DD/MM/YYYY`), Tier 2 (`DD/MM` + `statement_year` with Dec $\rightarrow$ Jan rollover), Tier 3 (look-ahead borrowing from `row[i+1]` to `YYYY-MM-01` for `SALDO AWAL`), and Tier 4 (look-behind inheritance from `row[i-1]`). | Completed |
| **FR-09** | Multi-Signal Consensus Extraction (`component/consensus.py`) | Evaluate Geometric ($x_1$ right-edge column lock & vertical row band), Structural (PDF vector span continuity, attached/adjacent direction markers, dominant font profile), and Lexical (strict financial grammar & alpha-contamination guard) signals per numeric cell. | Completed |
| **FR-10** | Account Partitioning, Page Deduplication & Topological Sorting | Partition pages by `account_number`, drop duplicate pages via transaction tuple hashing (`DUPLICATE_PAGE_DROPPED`), sort pages/rows by `(statement_year, statement_month, printed_page_number, pdf_page_index, row_top_y)`, and assign sequential `transaction_index` ($0 \dots N$). | Completed |
| **FR-11** | Checkpoint & Bucket Reconciler & Digital PDF Forensics (`component/audit.py`, `component/forensics.py`) | Reconcile intermediate rows missing `printed_balance` via `pending_bucket` (`VERIFIED_BY_BUCKET`), log `BALANCE_MISMATCH` without overwriting `printed_balance`, reset `computed_balance = printed_balance` after discrepancies to prevent cascading false positives, check cross-month seam continuity (`MISSING_PERIOD_OR_CONTINUITY_GAP`), and detect PDF tampering (`SUSPICIOUS_PDF_PRODUCER`, `METADATA_MODIFIED`, `FONT_ANOMALY`, `OVERLAID_TEXT`). | Completed |
| **FR-12** | Callable Audit State, Single-Line Logger & Clean Audit UI | Expose `AuditIssue` and `StatementAuditResult` (`issues_df`, `summary_line()`), emit a single tab-aligned console summary line per statement stream, and render the Top Summary Banner + collapsible `View Audit Issues & Discrepancies` table in `app.py`. | Completed |

#### Execution Steps (Spec 003)
* [x] **Schema Humanization & Stateless Page Extraction:** Implement humanized fields on `Cell`/`ExtractedRow`, `PageFingerprint`, dynamic X-density projection, date-anchor row banding, and 4-tier date cascade (`specs/003_consensus_forensic_reconciler/plan.md`).
* [x] **Multi-Signal Consensus, Bucket Reconciler & Forensics Engine:** Create `component/consensus.py`, `component/audit.py`, and `component/forensics.py`, update `component/extractor.py` and `app.py`, and verify end-to-end (`specs/003_consensus_forensic_reconciler/walkthrough.md`).

### Spec 002 — Camera-Photo Detection, Illumination Normalization & Strip-Slope Dewarping (`specs/002_camera_photo_preprocess/`)
**Fokus:** Automatically detect camera-taken bank statement photos (even when embedded inside PDFs) and normalize uneven gray lighting, curved perspective row tilt, and blurry OCR date/amount glitches without affecting digital or clean scanned PDF speed.

#### Functional Requirements (Spec 002)
| ID | Fitur | Deskripsi Detail | Status |
| --- | --- | --- | --- |
| **FR-06** | Fast Camera-Photo Detection & Normalization | `< 1 ms` thumbnail percentile detector (`is_camera_photo`), morphological background division (`normalize_illumination`), 5-band strip-slope row rectification (`dewarp_rows`), and `use_angle_cls = False` to prevent 180° flips on numeric cells. | Completed |
| **FR-07** | Blurry-OCR Date, Amount & Balance Resilience | Recover dates with missing slashes or `O`/`D`/`C`/`Q` leading-zero misreads, normalize `.00D`/`.00K` OCR suffix noise, and apply balance-verified `1000x` `.00D -> .000` scaling and 2-step balance bridge repair in `reconcile()`. | Completed |

#### Execution Steps (Spec 002)
* [x] **Camera-Photo Preprocessing Pipeline:** Implement `is_camera_photo`, `normalize_illumination`, and `dewarp_rows` in `component/ocr.py` (`specs/002_camera_photo_preprocess/plan.md`).
* [x] **Blurry-OCR Parsing & Balance Bridge:** Enhance `parser.py`, `layout.py`, and `balancecalc.py` and verify 21/21 row reconciliation on the camera-photo BRI PDF (`specs/002_camera_photo_preprocess/walkthrough.md`).

### Spec 001 — Core Extraction Pipeline Audit, Deduplication & Refactoring (`specs/001_pipeline_audit_refactor/`)
**Fokus:** Audit, deduplicate, humanize, and future-proof the end-to-end OCR, layout reconstruction, parsing, reconciliation, and export pipeline.

#### Functional Requirements (Spec 001)
| ID | Fitur | Deskripsi Detail | Status |
| --- | --- | --- | --- |
| **FR-01** | Hybrid OCR & Header Detection | Digital text extraction via `PyMuPDF` with automatic `RapidOCR` + deskew fallback; top-30% first-page header/logo detection for bank and account number. | Completed |
| **FR-02** | Adaptive Table Layout Reconstruction | Coordinate-based line grouping, fuzzy header role matching, numeric column clustering, and multi-line transaction merging across pages. | Completed |
| **FR-03** | Unified Direction & Date/Amount Parsing | Consolidated debit/credit marker and narrative hint parsing (fixing `TRSF`/`TRF` collision bug), multi-format Indonesian/English date & time extraction, and currency normalization. | Completed |
| **FR-04** | Balance Chain Reconciliation | Chronological ordering (`as_printed` vs `newest_first`), global/row-level DB/CR swap recovery, missing amount/balance derivation, and confidence scoring. | Completed |
| **FR-05** | Multi-Account Grouping & Analytics Export | Automatic grouping by `(bank, account)`, single-pass date resolution, intra-day chronological sorting, 6-sheet per-account Excel export (`Detail`, `In`, `Out`, `Saldo Mengendap`, `Top 5`, `Review`), and shared UI/Export DataFrame builders. | Completed |

#### Execution Steps (Spec 001)
* [x] **Pipeline Audit & Root Cause Analysis:** Identify redundant functions, dead code, double-pass bugs, and hardcoded settings (`specs/001_pipeline_audit_refactor/analysis.md`).
* [x] **Backend Logic Refactoring:** Centralize env-backed config, unify direction parsers, remove dead `pypdfium2` code, deduplicate `ocr.py`/`layout.py`/`export.py`/`app.py`, and fix multi-document date/reconcile passes.
* [x] **Validation & Regression Testing:** Verify digital PDF, OCR, date/amount parsing, reconciliation, and Excel/JSON export end-to-end (`specs/001_pipeline_audit_refactor/walkthrough.md`).

## P5. Non-Functional Requirements
1. **Security & Forensic Integrity:** 100% local processing with no external API calls; never overwrite or auto-heal tampered printed balances (`printed_balance`); upload size validation (`MAX_UPLOAD_MB`); isolated `TemporaryDirectory` cleanup; Excel formula injection protection (`safe_str`); `.env` secret/config hygiene in `.gitignore`.
2. **Scalability & Performance:** Stateless per-page extraction (`parse_page`), native `PyMuPDF` vector stream fast-path ($\sim 5\text{ ms}$/page) before cold `RapidOCR` fallback, cached `RapidOCR` ONNX singleton, and single-line console logging per account stream.
3. **Maintainability:** Domain-humanized variable and schema names (`detected_amount`, `assigned_column`, `printed_balance`, `computed_balance`, `balance_discrepancy`, `is_reconciled`, `has_printed_balance`, `row_top_y`, `checkpoint_balance`, `clean_description`, `counterparty_name`, `transaction_index`).

## P6. High Level Data Models (`component/structures.py`)
* **`Word`:** `text, x0, y0, x1, y1, xc, yc, h`
* **`Span`:** `text, x0, y0, x1, y1, font, size, color, flags, page`
* **`Cell`:** `text, x0, x1, num_x1, words, money, assigned_column, detected_amount, is_int, y0, y1`
* **`Line`:** `cells, y, text, y0, y1`
* **`ColumnInterval`:** `x0, x1, peak_x, role`
* **`PageFingerprint`:** `account_number, bank_name, statement_period_start, statement_period_end, statement_month, statement_year, printed_page_number, pdf_page_index`
* **`RowBand`:** `index, y_start, y_end, row_top_y, lines, words, spans, date_word, date_parts, row_time, rest_text`
* **`NumericTokenProfile` & `ConsensusResult`:** `value, assigned_column, direction_marker, confidence, s1_geometric_ok, s2_structural_ok, s3_lexical_ok, font_name, font_size, color, width_hesitation, overlaid, notes`
* **`ExtractedRow`:** `transaction_index, page, pdf_page_index, printed_page_number, statement_year, statement_month, row_top_y, raw_text, date, date_parts, description, clean_description, counterparty_name, debit, credit, balance, printed_balance, computed_balance, balance_discrepancy, checkpoint_balance, has_printed_balance, is_reconciled, reconciliation_status, detected_amount, assigned_column, pending_mutasi, is_opening, validation, confidence, extraction_confidence, account, bank, time, token_profiles`
* **`AuditIssue`:** `transaction_index, pdf_page, date, issue_type, severity, printed_value, expected_value, discrepancy, description`
* **`StatementAuditResult`:** `account_number, bank_name, total_pages, total_transactions, is_fully_reconciled, opening_balance, closing_balance, total_debits, total_credits, issues, issues_df, summary_line()`
