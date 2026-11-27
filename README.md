# Indonesian Bank Statement OCR

Indonesian Bank Statement OCR adalah sistem ekstraksi mutasi rekening koran Indonesia (PDF digital, hasil scan, maupun foto kamera) menjadi laporan **Excel (`.xlsx`)**.

## Fitur Utama

- **Multi-Format & Resiliensi Kamera/Scan:** Mendukung PDF digital (ekstraksi vektor `PyMuPDF`), PDF scan/rusak (sanitasi stream + *cold fallback* `RapidOCR`), serta foto kamera (deteksi otomatis foto kamera, normalisasi pencahayaan, *deskew*, dan *strip-slope dewarping*).
- **Ekstraksi Halaman Stateless & Konsensus Multi-Sinyal:** Menggunakan *PageFingerprint*, proyeksi densitas X, *date-anchor row banding*, *4-Tier Date Inheritance Cascade*, dan konsensus 3 sinyal (Geometrik, Struktural PDF, Leksikal) untuk mengekstrak transaksi secara presisi.
- **Partisi Multi-Rekening & Deduplikasi:** Mengelompokkan halaman per `(bank, account)`, membuang halaman duplikat (*transaction tuple hashing*), dan mengurutkan ulang halaman/transaksi secara topologis.
- **Checkpoint & Bucket Reconciler (`audit_ledger`):** Merekonsiliasi saldo berjalan (*bucket accumulation*), mengisolasi diskrepansi saldo tanpa *cascade*, dan memeriksa keberlanjutan antar bulan.
- **Audit Forensik PDF Digital (`run_forensic_audit`):** Mendeteksi indikasi manipulasi PDF (`SUSPICIOUS_PDF_PRODUCER`, `METADATA_MODIFIED`, `FONT_ANOMALY`, `OVERLAID_TEXT`).
- **Ekstraksi Entitas & Kategorisasi:** Ekstraksi `Pihak Terkait` menggunakan model lokal **GLiNER INT8 ONNX** (`model/gliner_int8/`) dengan *fallback* regex, serta kategorisasi transaksi dan klasifikasi arus mutasi otomatis.
- **Executive Credit Memo & Export Multi-Sheet Excel:** Menghasilkan sheet ringkasan eksekutif `00_RINGKASAN` berformula Excel dinamis beserta 6 sheet analitik/audit per rekening.

## Arsitektur

```text
app.py (Streamlit Web UI)
  -> upload multi-file PDF/gambar, Executive Credit Memo Summary, banner status rekonsiliasi,
     dropdown isu audit, preview per rekening, & export Excel/JSON

component/ (Core Extraction, Audit, NER & Reporting Engine)
  __init__.py   -> public API (extract_statement, audit_ledger, run_forensic_audit, TransactionNER,
                   excel_bytes, create_memo_sheet, category, flow, AuditIssue, StatementAuditResult, dll.)

  core.py       -> konstanta konfigurasi, regex domain, toleransi audit, & dataclass model internal
                   (Word, Span, Cell, Line, Layout, PageFingerprint, RowBand, ConsensusResult,
                    ExtractedRow, AuditIssue, StatementAuditResult, StatementSession)

  ingest.py     -> sanitasi & ekstraksi stream vektor PyMuPDF, fallback RapidOCR, praproses foto kamera
                   (illumination normalization, deskew, dewarp), OCR header/logo & audit forensik PDF

  table.py      -> deteksi header tabel, proyeksi densitas X, konsensus multi-sinyal, deteksi overlaid
                   spans, intersepsi baris ringkasan, & pembacaan total tercetak

  page.py       -> ekstraksi halaman stateless, PageFingerprint, row banding berbasis tanggal,
                   & 4-tier date inheritance cascade

  parsing.py    -> parsing nominal uang, tanggal/waktu, periode/halaman, nomor rekening, nama bank,
                   arah mutasi, pembersihan deskripsi, kategorisasi transaksi, & arus mutasi (flow)

  ner.py        -> singleton TransactionNER berbasis GLiNER INT8 ONNX (model/gliner_int8/) dengan
                   regex fallback untuk ekstraksi Pihak Terkait & No. Referensi

  ledger.py     -> Checkpoint & Bucket Reconciler (audit_ledger), pemotongan sesi (slice_into_sessions),
                   deduplikasi halaman/baris, pengurutan kronologis, & cek kontinuitas lintas bulan

  extractor.py  -> orkestrator utama extract_statement() untuk multi-file/multi-rekening & CLI headless

  report.py     -> agregator metrik bulanan/portofolio/counterparty, generator sheet 00_RINGKASAN
                   berformula dinamis, & eksportir Excel multi-sheet (excel_bytes)

model/
  gliner_int8/  -> model NER lokal terkuantisasi INT8 ONNX (model_int8.onnx, tokenizer, inference.py)

functional/
  product_requirement.md, changelog.md, & specs/001..004 -> dokumen PRD, riwayat perubahan, & spesifikasi teknis
```

