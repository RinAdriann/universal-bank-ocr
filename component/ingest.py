from __future__ import annotations

from collections import Counter
from functools import lru_cache
import io
from pathlib import Path
from typing import Any, Iterator, Sequence, Union

import cv2
import numpy as np
import pymupdf as fitz
from rapidocr_onnxruntime import RapidOCR

from component.core import (
    AuditIssue,
    EXTRACTION_CONFIDENCE_OVERLAID,
    ExtractedRow,
    HEADER_DPI,
    HEADER_RATIO,
    IMAGE_SUFFIXES,
    NumericTokenProfile,
    STREAM_ERROR_PATTERNS,
    SUSPICIOUS_PDF_EDITORS,
    Span,
    Word,
    add_flag,
    band_words,
    iso_date,
    make_issue,
)
from component.table import detect_overlaid_spans, find_table_header_band, group_lines, median_height
from component.parsing import clean_text, detect_account, detect_bank, match_date


# ---------------------------------------------------------------------------
# PDF Sanitization & Text Extraction
# ---------------------------------------------------------------------------

_PATH_SANITIZE_STATE: dict[str, dict[str, Any]] = {}
_DOC_SANITIZE_STATE: dict[int, dict[str, Any]] = {}


def has_mupdf_stream_error(warnings_text: str | None) -> bool:
    if not warnings_text:
        return False
    lower = warnings_text.lower()
    return any(p in lower for p in STREAM_ERROR_PATTERNS)


def _default_sanitize_report() -> dict[str, Any]:
    return {
        "is_corrupted": False,
        "corrupt_stream_repaired": False,
        "flagged_pages": set(),
        "ocr_fallback_pages": set(),
        "warnings": "",
    }


def get_pdf_sanitize_report(pdf_source: Union[str, Path, Any]) -> dict[str, Any]:
    if isinstance(pdf_source, (str, Path)):
        p_str = str(Path(pdf_source).resolve())
        return _PATH_SANITIZE_STATE.setdefault(p_str, _default_sanitize_report())
    return _DOC_SANITIZE_STATE.setdefault(id(pdf_source), _default_sanitize_report())


def load_and_sanitize_pdf(path_or_bytes: Union[str, Path, bytes, io.BytesIO]) -> Any | None:
    fitz.TOOLS.mupdf_warnings(reset=True)
    is_path = isinstance(path_or_bytes, (str, Path))
    path_key = str(Path(path_or_bytes).resolve()) if is_path else None

    try:
        if isinstance(path_or_bytes, (str, Path)):
            doc = fitz.open(str(path_or_bytes))
        elif isinstance(path_or_bytes, io.BytesIO):
            doc = fitz.open(stream=path_or_bytes.getvalue(), filetype="pdf")
        else:
            doc = fitz.open(stream=path_or_bytes, filetype="pdf")

        init_warn = fitz.TOOLS.mupdf_warnings(reset=True)
        flagged_pages: set[int] = set()

        # Probe page streams so lazy object streams (e.g., 10 0 R) decompress now
        for page_no, page in enumerate(doc, start=1):
            try:
                _ = page.get_text("rawdict")
            except Exception:
                flagged_pages.add(page_no)
            pw = fitz.TOOLS.mupdf_warnings(reset=True)
            if has_mupdf_stream_error(pw):
                flagged_pages.add(page_no)

        combined_warnings = (init_warn or "") + ("\n" if init_warn else "")
        is_corrupted = bool(flagged_pages) or has_mupdf_stream_error(init_warn)
        repaired_ok = False

        if is_corrupted:
            try:
                fitz.TOOLS.mupdf_warnings(reset=True)
                repaired_bytes = doc.tobytes(
                    garbage=4,  # Aggressively de-duplicate and compact objects
                    clean=True,  # Clean and sanitize content streams
                    deflate=True,  # Re-compress streams with fresh, valid zlib headers
                    linear=False,
                )
                doc.close()
                fitz.TOOLS.mupdf_warnings(reset=True)
                doc = fitz.open(stream=repaired_bytes, filetype="pdf")
                repaired_ok = True
            except Exception:
                fitz.TOOLS.mupdf_warnings(reset=True)

        existing_ocr_fb: set[int] = set()
        if path_key and path_key in _PATH_SANITIZE_STATE:
            existing_ocr_fb = set(_PATH_SANITIZE_STATE[path_key].get("ocr_fallback_pages", set()))

        state_info: dict[str, Any] = {
            "is_corrupted": is_corrupted,
            "corrupt_stream_repaired": repaired_ok,
            "flagged_pages": flagged_pages,
            "ocr_fallback_pages": existing_ocr_fb,
            "warnings": combined_warnings,
        }
        _DOC_SANITIZE_STATE[id(doc)] = state_info
        if path_key:
            _PATH_SANITIZE_STATE[path_key] = state_info

        return doc

    except Exception:
        fitz.TOOLS.mupdf_warnings(reset=True)
        return None


