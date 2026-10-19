import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import cv2
import numpy as np
import pdfplumber
import pymupdf as fitz
import pypdfium2
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
                    words.append(Word("".join(c[0] for c in cur), min(c[1] for c in cur), min(c[2] for c in cur), max(c[3] for c in cur), max(c[4] for c in cur)))
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

def words_pdfplumber(page: Any) -> list[Word]:
    return [Word(w["text"], w["x0"], w["top"], w["x1"], w["bottom"]) for w in page.extract_words(use_text_flow=False, extra_attrs=["upright"]) if w.get("upright", True)]

@lru_cache(maxsize=1)
def rapidocr_engine() -> Any:
    return RapidOCR()

def preprocess(image: np.ndarray, deskew: bool = True) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if deskew and min(gray.shape) >= 120:
        scale = 1000 / max(gray.shape)
        small = cv2.resize(gray, None, fx=scale, fy=scale) if scale < 1 else gray
        _, bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        h, w = bw.shape
        score = lambda a: float(np.var(cv2.warpAffine(bw, cv2.getRotationMatrix2D((w / 2, h / 2), a, 1.0), (w, h), flags=cv2.INTER_NEAREST).sum(axis=1)))
        coarse = max(np.arange(-5, 5.01, 0.5), key=score)
        angle = float(max(np.arange(coarse - 0.5, coarse + 0.51, 0.1), key=score))
        if abs(angle) >= 0.3:
            gray = cv2.warpAffine(gray, cv2.getRotationMatrix2D((gray.shape[1] / 2, gray.shape[0] / 2), angle, 1.0), (gray.shape[1], gray.shape[0]), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    return cv2.cvtColor(np.where(gray > 195, 255, gray).astype(np.uint8), cv2.COLOR_GRAY2BGR)

def ocr_words(bgr: np.ndarray) -> list[Word]:
    words: list[Word] = []
    engine = rapidocr_engine()
    result, _ = engine(bgr)
    for box, text, _ in result or []:
        if not (text := clean_text(str(text).replace("|", " "))):
            continue
        pts = np.asarray(box, dtype=float)
        x0, x1, y0, y1 = pts[:, 0].min(), pts[:, 0].max(), pts[:, 1].min(), pts[:, 1].max()
        parts = text.split(" ")
        total, pos = max(sum(len(p) for p in parts) + max(len(parts) - 1, 0), 1), 0
        for part in parts:
            if part:
                words.append(Word(part, x0 + (x1 - x0) * pos / total, y0, x0 + (x1 - x0) * (pos + len(part)) / total, y1))
            pos += len(part) + 1
    return words

# Extract logo & header (30% atas halaman pertama) untuk nama bank dan nomor rekening
def largest_logo_text(words: list[Word]) -> str:
    if not words:
        return ""
    joined = " ".join(w.text for w in words)
    if matched := detect_bank(joined):
        return matched
    alpha = [w for w in words if len(re.sub(r"[^A-Za-z]", "", w.text)) >= 2]
    if not alpha:
        return ""
    tallest = max(alpha, key=lambda w: w.h)
    same_line = [w for w in alpha if abs(w.yc - tallest.yc) < 0.6 * tallest.h and w.h >= 0.7 * tallest.h]
    same_line.sort(key=lambda w: w.x0)
    candidate = clean_text(" ".join(re.sub(r"[^A-Za-z0-9]", "", w.text) for w in same_line)).upper()
    return detect_bank(candidate) or candidate

def header_lines_text(words: list[Word]) -> str:
    if not words:
        return ""
    hs = [w.h for w in words if len(w.text) >= 2] or [w.h for w in words]
    u = float(np.median(hs)) if hs else 10.0
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.yc, w.x0)):
        if rows and abs(w.yc - sum(x.yc for x in rows[-1]) / len(rows[-1])) <= 0.6 * u:
            rows[-1].append(w)
        else:
            rows.append([w])
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
        return detect_bank(txt) or largest_logo_text(words), detect_account(txt) or ""

    if fitz is not None:
        with fitz.open(str(path)) as doc:
            if len(doc) == 0:
                return "", ""
            page = doc[0]
            rect = page.rect
            top_clip = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * 0.3)
            if top_text := page.get_text("text", clip=top_clip):
                bank = detect_bank(top_text) or ""
                account = detect_account(top_text) or ""
                if bank and account:
                    return bank, account

            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=top_clip, alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR if pix.n >= 3 else cv2.COLOR_GRAY2BGR)
            words = ocr_words(preprocess(bgr, deskew=False))
            ocr_txt = header_lines_text(words)
            bank = bank or detect_bank(ocr_txt) or largest_logo_text(words)
            account = account or detect_account(ocr_txt) or ""
            return bank, account

    renderer = pypdfium2.PdfDocument(str(path))
    try:
        if len(renderer) == 0:
            return "", ""
        pil = renderer[0].render(scale=dpi / 72).to_pil().convert("RGB")
        arr = np.array(pil)
        bgr = cv2.cvtColor(arr[: max(1, int(arr.shape[0] * 0.3)), :], cv2.COLOR_RGB2BGR)
        words = ocr_words(preprocess(bgr, deskew=False))
        ocr_txt = header_lines_text(words)
        return detect_bank(ocr_txt) or largest_logo_text(words), detect_account(ocr_txt) or ""
    finally:
        renderer.close()

def extract_logo_text(path: Path, dpi: int = 160) -> str:
    bank, _ = extract_header_info(path, dpi)
    return bank

def iter_pages(path: Path, dpi: int) -> Iterator[tuple[int, list[Word], float, str]]:
    if path.suffix.lower() in IMAGE_SUFFIXES:
        if cv2 is None:
            raise RuntimeError("opencv-python is required for image input")
        if (img := cv2.imread(str(path))) is None:
            raise ValueError(f"Could not read the image file: {path.name}")
        yield 1, ocr_words(preprocess(img)), float(img.shape[1]), "ocr"
        return

    if fitz is not None:
        with fitz.open(str(path)) as doc:
            for number, page in enumerate(doc, start=1):
                words = words_pymupdf(page)
                if len(words) >= 8:
                    yield number, words, float(page.rect.width), "text"
                elif not page.get_images(full=True) and len(page.get_drawings()) < 50:
                    yield number, words, float(page.rect.width), ("text" if words else "blank")
                else:
                    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
                    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR if pix.n >= 3 else cv2.COLOR_GRAY2BGR)
                    yield number, ocr_words(preprocess(bgr)), float(pix.width), "ocr"
        return

    with pdfplumber.open(str(path)) as plumber:
        renderer = pypdfium2.PdfDocument(str(path))
        try:
            for number, page in enumerate(plumber.pages, start=1):
                words = words_pdfplumber(page)
                if len(words) >= 8:
                    yield number, words, float(page.width), "text"
                elif not words and not page.images:
                    yield number, words, float(page.width), "blank"
                else:
                    pil = renderer[number - 1].render(scale=dpi / 72).to_pil().convert("RGB")
                    bgr = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
                    yield number, ocr_words(preprocess(bgr)), float(bgr.shape[1]), "ocr"
        finally:
            renderer.close()