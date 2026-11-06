from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Optional, Union

import cv2
import numpy as np
import pymupdf as fitz
from rapidocr_onnxruntime import RapidOCR

from .constants import HEADER_DPI, HEADER_RATIO, IMAGE_SUFFIXES
from .layout import find_table_header_band, group_lines, median_height
from .parser import clean_text, detect_account, detect_bank, match_date
from .structures import Span, Word

# Step 1: Suppress direct-to-terminal C library error printing from MuPDF
fitz.TOOLS.mupdf_display_errors(False)

STREAM_ERROR_PATTERNS: tuple[str, ...] = (
    "corrupt object stream",
    "zlib error",
    "incorrect header check",
    "syntax error",
    "damaged",
    "format error",
    "read error",
)

_DOC_SANITIZE_STATE: dict[int, dict[str, Any]] = {}
_PATH_SANITIZE_STATE: dict[str, dict[str, Any]] = {}


def has_mupdf_stream_error(warnings_text: str) -> bool:
    low = (warnings_text or "").lower()
    return any(err in low for err in STREAM_ERROR_PATTERNS)


def get_pdf_sanitize_report(target: Union[str, Path, Any]) -> dict[str, Any]:
    if isinstance(target, (str, Path)):
        key = str(Path(target).resolve())
        return _PATH_SANITIZE_STATE.setdefault(
            key,
            {
                "is_corrupted": False,
                "corrupt_stream_repaired": False,
                "flagged_pages": set(),
                "ocr_fallback_pages": set(),
                "warnings": "",
            },
        )
    return _DOC_SANITIZE_STATE.get(
        id(target),
        {
            "is_corrupted": False,
            "corrupt_stream_repaired": False,
            "flagged_pages": set(),
            "ocr_fallback_pages": set(),
            "warnings": "",
        },
    )


def load_and_sanitize_pdf(file_input: Union[str, Path, bytes]) -> Optional[fitz.Document]:
    """
    Opens a PDF and automatically repairs broken zlib object streams or xref tables
    in-memory before returning the document instance.
    """
    fitz.TOOLS.mupdf_display_errors(False)
    fitz.TOOLS.mupdf_warnings(reset=True)

    path_key: Optional[str] = None
    if isinstance(file_input, (str, Path)):
        path_key = str(Path(file_input).resolve())

    try:
        if isinstance(file_input, (bytes, bytearray)):
            doc = fitz.open(stream=bytes(file_input), filetype="pdf")
        else:
            doc = fitz.open(str(file_input))

        open_warnings = fitz.TOOLS.mupdf_warnings(reset=True)
        warning_chunks: list[str] = [open_warnings] if open_warnings else []
        flagged_pages: set[int] = set()

        # Probe page streams so lazy object streams (e.g., 10 0 R) decompress now
        for page_no, page in enumerate(doc, start=1):
            try:
                _ = page.get_text("words")
            except Exception:
                flagged_pages.add(page_no)
            pw = fitz.TOOLS.mupdf_warnings(reset=True)
            if pw:
                warning_chunks.append(pw)
                if has_mupdf_stream_error(pw):
                    flagged_pages.add(page_no)

        combined_warnings = "\n".join(warning_chunks)
        is_corrupted = bool(flagged_pages) or has_mupdf_stream_error(combined_warnings)
        repaired_ok = False

        if is_corrupted:
            try:
                repaired_bytes = doc.tobytes(
                    garbage=4,     # Remove dead/unreferenced objects and compact
                    clean=True,    # Sanitize content streams and repair internal syntax
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
    tuples = raw_words if raw_words is not None else page.get_text("words")
    words: list[Word] = []
    for item in tuples:
        x0, y0, x1, y1, text = float(item[0]), float(item[1]), float(item[2]), float(item[3]), str(item[4]).strip()
        if not text:
            continue
        b_no = int(item[5]) if len(item) > 5 else 0
        l_no = int(item[6]) if len(item) > 6 else 0
        w_no = int(item[7]) if len(item) > 7 else 0
        words.append(Word(text, x0, y0, x1, y1, b_no, l_no, w_no))
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
    if deskew and min(gray.shape) >= 120:
        scale = 1000 / max(gray.shape)
        small = cv2.resize(gray, None, fx=scale, fy=scale) if scale < 1 else gray
        _, bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        h, w = bw.shape

        def angle_score(angle: float) -> float:
            mat = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
            rotated = cv2.warpAffine(bw, mat, (w, h), flags=cv2.INTER_NEAREST)
            return float(np.var(rotated.sum(axis=1)))

        coarse = max(np.arange(-5, 5.01, 0.5), key=angle_score)
        angle = float(max(np.arange(coarse - 0.5, coarse + 0.51, 0.1), key=angle_score))
        if abs(angle) >= 0.3:
            full_h, full_w = gray.shape
            mat = cv2.getRotationMatrix2D((full_w / 2, full_h / 2), angle, 1.0)
            gray = cv2.warpAffine(
                gray,
                mat,
                (full_w, full_h),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=255,
            )
    cleaned = np.where(gray > 195, 255, gray).astype(np.uint8)
    return cv2.cvtColor(cleaned, cv2.COLOR_GRAY2BGR)


def ocr_words(bgr: np.ndarray) -> list[Word]:
    words: list[Word] = []
    engine = rapidocr_engine()
    result, _ = engine(bgr)
    for box, raw_text, _ in result or []:
        text = clean_text(str(raw_text).replace("|", " "))
        if not text:
            continue
        pts = np.asarray(box, dtype=float)
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


def header_lines_text(words: list[Word]) -> str:
    if not words:
        return ""
    heights = [w.h for w in words if len(w.text) >= 2] or [w.h for w in words]
    height = float(np.median(heights)) if heights else 10.0
    rows: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.yc, w.x0)):
        if rows and abs(word.yc - sum(x.yc for x in rows[-1]) / len(rows[-1])) <= 0.6 * height:
            rows[-1].append(word)
        else:
            rows.append([word])
    return "\n".join(" ".join(w.text for w in sorted(r, key=lambda w: w.x0)) for r in rows)


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
            top_words = [w for w in all_words if w.y0 <= img.shape[0] * HEADER_RATIO]
        txt = header_lines_text(top_words)
        return detect_bank(top_words), detect_account(txt) or ""

    doc = load_and_sanitize_pdf(path)
    if doc is None:
        return "", ""
    try:
        if len(doc) == 0:
            return "", ""
        page = doc[0]
        rect = page.rect

        fitz.TOOLS.mupdf_warnings(reset=True)
        try:
            raw_words = page.get_text("words")
        except Exception:
            raw_words = []
        hdr_warn = fitz.TOOLS.mupdf_warnings(reset=True)

        if len(raw_words) > 0 and not has_mupdf_stream_error(hdr_warn):
            page_words = words_pymupdf(page, raw_words)
            header_band = find_table_header_band(page_words)
            if header_band is not None:
                header_y0 = header_band[0]
            else:
                # Phase 1.2 Fallback: first valid Date + Amount line y0
                height = median_height(page_words)
                lines = group_lines(page_words, height)
                first_date_amt_y0 = next(
                    (
                        ln.y0
                        for ln in lines
                        if any(match_date(c.text) for c in ln.cells[:3])
                        and any(c.money for c in ln.cells)
                    ),
                    rect.y0 + rect.height * HEADER_RATIO,
                )
                header_y0 = first_date_amt_y0

            meta_bottom = max(rect.y0 + 10.0, header_y0 - 2.0)
            metadata_rect = fitz.Rect(rect.x0, rect.y0, rect.x1, meta_bottom)
            top_words = [w for w in page_words if w.y0 <= meta_bottom]
            top_text = page.get_text("text", clip=metadata_rect) or header_lines_text(top_words)
            fitz.TOOLS.mupdf_warnings(reset=True)
            bank = detect_bank(top_words)
            account = detect_account(top_text) or ""

            # Phase 1.2: If bank name is missing from digital text, render ONLY metadata_rect at dpi=180
            if not bank and metadata_rect.height >= 8.0:
                bgr = render_page(page, 180, clip=metadata_rect)
                ocr_meta_words = ocr_words(preprocess(bgr, deskew=False))
                bank = detect_bank(ocr_meta_words)
                if not account:
                    account = detect_account(header_lines_text(ocr_meta_words)) or ""
            return bank, account

        top_y1 = rect.y0 + rect.height * HEADER_RATIO
        metadata_rect = fitz.Rect(rect.x0, rect.y0, rect.x1, top_y1)
        bgr = render_page(page, max(dpi, 180), clip=metadata_rect)
        words = ocr_words(preprocess(bgr, deskew=False))
        ocr_txt = header_lines_text(words)
        bank = detect_bank(words)
        account = detect_account(ocr_txt) or ""
        return bank, account
    finally:
        doc.close()


