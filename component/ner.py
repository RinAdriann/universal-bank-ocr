import os
from pathlib import Path
import re
import threading
from typing import Any, Sequence
import warnings

import torch
from transformers import AutoModelForTokenClassification, AutoTokenizer, pipeline

DEFAULT_MODEL_DIR = Path(__file__).resolve().parent.parent / "model" / "NER"
DEFAULT_CONFIDENCE_THRESHOLD = float(os.getenv("BANK_OCR_NER_THRESHOLD", "0.60"))
DEFAULT_CPU_THREADS = int(os.getenv("BANK_OCR_NER_THREADS", "4"))

COMPANY_MARKER_RE = re.compile(
    r"\b(?:PT|CV|UD|TBK|PERSERO|YAYASAN|KOPERASI|FIRMA|PERUM|BANK|SHOPEEPAY|SPAYLATER|"
    r"GOPAY|OVO|DANA|TOKOPEDIA|AIRPAY|LINKAJA|INDOMARET|ALFAMART)\b",
    re.IGNORECASE,
)

REF_CODE_RE = re.compile(
    r"\b(?:\d{4}/[A-Z0-9]{3,8}/[A-Z0-9]{4,10}|[A-Z]{2,5}\d{6,16}|\d{10,20})\b",
    re.IGNORECASE,
)

NOISE_PREFIX_RE = re.compile(
    r"^(?:SWITCHING\s+(?:CR|DB)(?:\s+(?:DR|KE)\s+\d{3})?|"
    r"BI-?FAST\s+(?:CR|DB)(?:\s+BIF\s+(?:TRANSFER|BIAYA\s+TXN)\s+(?:DR|KE)\s+\d{3})?|"
    r"TRSF\s+E-?BANKING\s+(?:CR|DB)(?:\s+\d{4}/[A-Z0-9]+/[A-Z0-9]+)?(?:\s+TANGGAL\s*:\s*\d{2}/\d{2})?(?:\s+\d+(?:\.\d{2})?)?(?:\s+PB\s+REK)?|"
    r"MCM\s+INHOUSE\s+TRF|KR\s+OTOMATIS|OVERBOOKING)\b",
    re.IGNORECASE,
)

STOPWORD_TOKENS = {
    "TRSF", "TRF", "TRANSFER", "E-BANKING", "EBANKING", "BI-FAST", "BIFAST", "BIF",
    "SWITCHING", "DEBIT", "KREDIT", "CREDIT", "DB", "CR", "DR", "KE", "DARI", "TO",
    "FROM", "SALDO", "AWAL", "AKHIR", "BIAYA", "ADMIN", "ADM", "TXN", "BUNGA", "PAJAK",
    "TARIK", "TUNAI", "SETOR", "PEMBAYARAN", "PEMBELIAN", "VIA", "M-BCA", "MBCA",
    "WEB", "BRILINK", "PB", "REK", "TANGGAL", "TGL", "BULAN", "NOTA", "AMBIL", "BAYAR",
    "OTOMATIS", "PAYMENT", "QRIS", "EDC", "ATM", "FTSCY", "FTFVA",
}


def _expand_span(text: str, start: int, end: int) -> str:
    n = len(text)
    s = max(0, min(start, n))
    e = max(s, min(end, n))
    while s > 0 and not text[s - 1].isspace() and text[s - 1] not in "/:;,-()[]":
        s -= 1
    while e < n and not text[e].isspace() and text[e] not in "/:;,-()[]":
        e += 1
    return text[s:e].strip(" -.,:/;()")


def _clean_entity_tokens(text: str) -> str:
    tokens = []
    for raw_tok in re.split(r"[\s/|_]+", text.upper()):
        tok = raw_tok.strip(" -.,:;()[]@#")
        if not tok or tok in STOPWORD_TOKENS:
            continue
        if re.fullmatch(r"[\d.,]+", tok) or re.fullmatch(r"WS\d+", tok) or len(tok) <= 1:
            continue
        if re.search(r"\d{3,}", tok):
            continue
        tokens.append(tok)
    return " ".join(tokens)


