# Implementation Plan — Spec 002: Camera-Photo Preprocessing & OCR Resilience

## 1. Camera-Photo Detection & Preprocessing (`component/ocr.py`)
- **`is_camera_photo(gray)`:** Downsample to `<= 200x200` and check if the 80th percentile background brightness `< 225` or the spread across the 4 quadrants `>= 15`. Takes `< 1 ms` per page.
- **`normalize_illumination(gray)`:** Estimate local paper illumination via downsampled morphological closing (`31x31` ellipse) + Gaussian blur, then divide `cv2.divide(gray, bg, scale=255)` to produce an even white background while preserving shaded table header bars.
- **`dewarp_rows(gray)`:** Split the page into 5 horizontal strips, find each strip's horizontal text-line angle via projection variance, interpolate the local angle per row `y`, and rectify vertical shear via `cv2.remap` so left-hand dates and right-hand amounts/balances share the same horizontal `y` coordinate.
- **`rapidocr_engine()`:** Set `engine.use_angle_cls = False` so already-deskewed numeric cells (e.g. `3,000.00D`) are not flipped 180° upside-down (`000000'c`) by the box orientation classifier.

## 2. Blurry-OCR Parsing & Reconciliation Resilience (`component/constants.py`, `component/parser.py`, `component/layout.py`, `component/balancecalc.py`)
- **Date resilience (`match_date` & `apply_dates`):** Recover dates when blurry OCR drops one or both slashes in `DD/MM/YY` before a timestamp or misreads a leading `0` as `O`/`D`/`C`/`Q`, and snap isolated single-row month/year digit glitches when both neighboring rows share the exact same `(month, year)`.
- **Amount & balance column resilience (`norm_money`, `parse_amount`, `learn_numeric`, `reconcile`):**
  - Clean OCR character substitutions on decimal suffixes (`.c00`, `.D0k`, `.0oo`, `.00x`, `.00M`, `.004`).
  - Fix `learn_numeric()` so it does not swap away a valid `roles[-1] == "balance"` column and defaults `roles[-1] = "balance"` when the header `Saldo` word was missed.
  - In `reconcile()`, automatically scale `.00D -> .000` (`1000x`) amounts when `signed / 1000` matches the running balance delta or 2-step balance bridge, and repair single-row OCR balance typos when `prev + signed + nxt_signed == nxt.balance`.