def iter_pages(
    path: Path,
    dpi: int,
    include_spans: bool = False,
) -> Iterator[tuple]:
    if path.suffix.lower() in IMAGE_SUFFIXES:
        img = cv2.imread(str(path))
        if img is None:
            raise ValueError(f"Could not read the image file: {path.name}")
        words = ocr_words(preprocess(img))
        if include_spans:
            yield 1, words, float(img.shape[1]), "ocr", spans_from_words(words)
        else:
            yield 1, words, float(img.shape[1]), "ocr"
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
                raw_words = page.get_text("words")
            except Exception:
                raw_words = []
                stream_exc = True
            page_warn = fitz.TOOLS.mupdf_warnings(reset=True)
            page_stream_corrupt = stream_exc or has_mupdf_stream_error(page_warn)

            if page_stream_corrupt or (page_no in flagged_pages and len(raw_words) == 0):
                ocr_fallback_pages.add(page_no)
                words, page_w, spans = ocr_page_from_pixmap(page, dpi=max(dpi, 200))
                if include_spans:
                    yield page_no, words, page_w, "ocr_fallback", spans
                else:
                    yield page_no, words, page_w, "ocr_fallback"
                continue

            if len(raw_words) > 0:
                words = words_pymupdf(page, raw_words)
                spans: list[Span] = []
                if include_spans:
                    try:
                        spans = spans_pymupdf(page)
                    except Exception:
                        spans = spans_from_words(words)
                    fitz.TOOLS.mupdf_warnings(reset=True)
                if include_spans:
                    yield page_no, words, float(page.rect.width), "text", spans
                else:
                    yield page_no, words, float(page.rect.width), "text"
            else:
                words, page_w, spans = ocr_page_from_pixmap(page, dpi=max(dpi, 200))
                if report.get("is_corrupted") or page_no in flagged_pages:
                    ocr_fallback_pages.add(page_no)
                    src_label = "ocr_fallback"
                else:
                    src_label = "ocr"
                if include_spans:
                    yield page_no, words, page_w, src_label, spans
                else:
                    yield page_no, words, page_w, src_label
    finally:
        doc.close()
