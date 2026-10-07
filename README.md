# Universal Indonesian Bank Statement OCR

This project uses a local hybrid pipeline:

- Digital PDF pages use PyMuPDF word coordinates without OCR.
- Each page uses a conservative quality gate: usable text layers stay on the
  fast PyMuPDF path, while incomplete/mixed pages fall back to RapidOCR.
- Scanned pages are rendered and recognized with one cached RapidOCR CPU engine.
- Rows and cells are inferred from adaptive coordinates rather than bank-specific templates.
- Optional PaddleOCR table recognition can be enabled for borderless tables.
- Indonesian dates and currency values are normalized.
- Mutation amounts embedded in description text are separated using Indonesian
  `DB`/`CR` markers before debit/credit assignment.
- Standalone direction markers such as `D/K`, `DB/CR`, and
  `Debit/Credit` are used when a template has one combined mutation column.
- For a combined mutation column, direction is resolved in this order:
  explicit marker, running-balance delta, transaction description, then a
  flagged fallback. A balance increase means money in/credit; a decrease
  means money out/debit.
- Running balances are reconciled in either printed order, and an omitted
  opening row is inferred from the first balance plus its transaction direction
  when that is mathematically unambiguous.
- Balance checks, OCR confidence, and source text are included in the export for review.

## Run locally

```bash
python -m pip install -r requirements.txt
streamlit run pdfplumber_app.py
```

For a headless/CLI run:

```bash
python parser.py statement.pdf --output output
```

The CLI writes `transactions.xlsx` and coordinate-aware `transactions.json`.

To try optional PaddleOCR table structure recognition:

```bash
python -m pip install -r requirements-table.txt
python parser.py statement.pdf --output output --table-model
```

The Streamlit app has the same opt-in checkbox. PaddleOCR table mode is slower
and is not part of the default path; if it returns no usable transaction rows,
the coordinate extractor is used automatically.

The Excel workbook follows the cleaned statement layout in its first sheet:

`Tanggal | Kredit | Debit | Saldo | Deskripsi | Kategori Transaksi`

The second `Review` sheet contains the page number, confidence, human review
status, validation flags, and full detected text for each extracted row.

The extractor is intentionally conservative: rows that cannot be identified as
date-containing transaction rows are not silently exported. Inspect the
`Validation flags` and `Full detected text` columns and use the JSON bounding
boxes to improve the reconstruction rules for representative documents.

## Package layout

The implementation is organized behind the `component` package boundary:

- `component/extractor.py` contains OCR, layout learning, row assembly, and
  balance-context reconciliation.
- `component/reconciliation.py` exposes balance reconciliation independently
  from file and UI concerns.
- `component/categories.py` contains transaction categorization.
- `component/export.py` contains Excel serialization.
- `parser.py` remains a small compatibility/CLI facade for existing commands.

New application code should import `extract_statement`, `excel_bytes`, and
`category` from `component` rather than importing parser internals.