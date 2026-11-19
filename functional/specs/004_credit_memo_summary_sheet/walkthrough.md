# Verification Walkthrough — Spec 004: Executive Credit Memo Summary Sheet

## 1. Benchmark & Latency Verification
Execute the benchmark harness against multi-account datasets to verify performance:
```bash
python -c "
import time, io, openpyxl
from component import extract_statement, excel_bytes

# Test with multi-account input
t0 = time.perf_counter()
# ...
"
```
**Acceptance Criteria:**
- Sheet creation and formula population completes in $< 50\text{ ms}$.
- Total export generation time increases by $< 0.1\text{ s}$.

---

## 2. Excel Structure & Formula Inspection
Inspect the generated workbook using OpenPyXL and spreadsheet software:
1. **Sheet Ordering:** `00_MEMO_RINGKASAN` is at index 0 (first tab displayed).
2. **Formula Integrity:**
   - Multi-Account Total row: `=SUM(E{start}:E{end})` across MD, MK, Selisih, Pengendapan, and Saldo Akhir.
   - Monthly Matrix: `=SUM(B{row}:D{row})` for Totals, `=AVERAGE(B{row}:D{row})` for Averages.
   - Difference column: `={avg_mk_cell} - {avg_md_cell}`.
3. **Number Formatting:**
   - All financial amounts formatted with standard thousand separators: `#,##0.00`.
   - Dates properly formatted as `dd/mm/yyyy`.

---

## 3. Business Rule Validation
1. **Bouwheer vs PINBUK Separation:**
   - Verify that incoming internal transfers matching `PINBUK`, `PEMINDAHAN`, or `OVERBOOKING` are isolated and labeled `Internal`.
   - Verify that corporate counterparties (`PT ...`, `CV ...`) appear in the Bouwheer ranking.
2. **Leasing & Debt Installments:**
   - Verify that recurring debits matching financing entities (`SHINHAN`, `CLIPAN`, `INDOMOBIL`, `HFI`, `DIPO`, etc.) are tagged `Cicilan / Leasing`.
3. **Single Account Graceful Handling:**
   - Verify that single-account uploads render without errors, populating Section 1 with 1 row and correctly rendering Sections 2, 3, and 4.
