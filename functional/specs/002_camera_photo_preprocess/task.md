# Task Checklist — Spec 002

- [x] Save pre-Spec-002 stable rollback backup outside the repository (`scratch/stable_backup/`)
- [x] Implement `is_camera_photo`, `normalize_illumination`, `dewarp_rows`, and `engine.use_angle_cls = False` in `component/ocr.py`
- [x] Add blurry-OCR date & amount normalization in `component/constants.py` and `component/parser.py`
- [x] Fix balance column assignment in `learn_numeric`, capture pre-date description lines, and add neighbor month/year consistency in `component/layout.py`
- [x] Add `1000x` `.00D -> .000` balance-verified scaling and 2-step balance bridge repair in `component/balancecalc.py`
- [x] Verify end-to-end extraction on the BRI camera-photo PDF (21/21 rows, `chain_reconciles = True`, `bank = BBRI`, `account = 808301018733530`)
- [x] Sync `Me\bank-ocr` and `Work\bank-ocr` and update `product_requirement.md` and `changelog.md`