def words_pymupdf(page: Any, raw_words: list | None = None) -> list[Word]:
    words: list[Word] = []
    raw_dict = page.get_text("rawdict")
    for b_no, block in enumerate(raw_dict.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for l_no, line in enumerate(block.get("lines", [])):
            dx, dy = line.get("dir", (1.0, 0.0))
            if abs(dy) > 0.1 or dx < 0.9:
                continue
            cur: list[tuple[str, float, float, float, float]] = []
            w_no = 0

            def flush() -> None:
                nonlocal w_no
                if cur:
                    text = "".join(c[0] for c in cur)
                    x0 = min(c[1] for c in cur)
                    y0 = min(c[2] for c in cur)
                    x1 = max(c[3] for c in cur)
                    y1 = max(c[4] for c in cur)
                    words.append(Word(text, x0, y0, x1, y1, b_no, l_no, w_no))
                    w_no += 1
                    cur.clear()

            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    c, (x0, y0, x1, y1) = ch["c"], ch["bbox"]
                    if c.isspace() or (cur and x0 - cur[-1][3] > 0.25 * max(y1 - y0, 1.0)):
                        flush()
                        if c.isspace():
                            continue
                    cur.append((c, x0, y0, x1, y1))
            flush()

    if len(words) > 1:
        deduped: list[Word] = []
        for w in words:
            if any(
                prev.text == w.text
                and abs(prev.x0 - w.x0) <= 0.6
                and abs(prev.y0 - w.y0) <= 0.6
                for prev in deduped[-16:]
            ):
                continue
            deduped.append(w)
        words = deduped
    return words


def spans_pymupdf(page: Any) -> list[Span]:
    spans: list[Span] = []
    text_dict = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            dx, dy = line.get("dir", (1.0, 0.0))
            if abs(dy) > 0.1 or dx < 0.9:
                continue
            for sp in line.get("spans", []):
                raw_text = str(sp.get("text", "")).strip()
                if not raw_text:
                    continue
                bbox = sp.get("bbox", (0.0, 0.0, 0.0, 0.0))
                origin = sp.get("origin", (bbox[0], bbox[3]))
                spans.append(
                    Span(
                        text=raw_text,
                        x0=float(bbox[0]),
                        y0=float(bbox[1]),
                        x1=float(bbox[2]),
                        y1=float(bbox[3]),
                        font=str(sp.get("font", "")),
                        size=round(float(sp.get("size", 0.0)), 2),
                        color=int(sp.get("color", 0)),
                        flags=int(sp.get("flags", 0)),
                        origin_x=float(origin[0]),
                        origin_y=float(origin[1]),
                    )
                )
    return spans


# ---------------------------------------------------------------------------
# Vision & RapidOCR
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def rapidocr_engine() -> RapidOCR:
    engine = RapidOCR()
    engine.use_angle_cls = False
    return engine


def is_camera_photo(gray: np.ndarray) -> bool:
    h, w = gray.shape
    small = cv2.resize(gray, (min(w, 200), min(h, 200)), interpolation=cv2.INTER_AREA)
    if float(np.percentile(small, 80)) < 225.0:
        return True
    sh, sw = small.shape
    quads = (
        float(np.percentile(small[: sh // 2, : sw // 2], 80)),
        float(np.percentile(small[: sh // 2, sw // 2 :], 80)),
        float(np.percentile(small[sh // 2 :, : sw // 2], 80)),
        float(np.percentile(small[sh // 2 :, sw // 2 :], 80)),
    )
    return max(quads) - min(quads) >= 15.0


def normalize_illumination(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape
    small = cv2.resize(gray, (max(1, w // 8), max(1, h // 8)), interpolation=cv2.INTER_AREA)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
    bg_small = cv2.morphologyEx(small, cv2.MORPH_CLOSE, kernel)
    bg_small = cv2.GaussianBlur(bg_small, (31, 31), 0)
    bg = cv2.resize(bg_small, (w, h), interpolation=cv2.INTER_CUBIC)
    return cv2.divide(gray, np.maximum(bg, 1), scale=255)


def dewarp_rows(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape
    scale = 1000 / max(h, w)
    small = cv2.resize(gray, None, fx=scale, fy=scale) if scale < 1 else gray
    _, bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    sh, sw = bw.shape

    def score_band(band: np.ndarray, angle: float) -> float:
        bh, bw_w = band.shape
        mat = cv2.getRotationMatrix2D((bw_w / 2, bh / 2), angle, 1.0)
        rot = cv2.warpAffine(band, mat, (bw_w, bh), flags=cv2.INTER_NEAREST)
        return float(np.var(rot.sum(axis=1)))

    n_strips = 5
    strip_h = sh // n_strips
    angles: list[float] = []
    y_centers: list[float] = []
    for i in range(n_strips):
        y0 = i * strip_h
        y1 = sh if i == n_strips - 1 else (i + 1) * strip_h
        band = bw[y0:y1, :]
        if np.count_nonzero(band) < 50:
            continue
        coarse = max(np.arange(-5, 5.01, 0.5), key=lambda a: score_band(band, a))
        fine = float(max(np.arange(coarse - 0.5, coarse + 0.51, 0.1), key=lambda a: score_band(band, a)))
        angles.append(fine)
        y_centers.append((y0 + y1) / 2 / (scale if scale < 1 else 1.0))

    if len(angles) >= 2:
        ys = np.arange(h, dtype=np.float32)
        interp_angles = np.interp(ys, y_centers, angles)
        slopes = np.tan(np.deg2rad(interp_angles))[:, None]
        xs = (np.arange(w, dtype=np.float32) - w / 2.0)[None, :]
        map_x = np.broadcast_to(np.arange(w, dtype=np.float32)[None, :], (h, w)).copy()
        map_y = (ys[:, None] + slopes * xs).astype(np.float32)
        return cv2.remap(
            gray,
            map_x,
            map_y,
            interpolation=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=255,
        )
    return gray


def preprocess(image: np.ndarray, deskew: bool = True) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if is_camera_photo(gray):
        norm = normalize_illumination(gray)
        if deskew and min(norm.shape) >= 120:
            norm = dewarp_rows(norm)
        return cv2.cvtColor(norm, cv2.COLOR_GRAY2BGR)
    return image


def ocr_words(bgr: np.ndarray) -> list[Word]:
    words: list[Word] = []
    h, w = bgr.shape[:2]
    scale = 2.0 if (0 < min(h, w) < 180 or (0 < max(h, w) < 900 and h > w)) else 1.0
    inp = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale > 1.0 else bgr
    engine = rapidocr_engine()
    result, _ = engine(inp)
    for box, raw_text, _ in result or []:
        text = clean_text(str(raw_text).replace("|", " "))
        if not text:
            continue
        pts = np.asarray(box, dtype=float) / scale
        dx, dy = pts[1, 0] - pts[0, 0], pts[1, 1] - pts[0, 1]
        ew = float(np.hypot(dx, dy))
        eh = float(np.hypot(pts[3, 0] - pts[0, 0], pts[3, 1] - pts[0, 1]))
        if ew > 1.2 * eh and abs(dy) > 0.22 * max(abs(dx), 1.0):
            continue
        x0, x1 = pts[:, 0].min(), pts[:, 0].max()
        y0, y1 = pts[:, 1].min(), pts[:, 1].max()
        parts = text.split(" ")
        total = max(sum(len(p) for p in parts) + max(len(parts) - 1, 0), 1)
        pos = 0
        for part in parts:
            if part:
                w_x0 = x0 + (x1 - x0) * pos / total
                w_x1 = x0 + (x1 - x0) * (pos + len(part)) / total
                words.append(Word(part, w_x0, y0, w_x1, y1))
            pos += len(part) + 1
    return words


def render_page(page: Any, dpi: int, clip: Any = None) -> np.ndarray:
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=clip, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    mode = cv2.COLOR_RGB2BGR if pix.n >= 3 else cv2.COLOR_GRAY2BGR
    return cv2.cvtColor(arr, mode)


# ---------------------------------------------------------------------------
# Page Iteration & OCR Fallback
# ---------------------------------------------------------------------------


def spans_from_words(words: list[Word]) -> list[Span]:
    return [
        Span(
            text=w.text,
            x0=w.x0,
            y0=w.y0,
            x1=w.x1,
            y1=w.y1,
            font="OCR",
            size=round(w.h, 1),
            color=0,
            flags=0,
            origin_x=w.x0,
            origin_y=w.y1,
        )
        for w in words
    ]


def ocr_page_from_pixmap(page: Any, dpi: int = 200) -> tuple[list[Word], float, list[Span]]:
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    img_bytes = pix.tobytes("png")
    arr = np.frombuffer(img_bytes, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        bgr = render_page(page, dpi)
    raw_ocr_words = ocr_words(preprocess(bgr))
    pdf_w = float(page.rect.width) if page.rect.width > 0 else float(bgr.shape[1])
    pdf_h = float(page.rect.height) if page.rect.height > 0 else float(bgr.shape[0])
    sx = pdf_w / max(float(bgr.shape[1]), 1.0)
    sy = pdf_h / max(float(bgr.shape[0]), 1.0)
    words = [
        Word(w.text, w.x0 * sx, w.y0 * sy, w.x1 * sx, w.y1 * sy)
        for w in raw_ocr_words
    ]
    spans = spans_from_words(words)
    return words, pdf_w, spans


def ocr_fallback_single_page(
    path: Union[str, Path],
    page_no: int,
    dpi: int = 200,
) -> tuple[list[Word], float, list[Span]]:
    report = get_pdf_sanitize_report(path)
    report.setdefault("ocr_fallback_pages", set()).add(page_no)
    doc = load_and_sanitize_pdf(path)
    if doc is None:
        return [], 0.0, []
    try:
        if page_no < 1 or page_no > len(doc):
            return [], 0.0, []
        page = doc[page_no - 1]
        words, page_w, spans = ocr_page_from_pixmap(page, dpi=max(dpi, 200))
        return words, page_w, spans
    finally:
        doc.close()


def _iter_pages_raw(
    path: Path,
    dpi: int,
    include_spans: bool = False,
) -> Iterator[tuple[int, list[Word], float, str, list[Span]]]:
    if path.suffix.lower() in IMAGE_SUFFIXES:
        img = cv2.imread(str(path))
        if img is None:
            raise ValueError(f"Could not read the image file: {path.name}")
        words = ocr_words(preprocess(img))
        yield 1, words, float(img.shape[1]), "ocr", spans_from_words(words) if include_spans else []
        return

    doc = load_and_sanitize_pdf(path)
    if doc is None:
        return

    report = get_pdf_sanitize_report(path)
    flagged_pages: set[int] = report.get("flagged_pages", set())
    ocr_fallback_pages: set[int] = report.setdefault("ocr_fallback_pages", set())

    try:
        for page_no, page in enumerate(doc, start=1):
            fitz.TOOLS.mupdf_warnings(reset=True)
            stream_exc = False
            try:
                words = words_pymupdf(page)
            except Exception:
                words = []
                stream_exc = True
            page_warn = fitz.TOOLS.mupdf_warnings(reset=True)
            page_stream_corrupt = stream_exc or has_mupdf_stream_error(page_warn)

            if page_stream_corrupt or (page_no in flagged_pages and len(words) == 0):
                ocr_fallback_pages.add(page_no)
                words, page_w, spans = ocr_page_from_pixmap(page, dpi=max(dpi, 200))
                yield page_no, words, page_w, "ocr_fallback", spans
                continue

            if len(words) >= 8 or (not page.get_images(full=True) and len(page.get_drawings()) < 50):
                src_label = "text" if words else "blank"
                spans: list[Span] = []
                if include_spans and words:
                    try:
                        spans = spans_pymupdf(page)
                    except Exception:
                        spans = spans_from_words(words)
                    fitz.TOOLS.mupdf_warnings(reset=True)
                yield page_no, words, float(page.rect.width), src_label, spans
            else:
                words, page_w, spans = ocr_page_from_pixmap(page, dpi=max(dpi, 200))
                if report.get("is_corrupted") or page_no in flagged_pages:
                    ocr_fallback_pages.add(page_no)
                    src_label = "ocr_fallback"
                else:
                    src_label = "ocr"
                yield page_no, words, page_w, src_label, spans
    finally:
        doc.close()


def iter_pages(
    path: Path,
    dpi: int,
    include_spans: bool = False,
) -> Iterator[tuple]:
    for page_no, words, width, src_label, spans in _iter_pages_raw(path, dpi, include_spans=include_spans):
        yield (page_no, words, width, src_label, spans) if include_spans else (page_no, words, width, src_label)


# ---------------------------------------------------------------------------
# Header Extraction
# ---------------------------------------------------------------------------


def header_lines_text(words: list[Word]) -> str:
    if not words:
        return ""
    heights = [w.h for w in words if len(w.text) >= 2] or [w.h for w in words]
    height = float(np.median(heights)) if heights else 10.0
    rows = band_words(words, 0.6 * height)
    return "\n".join(" ".join(w.text for w in sorted(r, key=lambda w: w.x0)) for r in rows)


def _find_first_date_amount_y0(words: list[Word]) -> float | None:
    height = median_height(words)
    lines = group_lines(words, height)
    return next(
        (
            ln.y0
            for ln in lines
            if any(match_date(c.text) for c in ln.cells[:3])
            and any(c.money for c in ln.cells)
        ),
        None,
    )


def extract_header_info(path: Path, dpi: int = HEADER_DPI) -> tuple[str, str]:
    if path.suffix.lower() in IMAGE_SUFFIXES:
        img = cv2.imread(str(path))
        if img is None:
            return "", ""
        all_words = ocr_words(preprocess(img, deskew=False))
        header_band = find_table_header_band(all_words)
        if header_band is not None:
            meta_y1 = max(1.0, header_band[0] - 2.0)
            top_words = [w for w in all_words if w.y1 <= meta_y1 + 4.0]
        else:
            first_date_amt_y0 = _find_first_date_amount_y0(all_words)
            if first_date_amt_y0 is not None:
                top_words = [w for w in all_words if w.y1 <= first_date_amt_y0 - 2.0]
            else:
                top_words = [w for w in all_words if w.y0 <= img.shape[0] * HEADER_RATIO]
        txt = header_lines_text(top_words)
        return detect_bank(top_words, is_ocr=True), detect_account(txt) or ""

    doc = load_and_sanitize_pdf(path)
    if doc is None:
        return "", ""
    try:
        if len(doc) == 0:
            return "", ""
        first_bank = ""
        for page_idx in range(min(len(doc), 4)):
            page = doc[page_idx]
            rect = page.rect

            fitz.TOOLS.mupdf_warnings(reset=True)
            try:
                page_words = words_pymupdf(page)
            except Exception:
                page_words = []
            hdr_warn = fitz.TOOLS.mupdf_warnings(reset=True)

            if len(page_words) >= 8 and not has_mupdf_stream_error(hdr_warn):
                header_band = find_table_header_band(page_words)
                if header_band is not None:
                    header_y0 = header_band[0]
                else:
                    first_date_amt_y0 = _find_first_date_amount_y0(page_words)
                    header_y0 = (
                        first_date_amt_y0
                        if first_date_amt_y0 is not None
                        else (rect.y0 + rect.height * HEADER_RATIO)
                    )

                meta_bottom = max(rect.y0 + 10.0, header_y0 - 2.0)
                metadata_rect = fitz.Rect(rect.x0, rect.y0, rect.x1, meta_bottom)
                top_words = [w for w in page_words if w.y0 <= meta_bottom]
                top_text = header_lines_text(top_words) or page.get_text("text", clip=metadata_rect)
                fitz.TOOLS.mupdf_warnings(reset=True)
                bank = detect_bank(top_words, is_ocr=False)
                account = detect_account(top_text) or ""

                if not bank and metadata_rect.height >= 8.0:
                    bgr = render_page(page, 180, clip=metadata_rect)
                    ocr_meta_words = ocr_words(preprocess(bgr, deskew=False))
                    bank = detect_bank(ocr_meta_words, is_ocr=True)
                    if not account:
                        account = detect_account(header_lines_text(ocr_meta_words)) or ""

                if not bank:
                    for img_info in page.get_images(full=True):
                        try:
                            ibbox = page.get_image_bbox(img_info) & rect
                        except Exception:
                            continue
                        if ibbox.is_empty or ibbox.width < 15.0 or ibbox.height < 10.0:
                            continue
                        if ibbox.y0 <= meta_bottom or ibbox.y1 >= rect.y0 + rect.height * 0.80:
                            pad_clip = fitz.Rect(
                                max(rect.x0, ibbox.x0 - 4.0),
                                max(rect.y0, ibbox.y0 - 4.0),
                                min(rect.x1, ibbox.x1 + 4.0),
                                min(rect.y1, ibbox.y1 + 4.0),
                            )
                            bgr = render_page(page, 200, clip=pad_clip)
                            img_words = ocr_words(preprocess(bgr, deskew=False))
                            bank = detect_bank(img_words, is_ocr=True)
                            if bank:
                                break
                if bank and not first_bank:
                    first_bank = bank
                if account:
                    return (bank or first_bank), account
                continue

            if page_idx == 0:
                top_y1 = rect.y0 + rect.height * HEADER_RATIO
                metadata_rect = fitz.Rect(rect.x0, rect.y0, rect.x1, top_y1)
                bgr = render_page(page, max(dpi, 180), clip=metadata_rect)
                words = ocr_words(preprocess(bgr, deskew=False))
                ocr_txt = header_lines_text(words)
                bank = detect_bank(words, is_ocr=True)
                account = detect_account(ocr_txt) or ""
                if bank and not first_bank:
                    first_bank = bank
                if account:
                    return (bank or first_bank), account
        return first_bank, ""
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Document Forensics
# ---------------------------------------------------------------------------

fitz.TOOLS.mupdf_display_errors(False)

_fmt_date = iso_date


def _empty_metadata_check(file_name: str) -> dict[str, Any]:
    return {
        "file": file_name,
        "metadata": {},
        "suspicious_editor": None,
        "dates_diverged": False,
        "flags": [],
        "findings": [],
    }


def check_document_metadata(path: Path | str) -> dict[str, Any]:
    file_path = Path(path)
    if file_path.suffix.lower() in IMAGE_SUFFIXES or not file_path.exists():
        return _empty_metadata_check(file_path.name)

    doc = load_and_sanitize_pdf(file_path)
    if doc is None:
        return _empty_metadata_check(file_path.name)
    try:
        meta = dict(doc.metadata or {})
    finally:
        doc.close()

    producer = str(meta.get("producer") or "").strip()
    creator = str(meta.get("creator") or "").strip()
    creation_date = str(meta.get("creationDate") or "").strip()
    mod_date = str(meta.get("modDate") or "").strip()

    combined_sig = f"{producer} {creator}".lower()
    matched_editor = next(
        (editor for editor in SUSPICIOUS_PDF_EDITORS if editor in combined_sig),
        None,
    )
    dates_diverged = bool(creation_date and mod_date and creation_date != mod_date)

    flags: list[str] = []
    findings: list[dict[str, Any]] = []

    if matched_editor:
        flags.append("SUSPICIOUS_PDF_PRODUCER")
        findings.append({
            "flag": "SUSPICIOUS_PDF_PRODUCER",
            "transaction_index": 0,
            "page": 1,
            "file": file_path.name,
            "producer": producer,
            "creator": creator,
            "matched_editor": matched_editor,
            "detail": f"PDF Producer/Creator matches consumer editor '{matched_editor}' (Producer='{producer}', Creator='{creator}').",
        })

    if dates_diverged:
        flags.append("METADATA_MODIFIED")
        findings.append({
            "flag": "METADATA_MODIFIED",
            "transaction_index": 0,
            "page": 1,
            "file": file_path.name,
            "creation_date": creation_date,
            "mod_date": mod_date,
            "detail": f"PDF ModDate ('{mod_date}') diverges from CreationDate ('{creation_date}').",
        })

    return {
        "file": file_path.name,
        "metadata": meta,
        "suspicious_editor": matched_editor,
        "dates_diverged": dates_diverged,
        "flags": flags,
        "findings": findings,
    }


def _format_font_style(flags: int) -> str:
    parts: list[str] = []
    if flags & 16:
        parts.append("bold")
    if flags & 2:
        parts.append("italic")
    if flags & 1:
        parts.append("superscript")
    if flags & 8:
        parts.append("mono")
    elif flags & 4:
        parts.append("serif")
    return "+".join(parts) if parts else "regular"


def dominant_profile_for_tokens(profiles: Sequence[NumericTokenProfile]) -> dict[str, Any]:
    total = max(len(profiles), 1)
    family_counts = Counter(p.font_family for p in profiles if p.font_family)
    subset_counts = Counter(p.subset_prefix for p in profiles if p.subset_prefix)
    size_counts = Counter(round(p.size, 1) for p in profiles if p.size > 0)
    flags_counts = Counter(p.flags for p in profiles)
    color_counts = Counter(p.color for p in profiles)

    dom_family, fam_n = family_counts.most_common(1)[0] if family_counts else ("", 0)
    dom_subset, sub_n = subset_counts.most_common(1)[0] if subset_counts else ("", 0)
    dom_size, size_n = size_counts.most_common(1)[0] if size_counts else (0.0, 0)
    dom_flags, flags_n = flags_counts.most_common(1)[0] if flags_counts else (None, 0)
    dom_color, color_n = color_counts.most_common(1)[0] if color_counts else (None, 0)

    enforce_subset = bool(dom_subset) and (sub_n / total >= 0.75)
    enforce_family = bool(dom_family) and (fam_n / total >= 0.65)
    enforce_size = dom_size > 0 and (size_n / total >= 0.65)
    enforce_flags = dom_flags is not None and (flags_n / total >= 0.65)
    enforce_color = dom_color is not None and (color_n / total >= 0.75)
    return {
        "font_family": dom_family if enforce_family else "",
        "subset_prefix": dom_subset if enforce_subset else "",
        "size": dom_size if enforce_size else 0.0,
        "flags": dom_flags if enforce_flags else None,
        "color": dom_color if enforce_color else None,
    }


def detect_font_anomalies(rows: Sequence[ExtractedRow]) -> list[dict[str, Any]]:
    all_entries: list[tuple[int, ExtractedRow, NumericTokenProfile]] = []
    by_col: dict[str, list[NumericTokenProfile]] = {}

    for idx, row in enumerate(rows):
        tx_idx = getattr(row, "transaction_index", idx)
        for prof in row.numeric_profiles:
            if not prof.font or prof.font == "OCR":
                continue
            all_entries.append((tx_idx, row, prof))
            by_col.setdefault(prof.assigned_column, []).append(prof)

    if len(all_entries) < 3:
        return []

    global_dom = dominant_profile_for_tokens([prof for _, _, prof in all_entries])
    col_dom = {
        col_name: dominant_profile_for_tokens(profs) if len(profs) >= 3 else global_dom
        for col_name, profs in by_col.items()
    }

    anomalies: list[dict[str, Any]] = []
    for tx_idx, row, prof in all_entries:
        dom = col_dom.get(prof.assigned_column, global_dom)
        exp_family = dom["font_family"]
        exp_subset = dom["subset_prefix"]
        exp_size = dom["size"]
        exp_flags = dom["flags"]
        exp_color = dom["color"]

        family_mismatch = bool(exp_family and prof.font_family != exp_family)
        subset_mismatch = bool(exp_subset and prof.subset_prefix != exp_subset)
        size_mismatch = bool(exp_size > 0 and abs(round(prof.size, 1) - exp_size) > 0.25)
        style_mismatch = exp_flags is not None and prof.flags != exp_flags
        color_mismatch = exp_color is not None and prof.color != exp_color

        if family_mismatch or subset_mismatch or size_mismatch or style_mismatch or color_mismatch:
            add_flag(row, "FONT_ANOMALY")
            reasons: list[str] = []
            if family_mismatch:
                reasons.append(f"font_family='{prof.font_family}' vs expected='{exp_family}'")
            if subset_mismatch:
                reasons.append(f"subset_prefix='{prof.subset_prefix}' vs expected='{exp_subset}'")
            if size_mismatch:
                reasons.append(f"size={prof.size}pt vs expected={exp_size}pt")
            if style_mismatch:
                reasons.append(
                    f"style='{_format_font_style(prof.flags)}' vs expected='{_format_font_style(exp_flags)}'"
                )
            if color_mismatch:
                reasons.append(f"color=#{prof.color:06x} vs expected=#{exp_color:06x}")
            anomalies.append({
                "flag": "FONT_ANOMALY",
                "row_index": tx_idx,
                "transaction_index": tx_idx,
                "page": row.pdf_page_index,
                "date": _fmt_date(row.date),
                "column": prof.assigned_column,
                "text": prof.text,
                "value": prof.detected_amount,
                "font": prof.font,
                "font_family": prof.font_family,
                "subset_prefix": prof.subset_prefix,
                "size": prof.size,
                "style": _format_font_style(prof.flags),
                "color": f"#{prof.color:06x}",
                "expected_font_family": exp_family,
                "expected_subset_prefix": exp_subset,
                "expected_size": exp_size,
                "expected_style": _format_font_style(exp_flags) if exp_flags is not None else "",
                "expected_color": f"#{exp_color:06x}" if exp_color is not None else "",
                "detail": f"Transaction {tx_idx} ({prof.assigned_column}='{prof.text}'): " + ", ".join(reasons),
            })

    return anomalies


def detect_overlaid_streams(
    page_no: int,
    spans: Sequence[Span],
    rows: Sequence[ExtractedRow] | None = None,
    overlaid_pairs: Sequence[tuple[Span, Span]] | None = None,
) -> list[dict[str, Any]]:
    pairs = overlaid_pairs if overlaid_pairs is not None else detect_overlaid_spans(spans)
    findings: list[dict[str, Any]] = []
    if not pairs:
        return findings

    page_rows = [r for r in (rows or []) if r.pdf_page_index == page_no]
    for sa, sb in pairs:
        ox0 = min(sa.x0, sb.x0)
        oy0 = min(sa.y0, sb.y0)
        ox1 = max(sa.x1, sb.x1)
        oy1 = max(sa.y1, sb.y1)
        matched_row: ExtractedRow | None = None
        for r in page_rows:
            for prof in r.numeric_profiles:
                if max(ox0, prof.x0) < min(ox1, prof.x1) and max(oy0, prof.y0) < min(oy1, prof.y1):
                    matched_row = r
                    add_flag(r, EXTRACTION_CONFIDENCE_OVERLAID)
                    r.extraction_confidence = EXTRACTION_CONFIDENCE_OVERLAID
                    break
        for r in page_rows:
            if EXTRACTION_CONFIDENCE_OVERLAID in r.validation:
                r.extraction_confidence = EXTRACTION_CONFIDENCE_OVERLAID

        tx_idx = matched_row.transaction_index if matched_row is not None else 0
        findings.append({
            "flag": EXTRACTION_CONFIDENCE_OVERLAID,
            "page": page_no,
            "row_index": tx_idx,
            "transaction_index": tx_idx,
            "date": _fmt_date(matched_row.date) if matched_row is not None else "",
            "printed_value": matched_row.printed_balance if matched_row is not None else None,
            "bbox": [round(ox0, 2), round(oy0, 2), round(ox1, 2), round(oy1, 2)],
            "span_a": {"text": sa.text, "font": sa.font, "size": sa.size},
            "span_b": {"text": sb.text, "font": sb.font, "size": sb.size},
            "detail": (
                f"Overlaid text spans on page {page_no} at "
                f"({ox0:.1f}, {oy0:.1f}, {ox1:.1f}, {oy1:.1f}): "
                f"'{sa.text}' overlaps '{sb.text}'."
            ),
        })
    return findings


def run_forensic_audit(
    paths: Sequence[Path | str],
    rows: Sequence[ExtractedRow],
    page_overlaid: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metadata_checks = [check_document_metadata(p) for p in paths]
    font_anomalies = detect_font_anomalies(rows)
    overlaid_findings: list[dict[str, Any]] = []

    for item in page_overlaid or []:
        updated = dict(item)
        bbox = updated.get("bbox")
        page_no = updated.get("page", 1)
        if bbox and rows:
            ox0, oy0, ox1, oy1 = bbox
            for r in rows:
                if r.pdf_page_index != page_no:
                    continue
                for prof in r.numeric_profiles:
                    if max(ox0, prof.x0) < min(ox1, prof.x1) and max(oy0, prof.y0) < min(oy1, prof.y1):
                        updated["transaction_index"] = r.transaction_index
                        updated["row_index"] = r.transaction_index
                        updated["date"] = _fmt_date(r.date)
                        updated["printed_value"] = prof.detected_amount
                        break
        overlaid_findings.append(updated)

    flags: list[str] = []
    for mc in metadata_checks:
        for fl in mc["flags"]:
            if fl not in flags:
                flags.append(fl)
    if font_anomalies and "FONT_ANOMALY" not in flags:
        add_flag(flags, "FONT_ANOMALY")
    if (
        overlaid_findings or any(EXTRACTION_CONFIDENCE_OVERLAID in r.validation for r in rows)
    ):
        add_flag(flags, EXTRACTION_CONFIDENCE_OVERLAID)

    all_findings: list[dict[str, Any]] = []
    for mc in metadata_checks:
        all_findings.extend(mc["findings"])
    all_findings.extend(font_anomalies)
    all_findings.extend(overlaid_findings)

    forensic_issues: list[AuditIssue] = []
    for mc in metadata_checks:
        for f in mc["findings"]:
            forensic_issues.append(
                make_issue(
                    transaction_index=0,
                    pdf_page=1,
                    date=_fmt_date(rows[0].date) if rows else "",
                    issue_type=f["flag"],
                    severity="HIGH" if f["flag"] == "SUSPICIOUS_PDF_PRODUCER" else "MEDIUM",
                    printed_value=None,
                    expected_value=None,
                    discrepancy=None,
                    description=f["detail"],
                )
            )
    for fa in font_anomalies:
        forensic_issues.append(
            make_issue(
                transaction_index=fa["transaction_index"],
                pdf_page=fa["page"],
                date=fa.get("date", ""),
                issue_type="FONT_ANOMALY",
                severity="HIGH",
                printed_value=fa["value"],
                expected_value=None,
                discrepancy=None,
                description=fa["detail"],
            )
        )
    for ov in overlaid_findings:
        forensic_issues.append(
            make_issue(
                transaction_index=ov.get("transaction_index", 0),
                pdf_page=ov.get("page", 1),
                date=ov.get("date", ""),
                issue_type=EXTRACTION_CONFIDENCE_OVERLAID,
                severity="HIGH",
                printed_value=ov.get("printed_value"),
                expected_value=None,
                discrepancy=None,
                description=ov["detail"],
            )
        )

    return {
        "is_suspicious": bool(flags),
        "flags": flags,
        "metadata_checks": metadata_checks,
        "font_anomalies": font_anomalies,
        "overlaid_streams": overlaid_findings,
        "findings": all_findings,
        "audit_issues": forensic_issues,
    }
