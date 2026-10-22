# Indonesian Bank Statement OCR

**Document Type:** PRD & Implementation Roadmap
**Version:** 1.0
**Platform Strategy:** Modular Implementation (Antigravity Framework)

## P1. Executive Summary
Indonesian Bank Statement OCR is a local extraction, reconciliation, and reporting pipeline that converts digital PDFs, scanned PDFs, and statement images from Indonesian banks into structured **Excel (`.xlsx`)** and **JSON** outputs. It automates bank/account grouping across multi-document uploads, reconstructs tabular layouts without hardcoded bank templates, reconciles running balance chains, categorizes transactions, and computes monthly analytics (`Saldo Mengendap` and `Top 5 CR/DB`).

## P2. Tech Stack Recommendation
* **Frontend / UI:** Streamlit (`app.py`)
* **Backend / Extraction Engine:** Python 3.12+ modular package (`component/`)
* **PDF & Text Layer:** `PyMuPDF` (`fitz`), `pdfplumber` (secondary digital text fallback)
* **OCR & Image Processing:** `rapidocr-onnxruntime` (CPU ONNX), `opencv-python-headless`, `numpy`
* **Data & Export:** `pandas`, `openpyxl`
* **Infrastructure:** Docker (`python:3.12-slim`)

## P3. User Roles
1. **Credit Analyst / Reviewer:** Uploads single or multiple bank statement files (PDF/images), inspects extracted transactions and validation flags, reviews monthly analytics (`Saldo Mengendap` & `Top 5 CR/DB`), and exports reconciled Excel/JSON reports.
2. **Downstream Integration / CLI Consumer:** Invokes `extract_statement` or CLI (`python -m component.extractor`) for automated batch extraction and JSON/Excel consumption.

## P4. Specs Catalog (Spec-based Strategy)

| Spec | Fitur | Status | Folder |
| --- | --- | --- | --- |
| **001** | Core Extraction Pipeline Audit, Deduplication & Refactoring | ✅ Completed | `specs/001_pipeline_audit_refactor/` |
| **002** | Camera-Photo Detection, Illumination Normalization & Strip-Slope Dewarping | ✅ Completed | `specs/002_camera_photo_preprocess/` |

### Spec 002 — Camera-Photo Detection, Illumination Normalization & Strip-Slope Dewarping (`specs/002_camera_photo_preprocess/`)
**Fokus:** Automatically detect camera-taken bank statement photos (even when embedded inside PDFs) and normalize uneven gray lighting, curved perspective row tilt, and blurry OCR date/amount glitches without affecting digital or clean scanned PDF speed.

#### Functional Requirements (Spec 002)
| ID | Fitur | Deskripsi Detail | Status |
| --- | --- | --- | --- |
| **FR-06** | Fast Camera-Photo Detection & Normalization | `< 1 ms` thumbnail percentile detector (`is_camera_photo`), morphological background division (`normalize_illumination`), 5-band strip-slope row rectification (`dewarp_rows`), and `use_angle_cls = False` to prevent 180° flips on numeric cells. | ✅ Completed |
| **FR-07** | Blurry-OCR Date, Amount & Balance Resilience | Recover dates with missing slashes or `O`/`D`/`C`/`Q` leading-zero misreads, normalize `.00D`/`.00K` OCR suffix noise, and apply balance-verified `1000x` `.00D -> .000` scaling and 2-step balance bridge repair in `reconcile()`. | ✅ Completed |

#### Execution Steps (Spec 002)
* [x] **Camera-Photo Preprocessing Pipeline:** Implement `is_camera_photo`, `normalize_illumination`, and `dewarp_rows` in `component/ocr.py` (`specs/002_camera_photo_preprocess/plan.md`).
* [x] **Blurry-OCR Parsing & Balance Bridge:** Enhance `parser.py`, `layout.py`, and `balancecalc.py` and verify 21/21 row reconciliation on the camera-photo BRI PDF (`specs/002_camera_photo_preprocess/walkthrough.md`).

### Spec 001 — Core Extraction Pipeline Audit, Deduplication & Refactoring (`specs/001_pipeline_audit_refactor/`)
**Fokus:** Audit, deduplicate, humanize, and future-proof the end-to-end OCR, layout reconstruction, parsing, reconciliation, and export pipeline.

#### Functional Requirements (Spec 001)
| ID | Fitur | Deskripsi Detail | Status |
| --- | --- | --- | --- |
| **FR-01** | Hybrid OCR & Header Detection | Digital text extraction via `PyMuPDF` with `pdfplumber` fallback and automatic `RapidOCR` + deskew fallback; top-30% first-page header/logo detection for bank and account number. | ✅ Completed |
| **FR-02** | Adaptive Table Layout Reconstruction | Coordinate-based line grouping, fuzzy header role matching, numeric column clustering, and multi-line transaction merging across pages. | ✅ Completed |
| **FR-03** | Unified Direction & Date/Amount Parsing | Consolidated debit/credit marker and narrative hint parsing (fixing `TRSF`/`TRF` collision bug), multi-format Indonesian/English date & time extraction, and currency normalization. | ✅ Completed |
| **FR-04** | Balance Chain Reconciliation | Chronological ordering (`as_printed` vs `newest_first`), global/row-level DB/CR swap recovery, missing amount/balance derivation, and confidence scoring. | ✅ Completed |
| **FR-05** | Multi-Account Grouping & Analytics Export | Automatic grouping by `(bank, account)`, single-pass date resolution, intra-day chronological sorting, 6-sheet per-account Excel export (`Detail`, `In`, `Out`, `Saldo Mengendap`, `Top 5`, `Review`), and shared UI/Export DataFrame builders. | ✅ Completed |

#### Execution Steps (Spec 001)
* [x] **Pipeline Audit & Root Cause Analysis:** Identify redundant functions, dead code, double-pass bugs, and hardcoded settings (`specs/001_pipeline_audit_refactor/analysis.md`).
* [x] **Backend Logic Refactoring:** Centralize env-backed config, unify direction parsers, remove dead `pypdfium2` code, deduplicate `ocr.py`/`layout.py`/`export.py`/`app.py`, and fix multi-document date/reconcile passes.
* [x] **Validation & Regression Testing:** Verify digital PDF, OCR, date/amount parsing, reconciliation, and Excel/JSON export end-to-end (`specs/001_pipeline_audit_refactor/walkthrough.md`).

## P5. Non-Functional Requirements
1. **Security:** 100% local processing with no external API calls; upload size validation (`MAX_UPLOAD_MB`); isolated `TemporaryDirectory` cleanup; Excel formula injection protection (`safe_str`); `.env` secret/config hygiene in `.gitignore`.
2. **Scalability & Performance:** Stateless extraction functions, cached `RapidOCR` ONNX singleton, digital text fast-path before OCR, and environment-configurable DPI/thresholds.
3. **Maintainability:** Zero duplicate business logic across modules; clean, human-readable variable and function names.

## P6. High Level Data Models (`component/structures.py`)
* **`Word`:** `text, x0, y0, x1, y1, xc, yc, h`
* **`Cell`:** `text, x0, x1, num_x1, words, money, role, is_int`
* **`Line`:** `cells, y, text`
* **`Layout`:** `align, numeric, tol, text_cols`
* **`ExtractedRow`:** `page, raw_text, date, description, debit, credit, balance, pending_mutasi, is_opening, validation, confidence, account, bank, time`
