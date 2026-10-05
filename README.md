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