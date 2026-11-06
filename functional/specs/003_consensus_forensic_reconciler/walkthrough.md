# Verification Walkthrough — Spec 003

## Test Case: Out-of-Order & Duplicated Multi-Page BCA Statement with Missing Intermediate `Saldo` and Tampered Checkpoint
- **Input (`bca_merged.pdf`):** 3-page synthetic PDF simulating a merged BCA statement (`NO. REKENING : 1234567890`, `PERIODE : OKTOBER 2026`):
  - **Physical Page 1 (`Halaman 2 dari 2` — out of order):**
    - `05/10` `TRSF E-BANKING CR BUDI SANTOSO` | `2.000.000,00 CR` | `10.501.000,00` *(tampered printed balance: off by +1,000.00 from expected `10,500,000.00`)*
    - `06/10` `TARIK TUNAI ATM` | `501.000,00 DB` | `10.000.000,00` *(reconciles cleanly against the previous printed checkpoint `10,501,000.00 - 501,000.00 = 10,000,000.00`)*
  - **Physical Page 2 (`Halaman 1 dari 2`):**
    - Dateless `SALDO AWAL` | `10.000.000,00`
    - `04/10` `TRSF E-BANKING DB PT SUMBER MAKMUR` | `1.000.000,00 DB` | *(no printed balance)*
    - `04/10` `BIAYA ADM` | `500.000,00 DB` | `8.500.000,00`
  - **Physical Page 3 (`Halaman 1 dari 2` — duplicate page):**
    - Exact duplicate of Physical Page 2.

## Verification Results
1. **Page Deduplication:** Physical Page 3 was automatically dropped via `_page_tx_hash` and logged as `DUPLICATE_PAGE_DROPPED` (`pdf_page=3`).
2. **Topological Re-Ordering:** Physical Page 2 (`printed_page_number=1`) was ordered before Physical Page 1 (`printed_page_number=2`), assigning `transaction_index` `0..4`.
3. **4-Tier Date Resolution Cascade:**
   - Row 0 (`SALDO AWAL`) borrowed `(year=2026, month=10)` from Row 1 via Tier 3 look-ahead and resolved to `2026-10-01`.
   - Rows 1–4 resolved partial `DD/MM` dates with `statement_year=2026` via Tier 2 (`2026-10-04`, `2026-10-05`, `2026-10-06`).
4. **Checkpoint & Bucket Reconciliation:**
   - Row 1 (`has_printed_balance=False`) was accumulated in `pending_bucket` and verified when Row 2's checkpoint balance (`8,500,000.00`) matched `10,000,000.00 - 1,000,000.00 - 500,000.00 = 8,500,000.00`, marking Row 1 `VERIFIED_BY_BUCKET` and Row 2 `RECONCILED`.
   - Row 3 (`printed_balance=10,501,000.00` vs `computed_balance=10,500,000.00`) was flagged `DISCREPANCY` (`BALANCE_MISMATCH` at `transaction_index=3`, `discrepancy=-1000.0`) without overwriting `printed_balance`.
   - Because `computed_balance` reset to `10,501,000.00` after Row 3, Row 4 (`10,501,000.00 - 501,000.00 = 10,000,000.00`) reconciled cleanly (`RECONCILED`, `is_reconciled=True`) with zero false-positive cascade.
5. **Single-Line Console Logger & `issues_df`:**
   - Console output emitted exactly one summary line:
     `[FLAGGED] Acct: 1234567890 	| Pages: 3 	| Txns: 5 	| Issues (3): DUPLICATE_PAGE_DROPPED: 1 | BALANCE_MISMATCH: 2`
   - `audit_result.issues_df` and `excel_bytes(res)` generated valid DataFrame and 6-sheet `.xlsx` outputs.