## Tech Stack

- **Runtime & UI:** Python 3.12+ & Streamlit
- **PDF & Vector Text Layer:** `PyMuPDF` (`fitz`)
- **OCR & Image Processing:** `rapidocr-onnxruntime` (CPU ONNX), `opencv-python-headless`, `numpy`
- **NER (Counterparty & Reference):** `gliner`, `transformers`, `torch` (CPU) + `model/gliner_int8/model_int8.onnx`
- **Data & Export:** `pandas`, `openpyxl`

## Output Utama

- `extract_statement(paths, dpi=220)` — mengekstrak file PDF/gambar sekaligus, dikelompokkan per `(bank, account)`, lengkap dengan `summary`, `audit_result` (`StatementAuditResult` dengan `issues_df` & `summary_line()`), dan `audit_results` per rekening.
- `excel_bytes(result)` — menghasilkan file `.xlsx` dengan struktur:
  - `00_RINGKASAN` — ringkasan eksekutif kredit multi-rekening (Portfolio Overview, Monthly Breakdown Matrix, Category Breakdown, dan Top Counterparties) dengan formula Excel dinamis.
  - Per rekening (6 sheet berindeks `transaction_index`):
    - `Detail <Bank - Rekening>` — seluruh transaksi (`Tanggal`, `Kredit`, `Debit`, `Saldo`, `Deskripsi`, `Pihak Terkait`, `No. Referensi`, `Kategori Transaksi`)
    - `In <Bank - Rekening>` — khusus transaksi uang masuk (`Kredit`)
    - `Out <Bank - Rekening>` — khusus transaksi uang keluar (`Debit`)
    - `Saldo Mengendap <Bank - Rekening>` — saldo akhir harian & *streak* pengendapan per bulan
    - `Top 5 <Bank - Rekening>` — 5 transaksi Kredit & Debit terbesar per bulan
    - `Review <Bank - Rekening>` — halaman, *validation flags*, status rekonsiliasi, dan teks mentah hasil deteksi

## Konfigurasi Environment

Beberapa parameter dapat diatur melalui *environment variable* (opsional):

| Variable | Default | Deskripsi |
| --- | --- | --- |
| `BANK_OCR_DPI` | `220` | Resolusi render OCR halaman |
| `BANK_OCR_HEADER_DPI` | `180` | Resolusi render OCR area header/logo |
| `BANK_OCR_HEADER_RATIO` | `0.3` | Rasio tinggi area header pada halaman pertama |
| `BANK_OCR_AUDIT_TOL` | `0.05` | Toleransi selisih rekonsiliasi saldo |
| `BANK_OCR_MAX_UPLOAD_MB` | `50` | Batas ukuran unggahan file (MB) |
| `BANK_OCR_NER_THRESHOLD` | `0.50` | Ambang skor kepercayaan ekstraksi NER |
| `BANK_OCR_NER_THREADS` | `4` | Jumlah thread CPU untuk inferensi NER |

## Menjalankan

### A. Lokal (Python / Streamlit)

```powershell
python -m pip install -r requirements.txt
streamlit run app.py
```

Lalu buka `http://localhost:8501/`.

### B. Docker

```powershell
docker build -t bank-ocr .
docker run -p 8501:8501 bank-ocr
```

Lalu buka `http://localhost:8501/`.

### C. CLI (Headless)

```powershell
python -m component.extractor statement1.pdf statement2.pdf --output output
```

Hasil ekstraksi akan disimpan ke `output/results.xlsx`.
