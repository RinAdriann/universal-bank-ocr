from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

import pymupdf as fitz

from .consensus import detect_overlaid_spans
from .constants import (
    EXTRACTION_CONFIDENCE_OVERLAID,
    IMAGE_SUFFIXES,
    SUSPICIOUS_PDF_EDITORS,
)
from .ocr import load_and_sanitize_pdf
from .structures import AuditIssue, ExtractedRow, NumericTokenProfile, Span

fitz.TOOLS.mupdf_display_errors(False)


def _fmt_date(dt: Any) -> str:
    if dt is None:
        return ""
    if isinstance(dt, (date, datetime)):
        return dt.isoformat()
    return str(dt)


def check_document_metadata(path: Path | str) -> dict[str, Any]:
    file_path = Path(path)
    if file_path.suffix.lower() in IMAGE_SUFFIXES or not file_path.exists():
        return {
            "file": file_path.name,
            "metadata": {},
            "suspicious_editor": None,
            "dates_diverged": False,
            "flags": [],
            "findings": [],
        }

    doc = load_and_sanitize_pdf(file_path)
    if doc is None:
        return {
            "file": file_path.name,
            "metadata": {},
            "suspicious_editor": None,
            "dates_diverged": False,
            "flags": [],
            "findings": [],
        }
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


def dominant_profile_for_tokens(profiles: Sequence[NumericTokenProfile]) -> dict[str, Any]:
    family_counts = Counter(p.font_family for p in profiles if p.font_family)
    subset_counts = Counter(p.subset_prefix for p in profiles if p.subset_prefix)
    size_counts = Counter(round(p.size, 1) for p in profiles if p.size > 0)

    dom_family, fam_n = family_counts.most_common(1)[0] if family_counts else ("", 0)
    dom_subset, sub_n = subset_counts.most_common(1)[0] if subset_counts else ("", 0)
    dom_size, _ = size_counts.most_common(1)[0] if size_counts else (0.0, 0)

    enforce_subset = bool(dom_subset) and (sub_n / max(len(profiles), 1) >= 0.75)
    enforce_family = bool(dom_family) and (fam_n / max(len(profiles), 1) >= 0.65)
    return {
        "font_family": dom_family if enforce_family else "",
        "subset_prefix": dom_subset if enforce_subset else "",
        "size": dom_size,
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

        family_mismatch = bool(exp_family and prof.font_family != exp_family)
        subset_mismatch = bool(exp_subset and prof.subset_prefix != exp_subset)
        size_mismatch = bool(exp_size > 0 and abs(round(prof.size, 1) - exp_size) > 0.25)

        if family_mismatch or subset_mismatch or size_mismatch:
            if "FONT_ANOMALY" not in row.validation:
                row.validation.append("FONT_ANOMALY")
            reasons: list[str] = []
            if family_mismatch:
                reasons.append(f"font_family='{prof.font_family}' vs expected='{exp_family}'")
            if subset_mismatch:
                reasons.append(f"subset_prefix='{prof.subset_prefix}' vs expected='{exp_subset}'")
            if size_mismatch:
                reasons.append(f"size={prof.size}pt vs expected={exp_size}pt")
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
                "expected_font_family": exp_family,
                "expected_subset_prefix": exp_subset,
                "expected_size": exp_size,
                "detail": f"Transaction {tx_idx} ({prof.assigned_column}='{prof.text}'): " + ", ".join(reasons),
            })

    return anomalies


def detect_overlaid_streams(
    page_no: int,
    spans: Sequence[Span],
    rows: Sequence[ExtractedRow] | None = None,
) -> list[dict[str, Any]]:
    pairs = detect_overlaid_spans(spans)
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
        if rows:
            for r in rows:
                if r.pdf_page_index != page_no:
                    continue
                for prof in r.numeric_profiles:
                    if max(ox0, prof.x0) < min(ox1, prof.x1) and max(oy0, prof.y0) < min(oy1, prof.y1):
                        matched_row = r
                        if EXTRACTION_CONFIDENCE_OVERLAID not in r.validation:
                            r.validation.append(EXTRACTION_CONFIDENCE_OVERLAID)
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
        flags.append("FONT_ANOMALY")
    if (
        overlaid_findings or any(EXTRACTION_CONFIDENCE_OVERLAID in r.validation for r in rows)
    ) and EXTRACTION_CONFIDENCE_OVERLAID not in flags:
        flags.append(EXTRACTION_CONFIDENCE_OVERLAID)

    all_findings: list[dict[str, Any]] = []
    for mc in metadata_checks:
        all_findings.extend(mc["findings"])
    all_findings.extend(font_anomalies)
    all_findings.extend(overlaid_findings)

    forensic_issues: list[AuditIssue] = []
    for mc in metadata_checks:
        for f in mc["findings"]:
            forensic_issues.append(
                AuditIssue(
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
            AuditIssue(
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
            AuditIssue(
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


__all__ = [
    "check_document_metadata",
    "detect_font_anomalies",
    "detect_overlaid_streams",
    "run_forensic_audit",
]
