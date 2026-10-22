# Task Checklist (Spec 001)

- [x] Initialize `functional/` governance structure (`product_requirement.md`, `changelog.md`, `specs/001_pipeline_audit_refactor/`)
- [x] Add `.env.example` and update `.gitignore` per Rule 10
- [x] Centralize env-configurable constants and fix `DEBIT_HINT_RE` in `component/constants.py`
- [x] Clean up `component/structures.py` and `component/transactioncat.py`
- [x] Consolidate direction functions and humanize `component/parser.py`
- [x] Refactor and humanize `component/layout.py`
- [x] Deduplicate `component/ocr.py` (reuse `group_lines`, add `render_page`, activate `pdfplumber` fallback, remove dead `pypdfium2` code)
- [x] Remove unused `pypdfium2` from `requirements.txt`
- [x] Refactor `component/balancecalc.py` (`ensure_chronological`, `add_flag`, human-readable variable names)
- [x] Refactor `component/export.py` and share DataFrame/serialization helpers with `app.py`
- [x] Refactor `component/extractor.py` (single `apply_dates` pass, `validate_inputs`, timestamp-aware sort, `dedupe_openings`)
- [x] Clean up stale `.pyc` files and run end-to-end verification tests