def _prepare_ner_prompt(description: str) -> str:
    text = re.sub(r"\s+", " ", description or "").strip()
    if not text:
        return ""
    is_debit = bool(re.search(r"\b(?:DB|DEBIT|TRANSFER\s+KE|TRSF\s+KE|TRF\s+KE|PB\s+REK)\b", text, re.I))
    cleaned = NOISE_PREFIX_RE.sub(" ", text)
    cleaned = re.sub(r"\b\d{4}/[A-Z0-9]+/[A-Z0-9]+\b", " ", cleaned)
    cleaned = re.sub(r"\b\d+(?:\.\d{2})\b", " ", cleaned)
    cleaned = re.sub(r"\b\d{6,}[A-Z0-9_]*\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -.,:/")
    if not cleaned:
        return text
    prep = "Transfer ke" if is_debit else "Transfer dari"
    if not re.match(r"^(?:transfer|trsf|trf|pembayaran|dari|ke)\b", cleaned, re.I):
        return f"{prep} {cleaned}"
    return cleaned


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
        quantize: bool = True,
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
        self.model_dir = model_dir
        self.confidence_threshold = confidence_threshold
        self._pipe = None
        self._infer_lock = threading.Lock()

        try:
            if num_threads > 0:
                torch.set_num_threads(num_threads)
            tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)
            model = AutoModelForTokenClassification.from_pretrained(str(model_dir), local_files_only=True)
            model.eval()
            if quantize:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    model = torch.quantization.quantize_dynamic(
                        model, {torch.nn.Linear}, dtype=torch.qint8
                    )
            self._pipe = pipeline(
                "token-classification",
                model=model,
                tokenizer=tokenizer,
                aggregation_strategy="simple",
                device=-1,
            )
        except Exception:
            self._pipe = None

    def _resolve_single(self, raw_desc: str, prompt_text: str, raw_entities: list[dict[str, Any]]) -> dict[str, Any]:
        result = _empty_result()
        if not raw_desc:
            return result

        ref_match = REF_CODE_RE.search(raw_desc)
        fallback_ref = ref_match.group(0).upper() if ref_match else ""

        per_candidates: list[tuple[float, str, int, int]] = []
        org_candidates: list[tuple[float, str, int, int]] = []
        crd_parts: list[str] = []
        dt_parts: list[str] = []
        mon_parts: list[str] = []
        serialized_entities: list[dict[str, Any]] = []

        for ent in raw_entities or []:
            group = str(ent.get("entity_group") or ent.get("entity") or "").upper()
            score = float(ent.get("score", 0.0))
            start = int(ent.get("start", 0))
            end = int(ent.get("end", 0))
            span_text = _expand_span(prompt_text, start, end) if prompt_text else str(ent.get("word", ""))
            serialized_entities.append({
                "entity_group": group,
                "score": round(score, 4),
                "text": span_text.upper(),
            })

            if group in ("PER", "ORG") and score >= self.confidence_threshold:
                cleaned = _clean_entity_tokens(span_text)
                if len(cleaned) >= 3:
                    if group == "PER":
                        per_candidates.append((score, cleaned, start, end))
                    else:
                        org_candidates.append((score, cleaned, start, end))
            elif group in ("CRD", "ORD") and score >= 0.50:
                tok = span_text.upper().strip()
                if len(re.sub(r"\D", "", tok)) >= 4 and tok not in crd_parts:
                    crd_parts.append(tok)
            elif group in ("DAT", "TIM") and score >= 0.50:
                tok = span_text.upper().strip()
                if tok and tok not in dt_parts:
                    dt_parts.append(tok)
            elif group == "MON" and score >= 0.50:
                tok = span_text.upper().strip()
                if tok and tok not in mon_parts:
                    mon_parts.append(tok)

        def _merge_candidates(cands: list[tuple[float, str, int, int]]) -> tuple[float, str]:
            if not cands:
                return 0.0, ""
            merged_words: list[str] = []
            scores: list[float] = []
            for sc, text_val, _, _ in cands:
                scores.append(sc)
                for w in text_val.split():
                    if w not in merged_words:
                        merged_words.append(w)
            return max(scores), " ".join(merged_words)

        per_score, per_name = _merge_candidates(per_candidates)
        org_score, org_name = _merge_candidates(org_candidates)

        counterparty = ""
        best_conf = 0.0
        if org_name and per_name:
            if COMPANY_MARKER_RE.search(org_name) or COMPANY_MARKER_RE.search(raw_desc):
                counterparty, best_conf = org_name, org_score
            elif per_score >= org_score:
                counterparty, best_conf = per_name, per_score
            else:
                counterparty, best_conf = org_name, org_score
        elif org_name:
            counterparty, best_conf = org_name, org_score
        elif per_name:
            counterparty, best_conf = per_name, per_score

        result["counterparty"] = counterparty
        result["reference"] = fallback_ref or (" ".join(crd_parts) if crd_parts else "")
        result["datetime"] = " ".join(dt_parts)
        result["monetary"] = " ".join(mon_parts)
        result["confidence"] = round(best_conf, 4)
        result["entities"] = serialized_entities
        return result

    def extract(self, description: str) -> dict[str, Any]:
        return self.extract_batch([description], batch_size=1)[0]

    def extract_batch(self, descriptions: Sequence[str], batch_size: int = 16) -> list[dict[str, Any]]:
        if not descriptions:
            return []
        if self._pipe is None:
            return [_empty_result() for _ in descriptions]

        prompts: list[str] = []
        unique_prompts: list[str] = []
        prompt_to_idx: dict[str, int] = {}

        for desc in descriptions:
            raw = str(desc or "").strip()
            if not raw or re.fullmatch(r"SALDO\s+AWAL|OPENING\s+BALANCE", raw, re.I):
                prompts.append("")
                continue
            prompt = _prepare_ner_prompt(raw)
            prompts.append(prompt)
            if prompt and prompt not in prompt_to_idx:
                prompt_to_idx[prompt] = len(unique_prompts)
                unique_prompts.append(prompt)

        if not unique_prompts:
            return [_empty_result() for _ in descriptions]

        try:
            with self._infer_lock, torch.inference_mode():
                raw_outputs = self._pipe(unique_prompts, batch_size=max(1, batch_size))
            if len(unique_prompts) == 1 and raw_outputs and isinstance(raw_outputs[0], dict):
                raw_outputs = [raw_outputs]
        except Exception:
            return [_empty_result() for _ in descriptions]

        results: list[dict[str, Any]] = []
        for raw_desc, prompt in zip(descriptions, prompts):
            if not prompt or prompt not in prompt_to_idx:
                results.append(_empty_result())
                continue
            u_idx = prompt_to_idx[prompt]
            ent_list = raw_outputs[u_idx] if u_idx < len(raw_outputs) else []
            results.append(self._resolve_single(str(raw_desc or ""), prompt, ent_list))
        return results


_NER_SINGLETON: TransactionNER | None = None


def get_transaction_ner() -> TransactionNER:
    global _NER_SINGLETON
    if _NER_SINGLETON is None:
        _NER_SINGLETON = TransactionNER()
    return _NER_SINGLETON


__all__ = ["TransactionNER", "get_transaction_ner"]
