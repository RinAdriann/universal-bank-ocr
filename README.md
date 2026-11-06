# Indonesian Bank Statement OCR

Snapshot dokumentasi: 2026-11-05 (v0.3 — Multi-Signal Consensus, Forensic Audit & Checkpoint Reconciler)

Indonesian Bank Statement OCR adalah layanan ekstraksi, konsensus multi-sinyal, audit forensik, dan rekonsiliasi lokal untuk membaca dan mengekspor mutasi rekening koran (PDF digital, hasil scan, maupun foto kamera) dari berbagai bank di Indonesia menjadi laporan **Excel (`.xlsx`)** dan **JSON** secara otomatis.

> **Update v0.3:** mendukung ekstraksi halaman *stateless* dengan *4-Tier Date Inheritance Cascade*, pengurutan ulang halaman acak/duplikat (*Topological Re-Ordering*), *Checkpoint & Bucket Reconciler* (untuk mutasi BCA tanpa saldo antara), deteksi forensik manipulasi PDF (`SUSPICIOUS_PDF_PRODUCER`, `METADATA_MODIFIED`, `FONT_ANOMALY`, `OVERLAID_TEXT`), *Single-Line Console Logger*, serta banner status & tabel dropdown isu audit di Streamlit UI.

## Arsitektur

```text
app.py (Streamlit Web UI)
  -> upload multi-file PDF/gambar, banner status rekonsiliasi & dropdown isu audit (issues_df), preview tabel & analitik, export Excel/JSON

component/ (Core Extraction, Consensus & Forensic Engine)
  __init__.py         -> public API (extract_statement, extract_statements, audit_ledger, run_forensic_audit, excel_bytes, category, flow, AuditIssue, StatementAuditResult)
  ocr.py              -> ekstraksi stream vektor PyMuPDF (words & spans) + cold fallback RapidOCR, normalisasi foto kamera, deskew, & header/logo OCR
  layout.py           -> ekstraksi halaman stateless, PageFingerprint, proyeksi densitas X, row banding berbasis tanggal, & 4-tier date cascade
  consensus.py        -> ekstraksi konsensus multi-sinyal (Geometrik x1 lock, Struktural PDF span/font/overlay, Leksikal)
  parser.py           -> parsing nominal uang, tanggal/waktu, periode/halaman, nomor rekening, nama bank, arah mutasi, & clean_description/counterparty_name
  audit.py            -> Checkpoint & Bucket Reconciler (audit_ledger), isolasi diskrepansi saldo, & cek kontinuitas lintas bulan
  forensics.py        -> audit forensik PDF digital (metadata editor konsumen, ModDate != CreationDate, anomali font, & teks tumpang-tindih)
  balancecalc.py      -> re-export kompatibilitas rekonsiliasi saldo dari audit.py
  transactioncat.py   -> kategorisasi transaksi otomatis & klasifikasi arus kas (flow)
  export.py           -> serialisasi multi-sheet Excel (Detail, In, Out, Saldo Mengendap, Top 5, Review) berindeks transaction_index
  structures.py       -> dataclass model internal (Word, Span, Cell, Line, Layout, PageFingerprint, RowBand, ConsensusResult, ExtractedRow, AuditIssue, StatementAuditResult)
  constants.py        -> kamus regex tanggal, aturan finansial, toleransi audit, daftar PDF editor mencurigakan, & aturan kategori
```

## Tech Stack

- **Runtime & UI:** Python 3.12+ + Streamlit
- **PDF & Text Layer:** `PyMuPDF` (`fitz`)
- **OCR & Image Processing:** `rapidocr-onnxruntime` (CPU ONNX) + `opencv-python-headless` + `numpy`
- **Data & Export:** `pandas` + `openpyxl`
- **Deployment:** Docker (`python:3.12-slim`)

## Fitur & Output Utama

- `extract_statement(paths, dpi=220)` — ekstraksi satu atau banyak file PDF/gambar sekaligus, dikelompokkan per `(bank, account)`, lengkap dengan objek `StatementAuditResult` (`audit_result.issues_df` & `audit_result.summary_line()`).
- `excel_bytes(result)` — menghasilkan file `.xlsx` dengan 6 sheet per rekening (berindeks `transaction_index`):
  - `Detail <Bank - Rekening>` — seluruh transaksi (`Tanggal`, `Kredit`, `Debit`, `Saldo`, `Deskripsi`, `Kategori Transaksi`)
  - `In <Bank - Rekening>` — khusus transaksi uang masuk (`Kredit`)
  - `Out <Bank - Rekening>` — khusus transaksi uang keluar (`Debit`)
  - `Saldo Mengendap <Bank - Rekening>` — saldo akhir harian per bulan
  - `Top 5 <Bank - Rekening>` — 5 transaksi Kredit & Debit terbesar per bulan
  - `Review <Bank - Rekening>` — halaman, *validation flags*, status rekonsiliasi, dan teks mentah hasil deteksi
- `saldo_mengendap(rows)` & `top5_monthly(rows)` — fungsi analitik bulanan yang dipakai bersama oleh UI dan eksportir Excel.

## Menjalankan

### A. Lokal (Python / Streamlit)

```powershell
python -m pip install -r requirements.txt
streamlit run app.py
```

Lalu buka: `http://localhost:8501/`

### B. Docker (disarankan untuk deployment)

```powershell
docker build -t bank-ocr .
docker run -p 8501:8501 bank-ocr
```

Lalu buka: `http://localhost:8501/`

### C. CLI (Headless)

```powershell
python -m component.extractor statement1.pdf statement2.pdf --output output
```

Hasil ekstraksi akan disimpan ke `output/results.xlsx` beserta satu baris ringkasan audit per rekening di terminal.

## Struktur File & Dokumentasi

- `app.py` — antarmuka web Streamlit, banner status audit, dropdown `View Audit Issues & Discrepancies`, preview per rekening, dan tombol unduh Excel/JSON.
- `component/` — modul inti ekstraksi, konsensus, forensik, parser, rekonsiliasi saldo, kategorisasi, dan pembuatan Excel.
- `functional/` — dokumen PRD (`product_requirement.md`), `changelog.md`, dan spesifikasi teknis per rilis (`functional/specs/001..003`).
- `Dockerfile` & `requirements.txt` — konfigurasi *container* dan daftar dependensi Python.
