# Task Checklist — Spec 003

- [x] Humanize domain & accounting field names (`detected_amount`, `assigned_column`, `printed_balance`, `computed_balance`, `balance_discrepancy`, `is_reconciled`, `has_printed_balance`, `row_top_y`, `checkpoint_balance`, `clean_description`, `counterparty_name`, `transaction_index`) in `component/structures.py`
- [x] Add `AuditIssue`, `StatementAuditResult` (`issues_df`, `summary_line()`), `PageFingerprint`, `RowBand`, `Span`, `NumericTokenProfile`, and `ConsensusResult` in `component/structures.py`
- [x] Implement native vector span extraction (`spans_pymupdf`, `words_pymupdf`) and cold `RapidOCR` fallback in `component/ocr.py` while preserving camera-photo preprocessing
- [x] Implement `extract_page_fingerprint`, `parse_printed_page_number`, `parse_statement_period`, `validate_financial_grammar`, `extract_clean_description`, and `extract_counterparty_name` in `component/parser.py`
- [x] Create `component/consensus.py` implementing 3-signal consensus (`evaluate_signal_1_geometric`, `evaluate_signal_2_structural`, `evaluate_signal_3_lexical`, `resolve_numeric_consensus`)
- [x] Implement stateless per-page extraction (`parse_page`), dynamic X-density projection (`build_x_density_histogram`), date-anchor row banding (`build_date_row_bands`), and 4-tier date inheritance cascade (`apply_dates`) in `component/layout.py`
- [x] Create `component/audit.py` implementing the Checkpoint & Bucket Reconciler (`audit_ledger`), discrepancy isolation (`computed_balance = printed_balance` reset), and cross-month continuity check (`MISSING_PERIOD_OR_CONTINUITY_GAP`)
- [x] Create `component/forensics.py` implementing PDF metadata checks (`SUSPICIOUS_PDF_PRODUCER`, `METADATA_MODIFIED`), `FONT_ANOMALY`, and `OVERLAID_TEXT` detection
- [x] Implement multi-account partitioning, page deduplication (`DUPLICATE_PAGE_DROPPED`), topological sorting, and single-line console logging in `component/extractor.py`
- [x] Update `component/export.py` and `app.py` to index tables by `transaction_index` and render the Top Summary Banner + collapsible `View Audit Issues & Discrepancies` table
- [x] Verify end-to-end with out-of-order pages, duplicate pages, dateless `SALDO AWAL`, BCA intermediate rows without `Saldo`, and isolated balance discrepancies
- [x] Document Spec 003 in `functional/specs/003_consensus_forensic_reconciler/`, `functional/product_requirement.md`, `functional/changelog.md`, and `README.md`
