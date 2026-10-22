# Verification Walkthrough — Spec 002

## Test Case: Camera-Photo BRI Statement Wrapped in PDF
- **Input:** 1-page PDF containing a `716x1017` JPEG camera photo with gray uneven lighting (`p80 = 169.0`) and curved perspective tilt (`-0.8°` at top to `0.0°` at bottom).
- **Before Spec 002:**
  - Global threshold `gray > 195 -> 255` left the entire background gray and erased shaded headers.
  - Perspective row curve caused left-side dates and right-side amounts/balances to split across separate vertical `Line` objects.
  - `RapidOCR` angle classifier flipped symmetric amounts (`3,000.00D` -> `000000'c`).
- **After Spec 002:**
  - `is_camera_photo` detected the camera photo in `< 1 ms`.
  - `normalize_illumination` + `dewarp_rows` aligned all 21 transaction rows and preserved the table header (`['debit', 'credit', 'balance']`).
  - `bank = 'BBRI'` and `account = '808301018733530'` were detected from Page 1 without any bank regex templates.
  - All **21 / 21** transactions extracted with exact dates, descriptions, debits (`13,173,000.0`), credits (`14,500,000.0`), opening balance (`558,350.0`), closing balance (`1,885,350.0`), and `chain_reconciles = True` with 0 `balance_mismatch` flags.
