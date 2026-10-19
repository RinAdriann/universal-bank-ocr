# Indonesian Bank Statement OCR

Snapshot dokumentasi: 2026-10-19 (v0.2 — Python/Streamlit & RapidOCR)

Indonesian Bank Statement OCR adalah layanan ekstraksi lokal untuk membaca, merekonsiliasi, dan mengekspor mutasi rekening koran (PDF digital, hasil scan, maupun gambar) dari berbagai bank di Indonesia menjadi laporan **Excel (`.xlsx`)** dan **JSON** secara otomatis.

> **Update v0.2:** mendukung upload **multi-dokumen** sekaligus dengan pengelompokan otomatis berdasarkan **nama bank & nomor rekening**, deteksi header/logo pada 30% area atas halaman pertama, analitik bulanan (**Saldo Mengendap** & **Top 5 CR/DB**), serta pemrosesan lokal menggunakan direktori sementara terisolasi demi menjaga privasi data. Riwayat perubahan terdokumentasi di `CHANGELOG.md`.

## Arsitektur

```text
app.py (Streamlit Web UI)
  -> upload multi-file PDF/gambar, preview tabel & analitik, export Excel/JSON

component/ (Core Extraction Engine)
  __init__.py         -> public API (extract_statement, extract_statements, excel_bytes, category, flow)
  ocr.py              -> ekstraksi teks PyMuPDF/pdfplumber + fallback RapidOCR, deskew, & header/logo OCR (top 30%)
  layout.py           -> rekonstruksi baris/kolom adaptif berbasis koordinat, deteksi header & tanggal
  parser.py           -> parsing nominal uang, tanggal/waktu, nomor rekening, nama bank, & arah mutasi
  balancecalc.py      -> rekonsiliasi rantai saldo (opening/closing), koreksi arah DB/CR, & skor confidence
  transactioncat.py   -> kategorisasi transaksi otomatis & klasifikasi arus kas (flow)
  export.py           -> serialisasi multi-sheet Excel (Detail, In, Out, Saldo Mengendap, Top 5, Review)
  structures.py       -> dataclass model internal (Word, Cell, Line, Layout, ExtractedRow, Amount, State)
  constants.py        -> kamus regex tanggal, pola bank Indonesia, nomor rekening, & aturan kategori
```

## Tech Stack

- **Runtime & UI:** Python 3.12+ + Streamlit
- **PDF & Text Layer:** `PyMuPDF` (`fitz`), `pdfplumber`, `pypdfium2`
- **OCR & Image Processing:** `rapidocr-onnxruntime` (CPU ONNX) + `opencv-python-headless` + `numpy`
- **Data & Export:** `pandas` + `openpyxl`
- **Deployment:** Docker (`python:3.12-slim`)

## Fitur & Output Utama

- `extract_statement(paths, dpi=220)` — ekstraksi satu atau banyak file PDF/gambar sekaligus, dikelompokkan per `(bank, account)`.
- `excel_bytes(result)` — menghasilkan file `.xlsx` dengan 6 sheet per rekening:
  - `Detail <Bank - Rekening>` — seluruh transaksi (`Tanggal`, `Kredit`, `Debit`, `Saldo`, `Deskripsi`, `Kategori Transaksi`)
  - `In <Bank - Rekening>` — khusus transaksi uang masuk (`Kredit`)
  - `Out <Bank - Rekening>` — khusus transaksi uang keluar (`Debit`)
  - `Saldo Mengendap <Bank - Rekening>` — saldo akhir harian per bulan
  - `Top 5 <Bank - Rekening>` — 5 transaksi Kredit & Debit terbesar per bulan
  - `Review <Bank - Rekening>` — halaman, *validation flags*, dan teks mentah hasil deteksi
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

Hasil ekstraksi akan disimpan ke `output/results.xlsx` beserta ringkasan rekonsiliasi di terminal.

## Struktur File & Dokumentasi

- `app.py` — antarmuka web Streamlit, indikator waktu proses *real-time*, preview per rekening, dan tombol unduh Excel/JSON.
- `component/` — modul inti ekstraksi, OCR, parser, rekonsiliasi saldo, kategorisasi, dan pembuatan Excel.
- `CHANGELOG.md` — catatan riwayat perubahan dan rencana pengembangan berikutnya.
- `Dockerfile` & `requirements.txt` — konfigurasi *container* dan daftar dependensi Python.

## Catatan Penting

- **Hybrid Pipeline:** halaman PDF digital langsung diekstrak melalui koordinat kata `PyMuPDF` tanpa OCR; halaman scan atau gambar otomatis dialihkan ke `RapidOCR` dengan *preprocessing* dan *deskew*.
- **Deteksi Bank & Rekening (Top 30% Header):** sistem memeriksa teks digital dan melakukan OCR pada 30% bagian atas halaman pertama untuk mengenali nama/logo bank serta nomor rekening secara akurat.
- **Rekonsiliasi Otomatis:** urutan transaksi (*oldest-first* vs *newest-first*), kolom mutasi tunggal, maupun posisi Debit/Kredit yang tertukar dikoreksi otomatis berdasarkan selisih rantai saldo (`chain_reconciles`).
- **Privasi & Keamanan Data:** seluruh proses berjalan 100% lokal tanpa panggilan API eksternal, file upload dibersihkan otomatis melalui `TemporaryDirectory`, dan sel teks di Excel dilindungi dari *formula injection*.