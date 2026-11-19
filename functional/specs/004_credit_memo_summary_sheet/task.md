# Task Checklist — Spec 004: Executive Credit Memo Summary Sheet

- [x] **Phase 1: Analytical Module Implementation (`component/memo.py`)**
  - [x] Implement `get_account_holder_name(acc_data, default="")` to resolve account holder name from metadata or row data
  - [x] Implement `calculate_monthly_metrics(rows)` computing monthly MD, MK, Saldo Mengendap (daily closing average), and Saldo Akhir
  - [x] Implement `calculate_category_breakdown(rows)` aggregating amounts by standard `category` from `transactioncat.py` across months
  - [x] Implement `calculate_top_counterparties(rows, is_credit, top_n=8)` ranking top counterparties by volume from NER
  - [x] Implement `create_memo_sheet(wb, result, title="00_MEMO_RINGKASAN")` with professional styling, font palettes, and dynamic Excel formulas (`=SUM`, `=AVERAGE`, difference formulas)

- [x] **Phase 2: Excel Export Pipeline Integration (`component/export.py`)**
  - [x] Import `create_memo_sheet` from `.memo` in `component/export.py`
  - [x] Update `excel_bytes(result)` to call `create_memo_sheet` on `writer.book` ensuring sheet index 0 placement
  - [x] Ensure backward compatibility with single-account and multi-account result formats

- [x] **Phase 3: Streamlit UI Executive Summary Preview (`app.py`)**
  - [x] Implement `render_memo_summary(result)` in `app.py`
  - [x] Add collapsible expander `st.expander("Executive Credit Memo Summary", expanded=True)` displaying the multi-account overview table and key credit metrics
  - [x] Verify Streamlit responsiveness and layout alignment

- [x] **Phase 4: Unit Testing & Verification**
  - [x] Verify execution time remains $< 50\text{ ms}$ for multi-account datasets
  - [x] Verify Excel output structure and formula evaluations in Microsoft Excel / LibreOffice Calc
  - [x] Verify edge cases:
    - Single-account statement
    - Statements with only 1 month of transactions
    - Statements with all zero credits or all zero debits
    - Transactions with missing `counterparty_name`
  - [x] Verify that opening rows (`is_opening = True`) do not contaminate turnover or monthly totals

- [x] **Phase 5: Documentation & Changelog**
  - [x] Update `functional/changelog.md` with Update-005
  - [x] Update `functional/product_requirement.md` to reflect the Executive Credit Memo feature
  - [x] Update repository `README.md`
