# Universal Indonesian Bank Statement OCR

This project uses a local hybrid pipeline:

- Digital PDF pages use PyMuPDF word coordinates without OCR.
- Each page uses a conservative quality gate: usable text layers stay on the
  fast PyMuPDF path, while incomplete/mixed pages fall back to RapidOCR.
- Scanned pages are rendered and recognized with one RapidOCR CPU engine.
- Rows and cells are inferred from adaptive coordinates rather than bank-specific templates.

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

The Excel workbook follows the cleaned statement layout:

The first sheet contains `date, credit, debit, balance, description and category`.

The second sheet contains `transaction in`.

The third sheet contains `transaction out`

The fourth sheet contains contains the page number and full detected text for each extracted row.

## Package layout

The implementation is organized behind the `component` package boundary:

- `ocr.py` contains the OCR part, extract everything from pdf
- `extractor.py` 
- `reconciliation.py` exposes balance reconciliation independently
  from file and UI concerns.
- `categories.py` contains transaction categorization.
- `export.py` contains Excel serialization.
- `parser.py` remains a small compatibility/CLI facade for existing commands.

New application code should import `extract_statement`, `excel_bytes`, and
`category` from `component` rather than importing parser internals.