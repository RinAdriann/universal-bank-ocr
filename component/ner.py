from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
import re
import threading
from typing import Any, Sequence

logger = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = Path(__file__).resolve().parent.parent / "model" / "gliner_int8"
DEFAULT_CONFIDENCE_THRESHOLD = float(os.getenv("BANK_OCR_NER_THRESHOLD", "0.50"))
DEFAULT_CPU_THREADS = int(os.getenv("BANK_OCR_NER_THREADS", "4"))

REF_CODE_RE = re.compile(
    r"\b(?:"
    r"\d{4}/[A-Z0-9]{3,8}/[A-Z0-9]{4,10}|"
    r"[A-Z]{2,8}(?:-[A-Z0-9]{2,8})+-\d{4,12}|"
    r"[A-Z]{2,5}\d{6,16}|"
    r"\d{10,20}"
    r")\b",
    re.IGNORECASE,
)


def _empty_result() -> dict[str, Any]:
    return {
        "counterparty": "",
        "reference": "",
        "datetime": "",
        "monetary": "",
        "confidence": 0.0,
        "entities": [],
    }


class TransactionNER:
    _instance: "TransactionNER | None" = None
    _lock = threading.Lock()

    def __new__(
        cls,
        model_dir: str | Path = DEFAULT_MODEL_DIR,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        num_threads: int = DEFAULT_CPU_THREADS,
        quantize: bool = False,
    ) -> "TransactionNER":
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._init_engine(
                    model_dir=Path(model_dir),
                    confidence_threshold=confidence_threshold,
                    num_threads=num_threads,
                    quantize=quantize,
                )
                cls._instance = instance
            return cls._instance

    def _init_engine(
        self,
        model_dir: Path,
        confidence_threshold: float,
        num_threads: int,
        quantize: bool,
    ) -> None:
        self.model_dir = Path(model_dir).resolve()
        self.confidence_threshold = float(confidence_threshold)
        self.num_threads = max(1, int(num_threads))
        self._extract_batch_fn = None
        self._infer_lock = threading.Lock()

        os.environ.setdefault("OMP_NUM_THREADS", str(self.num_threads))
        os.environ.setdefault("MKL_NUM_THREADS", str(self.num_threads))

        try:
            import onnxruntime as ort

            ort.set_default_logger_severity(3)

            inference_py = self.model_dir / "inference.py"
            if inference_py.is_file():
                spec = importlib.util.spec_from_file_location(
                    "gliner_int8_inference", str(inference_py)
                )
                if spec is not None and spec.loader is not None:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    self._extract_batch_fn = getattr(
                        mod, "extract_batch", None
                    )
            if self._extract_batch_fn is None:
                from model.gliner_int8 import inference as gliner_inference

                self._extract_batch_fn = getattr(
                    gliner_inference, "extract_batch", None
                ) 
        except Exception as exc:
            logger.warning("Failed to initialize TransactionNER engine: %s", exc)
            self._extract_batch_fn = None

    def _resolve_single(
        self, raw_desc: str, matches: list[tuple[str, str, float]]
    ) -> dict[str, Any]:
        result = _empty_result()
        if not raw_desc:
            return result

        ref_match = REF_CODE_RE.search(raw_desc)
        result["reference"] = ref_match.group(0).upper() if ref_match else ""

        if matches:
            raw_name, label, raw_score = matches[0]
            name = str(raw_name).strip().upper()
            score = round(float(raw_score), 3)
            result["counterparty"] = name
            result["confidence"] = score
            result["entities"] = [
                {
                    "entity_group": "ORG" if label == "org" else "PER",
                    "label": label,
                    "score": score,
                    "text": name,
                }
            ]
        return result

    def extract(self, description: str) -> dict[str, Any]:
        return self.extract_batch([description], batch_size=1)[0]

    def extract_batch(
        self, descriptions: Sequence[str], batch_size: int = 16
    ) -> list[dict[str, Any]]:
        if not descriptions:
            return []
        if self._extract_batch_fn is None:
            return [_empty_result() for _ in descriptions]

        texts = [str(desc or "") for desc in descriptions]

        try:
            with self._infer_lock:
                raw_outputs = self._extract_batch_fn(
                    texts,
                    batch_size=batch_size,
                    threshold=self.confidence_threshold,
                )
        except Exception as exc:
            logger.warning("TransactionNER batch inference failed: %s", exc)
            return [_empty_result() for _ in descriptions]

        return [
            self._resolve_single(raw_desc, matches)
            for raw_desc, matches in zip(texts, raw_outputs)
        ]


_NER_SINGLETON: TransactionNER | None = None


def get_transaction_ner() -> TransactionNER:
    global _NER_SINGLETON
    if _NER_SINGLETON is None:
        _NER_SINGLETON = TransactionNER()
    return _NER_SINGLETON


__all__ = ["TransactionNER", "get_transaction_ner"]