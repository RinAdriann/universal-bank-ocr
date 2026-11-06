# Spec 003 — Multi-Signal Consensus, Digital PDF Forensics, Checkpoint Bucket Reconciler & Callable Audit UI

## Overview
Bank statement uploads frequently combine multi-month or multi-account PDFs that have been merged out of order, contain duplicate pages, omit intermediate running balances on intra-day rows (e.g., BCA statements), omit explicit dates on `SALDO AWAL` rows, or exhibit PDF tampering (consumer PDF editors, font mismatches, overlaid text spans, or mathematical balance discrepancies).

Spec 003 upgrades the extraction and verification architecture with:
1. **Global Variable & Schema Humanization:** Domain-aligned accounting field names (`detected_amount`, `assigned_column`, `printed_balance`, `computed_balance`, `balance_discrepancy`, `is_reconciled`, `has_printed_balance`, `row_top_y`, `checkpoint_balance`, `clean_description`, `counterparty_name`, `transaction_index`) across `component/` and `app.py`.
2. **Stateless Page Extraction & 4-Tier Date Cascade:** Per-page header/footer fingerprinting (`PageFingerprint`) and a 4-tier date inheritance cascade that resolves full dates, partial `DD/MM` dates (with Dec $\rightarrow$ Jan rollover), look-ahead dates for dateless `SALDO AWAL` rows (`YYYY-MM-01`), and look-behind dates for continuation sub-rows.
3. **Multi-Signal Consensus Extraction (`component/consensus.py`):** Evaluates Geometric ($x_1$ right-edge lock & vertical row band), Structural (PDF vector span continuity, direction markers, font profile), and Lexical (strict financial grammar & alpha-contamination guard) signals per numeric token.
4. **Multi-Account Splitting, Page Deduplication & Topological Re-Sequencing (`component/extractor.py`):** Partitions pages by `account_number`, drops duplicate pages via transaction tuple hashing (`DUPLICATE_PAGE_DROPPED`), and topologically sorts pages/rows by `(statement_year, statement_month, printed_page_number, pdf_page_index, row_top_y)` before assigning `transaction_index` ($0 \dots N$).
5. **Checkpoint & Bucket Reconciler (`component/audit.py`) & Digital PDF Forensics (`component/forensics.py`):** Reconciles intermediate rows missing `printed_balance` via bucket accumulation (`VERIFIED_BY_BUCKET`), flags mathematical discrepancies (`BALANCE_MISMATCH`) without overwriting printed numbers, resets `computed_balance = printed_balance` after a discrepancy so a single bad row never cascades, verifies cross-month seam continuity (`MISSING_PERIOD_OR_CONTINUITY_GAP`), and detects PDF metadata/font/overlay tampering (`SUSPICIOUS_PDF_PRODUCER`, `METADATA_MODIFIED`, `FONT_ANOMALY`, `OVERLAID_TEXT`).
6. **Callable Audit State, Single-Line Logger & Streamlit Audit UI (`app.py`):** Exposes `StatementAuditResult` (`issues_df`, `summary_line()`), replaces noisy per-row logs with a single tab-separated summary line per account stream, and renders a top-level status banner and collapsible `View Audit Issues & Discrepancies` table in `app.py`.

## Artifacts
- [Implementation Plan](./plan.md)
- [Task Checklist](./task.md)
- [Verification Walkthrough](./walkthrough.md)
