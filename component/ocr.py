from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator
import re
import numpy as np
from .structures import Word
from .constants import IMAGE_SUFFIXES
from .parser import _clean_text

import cv2
import pymupdf as fitz
import pypdfium2
import pdfplumber


def _words_pymupdf(page: Any) -> list[Word]:
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

def _words_pdfplumber(page: Any) -> list[Word]:
    return [Word(w["text"], w["x0"], w["top"], w["x1"], w["bottom"]) for w in page.extract_words(use_text_flow=False, extra_attrs=["upright"]) if w.get("upright", True)]

@lru_cache(maxsize=1)
def _rapidocr_engine() -> Any:
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()

def _preprocess(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
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

def _ocr_words(bgr: np.ndarray) -> list[Word]:
    words: list[Word] = []

    engine = _rapidocr_engine()
    result, _ = engine(bgr)
    for box, text, _ in result or []:
        if not (text := _clean_text(str(text).replace("|", " "))):
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
    # import pytesseract 
    # Buat fallback kalo rapidocr gagal
    # data = pytesseract.image_to_data(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), config="--psm 6", output_type=pytesseract.Output.DICT)
    # for i, raw in enumerate(data["text"]):
    #     if (text := _clean_text(raw.replace("|", " "))) and float(data["conf"][i]) >= 0:
    #         x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
    #         words.append(Word(text, x, y, x + w, y + h))
    # return words

# Extract logo bank untuk extract nama bank
def _largest_logo_text(words: list[Word]) -> str:
    alpha = [w for w in words if len(re.sub(r"[^A-Za-z]", "", w.text)) >= 2]
    if not alpha:
        return ""
    tallest = max(alpha, key=lambda w: w.h)
    same_line = [w for w in alpha if abs(w.yc - tallest.yc) < 0.6 * tallest.h and w.h >= 0.7 * tallest.h]
    same_line.sort(key=lambda w: w.x0)
    return _clean_text(" ".join(re.sub(r"[^A-Za-z0-9]", "", w.text) for w in same_line)).upper()

def extract_logo_text(path: Path, dpi: int = 180) -> str:
    
    if path.suffix.lower() in IMAGE_SUFFIXES:
        img = cv2.imread(str(path))
        return _largest_logo_text(_ocr_words(_preprocess(img[: max(1, int(img.shape[0]*0.2)), :]))) if img is not None else ""
    
    if fitz is not None:
        with fitz.open(str(path)) as doc:
            if len(doc) == 0:
                return ""
            page = doc[0]
            rect = page.rect
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                rects = page.get_image_rects(xref)
                if rects and rects[0].y0 < rect.y0 + rect.height * 0.25 and rects[0].wdith < rect.wdith * 0.8:
                    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=rects[0], alpha=False)
                    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR if pix.n >= 3 else cv2.COLOR_GRAY2BGR)
                    if txt := _largest_logo_text(_ocr_words(_preprocess(bgr))):
                        return txt
        
            clip = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * 0.3)
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=clip, alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR if pix.n >= 3 else cv2.COLOR_GRAY2BGR)
            return _largest_logo_text(_ocr_words(_preprocess(bgr)))
        
    renderer = pypdfium2.PdfDocument(str(path))
    try:
        if len(renderer) == 0:
            return ""
        pil = renderer[0].render(scale=dpi / 72).to_pil().convert("RGB")
        arr = np.array(pil)
        bgr = cv2.cvtColor(arr[: max(1, int(arr.shape[0] * 0.3)), :], cv2.COLOR_RGB2BGR)
        return _largest_logo_text(_ocr_words(_preprocess(bgr)))
    finally:
        renderer.close()
    

def iter_pages(path: Path, dpi: int) -> Iterator[tuple[int, list[Word], float, str]]:
    if path.suffix.lower() in IMAGE_SUFFIXES:
        if cv2 is None:
            raise RuntimeError("opencv-python is required for image input")
        if (img := cv2.imread(str(path))) is None:
            raise ValueError(f"Could not read the image file: {path.name}")
        yield 1, _ocr_words(_preprocess(img)), float(img.shape[1]), "ocr"
        return
    
    if fitz is not None:
        with fitz.open(str(path)) as doc:
            for number, page in enumerate(doc, start=1):
                words = _words_pymupdf(page)
                if len(words) >= 8:
                    yield number, words, float(page.rect.width), "text"
                elif not page.get_images(full=True) and len(page.get_drawings()) < 50:
                    yield number, words, float(page.rect.width), ("text" if words else "blank")
                else:
                    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
                    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR if pix.n >= 3 else cv2.COLOR_GRAY2BGR)
                    yield number, _ocr_words(_preprocess(bgr)), float(pix.width), "ocr"
        return

    with pdfplumber.open(str(path)) as plumber:
        renderer = pypdfium2.PdfDocument(str(path))
        try:
            for number, page in enumerate(plumber.pages, start=1):
                words = _words_pdfplumber(page)
                if len(words) >= 8:
                    yield number, words, float(page.width), "text"
                elif not words and not page.images:
                    yield number, words, float(page.width), "blank"
                else:
                    pil = renderer[number - 1].render(scale=dpi / 72).to_pil().convert("RGB")
                    bgr = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
                    yield number, _ocr_words(_preprocess(bgr)), float(bgr.shape[1]), "ocr"
        finally:
            renderer.close()