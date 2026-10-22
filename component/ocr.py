import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import cv2
import numpy as np
import pymupdf as fitz
from rapidocr_onnxruntime import RapidOCR

from .constants import IMAGE_SUFFIXES
from .parser import clean_text, detect_account, detect_bank
from .structures import Word


def words_pymupdf(page: Any) -> list[Word]:
    words: list[Word] = []
    for block in page.get_text("rawdict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            dx, dy = line.get("dir", (1.0, 0.0))
            if abs(dy) > 0.1 or dx < 0.9:
                continue
            cur: list[tuple[str, float, float, float, float]] = []

            def flush() -> None:
                if cur:
                    text = "".join(c[0] for c in cur)
                    x0 = min(c[1] for c in cur)
                    y0 = min(c[2] for c in cur)
                    x1 = max(c[3] for c in cur)
                    y1 = max(c[4] for c in cur)
                    words.append(Word(text, x0, y0, x1, y1))
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
    return words


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


def extract_header_info(path: Path, dpi: int = 160) -> tuple[str, str]:
    bank, account = "", ""
    if path.suffix.lower() in IMAGE_SUFFIXES:
        img = cv2.imread(str(path))
        if img is None:
            return "", ""
        band = img[: max(1, int(img.shape[0] * 0.3)), :]
        words = ocr_words(preprocess(band, deskew=False))
        txt = header_lines_text(words)
        return detect_bank(words), detect_account(txt) or ""

    with fitz.open(str(path)) as doc:
        if len(doc) == 0:
            return "", ""
        page = doc[0]
        rect = page.rect
        top_clip = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * 0.3)
        top_text = page.get_text("text", clip=top_clip)
        if top_text:
            account = detect_account(top_text) or ""

        bgr = render_page(page, dpi, clip=top_clip)
        words = ocr_words(preprocess(bgr, deskew=False))
        ocr_txt = header_lines_text(words)
        bank = detect_bank(words)
        account = account or detect_account(ocr_txt) or ""
        return bank, account


def iter_pages(path: Path, dpi: int) -> Iterator[tuple[int, list[Word], float, str]]:
    if path.suffix.lower() in IMAGE_SUFFIXES:
        img = cv2.imread(str(path))
        if img is None:
            raise ValueError(f"Could not read the image file: {path.name}")
        yield 1, ocr_words(preprocess(img)), float(img.shape[1]), "ocr"
        return

    with fitz.open(str(path)) as doc:
        for page_no, page in enumerate(doc, start=1):
            words = words_pymupdf(page)
            if len(words) >= 8:
                yield page_no, words, float(page.rect.width), "text"
            elif not page.get_images(full=True) and len(page.get_drawings()) < 50:
                yield page_no, words, float(page.rect.width), ("text" if words else "blank")
            else:
                bgr = render_page(page, dpi)
                yield page_no, ocr_words(preprocess(bgr)), float(bgr.shape[1]), "ocr"
