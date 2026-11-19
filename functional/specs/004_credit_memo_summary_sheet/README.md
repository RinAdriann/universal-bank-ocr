# Spec 004 — Executive Credit Memo Summary Sheet (`00_MEMO_RINGKASAN`)

## Overview
Credit analysts, underwriters, and risk committees currently spend considerable manual effort taking extracted bank statement transactions from individual account sheets (`Detail`, `In`, `Out`, `Saldo Mengendap`) and re-calculating multi-month and multi-account portfolio summaries, monthly turnover matrices, Bouwheer (customer) turnover breakdowns, and leasing/operational expense distributions for credit proposal memos (such as `Contoh-Output_rekening_di_memo.odt`).

Spec 004 integrates an automated, executive-ready **Memo Ringkasan** sheet as the first tab (`00_MEMO_RINGKASAN`) in the exported Excel workbook and adds an in-app Streamlit executive preview.

### Core Objectives
1. **Zero Observable Latency Overhead:** All aggregations are performed in-memory on the final post-NER dataset in `< 50 ms` (< 0.05 seconds), preserving the high-throughput performance of the OCR pipeline.
2. **Formula-Driven Dynamics:** Output tables utilize native Excel formulas (`=SUM(...)`, `=AVERAGE(...)`, and cell-difference formulas) so any post-export manual adjustments in transaction sheets automatically flow into the credit memo summary.
3. **Downstream Separation of Concerns:** Core document parsing (`component/layout.py`) and OCR models remain untouched; logic is isolated in a modular `component/memo.py` aggregator consuming final post-audit `ExtractedRow` objects.
4. **Intelligent Counterparty & Obligation Categorization:** Leverages the existing `TransactionNER` model (`counterparty_name`) and transaction categories to separate legitimate business turnover (Bouwheer) from intra-account internal transfers (`PINBUK`) and identify recurring financing obligations (leasing/credit installments).

---

## Artifacts
- [Implementation Plan](./plan.md)
- [Task Checklist](./task.md)
- [Verification Walkthrough](./walkthrough.md)
