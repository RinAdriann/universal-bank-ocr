# Indonesian Bank Statement OCR - Changelog

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
