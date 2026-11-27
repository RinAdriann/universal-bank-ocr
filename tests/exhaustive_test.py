import argparse
import os
import random
import re
import sys
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pymupdf as fitz

# Ensure project root is in sys.path 
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from component import excel_bytes, extract_statement
from component.report import serialize_row

# Default paths
DEFAULT_DATA_ROOT = PROJECT_ROOT / "tests" / "Data" / "TEST"
DEFAULT_RESULT_DIR = PROJECT_ROOT / "tests" / "Data" / "Test_Result"

# Folders to skip until sorted (or exclude non-template folders)
UNSORTED_OR_EXCLUDED = {"_baselines"}
SUPPORTED_EXTS = {".pdf"}

# Opening balance rows stay in extraction with balance-only; exempt them from missing-amount checks
OPENING_RE = re.compile(
    r"(?:^|[\s\W\d])(SALDO\s*AWAL|SAK\s*AWAL|OPENING\s*BALANCE|BEGINNING\s*BALANCE|SALDO\s*SEBELUMNYA|BALANCE\s*B/?F(?:WD)?|BROUGHT\s*FORWARD)(?:PERIODE|BULAN|IDR|USD)?\b",
    re.IGNORECASE,
)

# All security / forensic / tampering issue codes that should still count as PASS 
SECURITY_ISSUE_PREFIXES = (
    "METADATA_MODIFIED",
    "SUSPICIOUS_PDF_PRODUCER",
    "FONT_ANOMALY",
    "OVERLAID_TEXT",
    "SIGNATURE_INVALID",
    "UNTRUSTED_PRODUCER",
    "PDF_TAMPER",
    "TAMPER",
    "SECURITY",
    "FORENSIC",
    "INCREMENTAL_UPDATE",
    "XREF_ANOMALY",
    "HIDDEN_TEXT",
    "WHITE_TEXT",
    "CORRUPT_STREAM_REPAIRED",  # informational: stream repaired, reconciliation still verified
)

# Regex to parse "Issues (24): METADATA_MODIFIED: 1 | BALANCE_MISMATCH: 10" from summary_line
ISSUES_BLOCK_RE = re.compile(r"Issues\s*\(\d+\)\s*:\s*(.+)$", re.IGNORECASE)


@dataclass
class DocumentAudit:
    folder_name: str
    file_path: Path
    page_count: int
    status: str  # PASS | RECON_FAIL | EMPTY | ERROR
    elapsed_sec: float = 0.0
    detected_bank: str = "-"
    account_count: int = 0
    row_count: int = 0
    document_year: Any = None
    excel_path: Path | None = None
    summary_lines: list[str] = field(default_factory=list)
    recon_issues: list[str] = field(default_factory=list)
    security_flags: list[str] = field(default_factory=list)


def get_page_count(path: Path) -> int:
    if path.suffix.lower() != ".pdf":
        return 1
    try:
        with fitz.open(path) as doc:
            return len(doc)
    except Exception:
        return -1  # Encrypted / password-protected or corrupt


def sample_folder_files(
    folder: Path,
    cap: int = 10,
    seed: int = 42,
) -> tuple[list[tuple[Path, int]], list[Path]]:
    """
    Collects supported files recursively inside `folder`.
    - If valid files <= cap: returns all of them.
    - If valid files > cap: forces shortest & longest (by page count) + (cap - 2) deterministic random picks.
    """
    rng = random.Random(f"{seed}_{folder.name}")
    valid: list[tuple[Path, int]] = []
    locked: list[Path] = []

    for f in sorted(folder.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in SUPPORTED_EXTS:
            continue
        pages = get_page_count(f)
        if pages > 0:
            valid.append((f, pages))
        else:
            locked.append(f)

    if len(valid) <= cap:
        return valid, locked

    valid.sort(key=lambda x: x[1])
    shortest = valid[0]
    longest = valid[-1]
    middle = valid[1:-1]
    sampled = [shortest, longest] + rng.sample(middle, cap - 2)
    sampled.sort(key=lambda x: x[0].name)
    return sampled, locked


def extract_summary_lines_list(result: dict[str, Any]) -> list[str]:
    """Mirrors extract_summary_lines from test_golden_master.py."""
    lines: list[str] = []
    primary = result.get("audit_result")
    overall = (
        primary.summary_line()
        if primary is not None and hasattr(primary, "summary_line")
        else (result.get("summary") or {}).get("summary_line")
    )
    if overall:
        lines.append(f"OVERALL: {overall}")

    accounts = result.get("accounts") or {}
    for acc_key, acc_val in accounts.items():
        ar = acc_val.get("audit_result")
        acc_line = (
            ar.summary_line()
            if ar is not None and hasattr(ar, "summary_line")
            else (acc_val.get("summary") or {}).get("summary_line")
        )
        if acc_line:
            lines.append(f"ACC[{acc_key}]: {acc_line}")
    return lines


def is_security_code(code: str) -> bool:
    upper = code.strip().upper()
    return any(upper.startswith(prefix) or prefix in upper for prefix in SECURITY_ISSUE_PREFIXES)


def classify_summary_issues(summary_lines: list[str]) -> tuple[list[str], list[str]]:
    recon_tokens: list[str] = []
    sec_tokens: list[str] = []
    seen: set[str] = set()

    for line in summary_lines:
        m = ISSUES_BLOCK_RE.search(line.strip())
        if not m:
            continue
        raw_block = m.group(1).strip()
        if raw_block.lower() == "none":
            continue

        for part in raw_block.split("|"):
            token = part.strip()
            if not token or token in seen:
                continue
            seen.add(token)

            code = token.split(":")[0].strip()
            if is_security_code(code):
                sec_tokens.append(token)
            else:
                recon_tokens.append(token)

    return recon_tokens, sec_tokens


def inspect_rows_for_issues(rows: list[dict[str, Any]], summary: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not rows:
        issues.append("Zero transaction rows extracted.")
        return issues

    missing_date = 0
    missing_amount = 0
    for r in rows:
        desc = str(r.get("description") or r.get("keterangan") or r.get("uraian") or "")
        has_amt = any(
            r.get(k) not in (None, "", 0, 0.0)
            for k in ("amount", "debit", "credit", "kredit", "mutasi")
        )
        has_bal = r.get("balance") not in (None, "") or r.get("saldo") not in (None, "")

        # Ignore opening balance rows that intentionally carry only a balance
        if not has_amt and has_bal and OPENING_RE.search(desc):
            continue

        if not r.get("date") and not r.get("tanggal"):
            missing_date += 1
        if not has_amt:
            missing_amount += 1

    if missing_date:
        issues.append(f"{missing_date}/{len(rows)} rows missing date.")
    if missing_amount:
        issues.append(f"{missing_amount}/{len(rows)} rows missing debit/credit amount.")

    if isinstance(summary, dict):
        for flag_key in ("reconciled", "is_balanced", "balanced", "valid"):
            if flag_key in summary and summary[flag_key] is False:
                issues.append(f"Summary flag '{flag_key}' is False.")
        diff = summary.get("difference") or summary.get("selisih") or summary.get("discrepancy")
        if isinstance(diff, (int, float)) and abs(diff) > 1.0:
            issues.append(f"Balance discrepancy detected: {diff:,.2f}")

    return issues


def save_extracted_excel(
    result: dict[str, Any],
    excel_root: Path,
    folder_name: str,
    source_file: Path,
) -> Path | None:
    try:
        xlsx_data = excel_bytes(result)
        if not xlsx_data:
            return None
        target_dir = excel_root / folder_name
        target_dir.mkdir(parents=True, exist_ok=True)
        out_path = target_dir / f"{source_file.stem}.xlsx"
        out_path.write_bytes(xlsx_data)
        return out_path
    except Exception:
        return None


def evaluate_document(
    folder_name: str,
    file_path: Path,
    page_count: int,
    excel_root: Path,
) -> DocumentAudit:
    start = time.perf_counter()
    try:
        result = extract_statement([str(file_path)])
        elapsed = time.perf_counter() - start

        saved_excel = save_extracted_excel(result, excel_root, folder_name, file_path)

        accounts = result.get("accounts") or {}
        raw_rows = result.get("rows") or []
        serialized_rows = [serialize_row(r) for r in raw_rows]
        summary = result.get("summary") or {}
        summary_lines = extract_summary_lines_list(result)

        detected_banks = sorted(
            {str(v.get("bank")) for v in accounts.values() if v.get("bank")}
        )
        detected_bank_str = ", ".join(detected_banks) if detected_banks else str(result.get("bank") or "UNKNOWN")

        row_issues = inspect_rows_for_issues(serialized_rows, summary)
        summary_recon_issues, security_flags = classify_summary_issues(summary_lines)

        recon_issues = list(row_issues)
        if summary_recon_issues:
            recon_issues.append(f"Reconciliation issues: {' | '.join(summary_recon_issues)}")

        if len(serialized_rows) == 0:
            status = "EMPTY"
        elif recon_issues:
            status = "RECON_FAIL"
        else:
            status = "PASS"

        return DocumentAudit(
            folder_name=folder_name,
            file_path=file_path,
            page_count=page_count,
            status=status,
            elapsed_sec=elapsed,
            detected_bank=detected_bank_str,
            account_count=len(accounts),
            row_count=len(serialized_rows),
            document_year=result.get("document_year"),
            excel_path=saved_excel,
            summary_lines=summary_lines,
            recon_issues=recon_issues,
            security_flags=security_flags,
        )

    except Exception as exc:
        elapsed = time.perf_counter() - start
        tb_last = traceback.format_exc().strip().splitlines()[-1]
        return DocumentAudit(
            folder_name=folder_name,
            file_path=file_path,
            page_count=page_count,
            status="ERROR",
            elapsed_sec=elapsed,
            recon_issues=[f"Exception: {type(exc).__name__}: {exc}", f"Trace: {tb_last}"],
        )


def render_txt_report(
    audits: list[DocumentAudit],
    locked_by_folder: dict[str, list[Path]],
    mode: str,
    data_root: Path,
    excel_root: Path,
    cap: int,
    seed: int,
) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_docs = len(audits)
    pass_docs = sum(1 for a in audits if a.status == "PASS")
    pass_with_sec = sum(1 for a in audits if a.status == "PASS" and a.security_flags)
    recon_fail_docs = sum(1 for a in audits if a.status == "RECON_FAIL")
    empty_docs = sum(1 for a in audits if a.status == "EMPTY")
    error_docs = sum(1 for a in audits if a.status == "ERROR")
    total_rows = sum(a.row_count for a in audits)
    total_time = sum(a.elapsed_sec for a in audits)

    lines: list[str] = []
    lines.append("=" * 96)
    lines.append(f"BANK-OCR EXHAUSTIVE TEMPLATE TEST REPORT ({mode.upper()} MODE)")
    lines.append("=" * 96)
    lines.append(f"Generated At   : {now_str}")
    lines.append(f"Source Root    : {data_root}")
    lines.append(f"Excel Exports  : {excel_root}")
    lines.append(f"Sampling Rule  : <= {cap} take all | > {cap} stratified sample (min, max, {cap - 2} random, seed={seed})")
    lines.append("Scoring Policy : Security/tamper-only flags (METADATA_MODIFIED, etc.) count as PASS")
    lines.append("-" * 96)
    lines.append(
        f"OVERALL SUMMARY: {total_docs} docs tested | "
        f"PASS: {pass_docs} ({(pass_docs / total_docs * 100) if total_docs else 0:.1f}%, incl. {pass_with_sec} w/ security flags) | "
        f"RECON_FAIL: {recon_fail_docs} | EMPTY: {empty_docs} | ERROR: {error_docs}"
    )
    lines.append(f"TOTAL ROWS     : {total_rows:,} rows extracted in {total_time:.1f}s")
    lines.append("=" * 96)
    lines.append("")

    # Group by folder
    by_folder: dict[str, list[DocumentAudit]] = defaultdict(list)
    for a in audits:
        by_folder[a.folder_name].append(a)

    # 1. Folder Rollup Table
    lines.append("1. FOLDER / TEMPLATE SUMMARY")
    lines.append("-" * 96)
    lines.append(
        f"{'FOLDER':<18} | {'TESTED':>6} | {'PASS':>4} | {'FAIL':>4} | {'EMPTY':>5} | {'ERR':>3} | {'LOCKED':>6} | {'ROWS':>6} | {'PASS %':>6}"
    )
    lines.append("-" * 96)

    for folder_name in sorted(by_folder.keys()):
        items = by_folder[folder_name]
        n = len(items)
        p = sum(1 for x in items if x.status == "PASS")
        rf = sum(1 for x in items if x.status == "RECON_FAIL")
        em = sum(1 for x in items if x.status == "EMPTY")
        er = sum(1 for x in items if x.status == "ERROR")
        lk = len(locked_by_folder.get(folder_name, []))
        rw = sum(x.row_count for x in items)
        pct = (p / n * 100) if n else 0.0
        lines.append(
            f"{folder_name:<18} | {n:>6} | {p:>4} | {rf:>4} | {em:>5} | {er:>3} | {lk:>6} | {rw:>6} | {pct:>5.1f}%"
        )

    lines.append("=" * 96)
    lines.append("")

    # 2. Detailed Document-by-Document Reconciliation & Issues
    lines.append("2. DOCUMENT-BY-DOCUMENT RECONCILIATION & ISSUES")
    lines.append("=" * 96)

    for folder_name in sorted(by_folder.keys()):
        lines.append("")
        lines.append(f"### FOLDER: {folder_name} ###")
        lines.append("-" * 96)

        for idx, doc in enumerate(by_folder[folder_name], 1):
            sec_tag = " [SEC_FLAGGED]" if doc.security_flags else ""
            lines.append(
                f"[{idx:02d}] [{doc.status}]{sec_tag} {doc.file_path.name} "
                f"(pages={doc.page_count}, rows={doc.row_count}, bank={doc.detected_bank}, "
                f"year={doc.document_year}, time={doc.elapsed_sec:.2f}s)"
            )
            lines.append(f"     Path          : {doc.file_path}")
            lines.append(f"     Excel Output  : {doc.excel_path if doc.excel_path else '<not generated>'}")
            if doc.summary_lines:
                for sl in doc.summary_lines:
                    lines.append(f"     Reconciliation: {sl}")
            else:
                lines.append("     Reconciliation: <no summary_line returned>")

            if doc.security_flags:
                lines.append(f"     [SEC] Info    : {' | '.join(doc.security_flags)}")

            if doc.recon_issues:
                for iss in doc.recon_issues:
                    lines.append(f"     [!] Issue     : {iss}")
            else:
                lines.append("     [OK] Issues   : None")

        locked_files = locked_by_folder.get(folder_name, [])
        if locked_files:
            lines.append(f"     [SKIPPED - LOCKED/CORRUPT PDFs ({len(locked_files)})]:")
            for lf in locked_files:
                lines.append(f"       - {lf}")

    lines.append("")
    lines.append("=" * 96)
    lines.append("END OF REPORT")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Exhaustive per-folder template test & TXT report exporter.")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT, help="Root folder containing bank folders.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_RESULT_DIR, help="Directory to export TXT summary report.")
    parser.add_argument("--cap", type=int, default=10, help="Max files per folder (take all if <= cap).")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible sampling.")
    parser.add_argument("--mode", type=str, default="deterministic", choices=["deterministic", "ner"], help="OCR pipeline mode.")
    parser.add_argument("--folder", type=str, default=None, help="Optional: test a single bank folder name only.")
    parser.add_argument("--include-unsorted", action="store_true", help="Also include BCA, BRI, BNI, BSI.")
    args = parser.parse_args()

    os.environ["DISABLE_NER"] = "1" if args.mode == "deterministic" else "0"

    excluded = set(UNSORTED_OR_EXCLUDED)
    if args.include_unsorted:
        excluded -= {"BCA", "BRI", "BNI", "BSI"}

    excel_root = args.out_dir / "excel_result"
    excel_root.mkdir(parents=True, exist_ok=True)

    all_audits: list[DocumentAudit] = []
    locked_by_folder: dict[str, list[Path]] = {}

    bank_folders = sorted(
        d for d in args.data_root.iterdir()
        if d.is_dir() and d.name not in excluded and (args.folder is None or d.name.lower() == args.folder.lower())
    )

    if not bank_folders:
        print(f"No matching folders found in {args.data_root}")
        sys.exit(1)

    print(f"Running exhaustive test in [{args.mode}] mode across {len(bank_folders)} folders...")

    for bank_dir in bank_folders:
        sampled, locked = sample_folder_files(bank_dir, cap=args.cap, seed=args.seed)
        locked_by_folder[bank_dir.name] = locked
        print(f"\n[{bank_dir.name}] Testing {len(sampled)} files (skipped {len(locked)} locked/corrupt)...")

        for file_path, pages in sampled:
            audit = evaluate_document(bank_dir.name, file_path, pages, excel_root)
            all_audits.append(audit)
            sec_note = " (SEC_FLAGGED)" if audit.security_flags else ""
            print(f"  -> {audit.status:<10}{sec_note} | rows={audit.row_count:<4} | {file_path.name}")

    report_text = render_txt_report(
        audits=all_audits,
        locked_by_folder=locked_by_folder,
        mode=args.mode,
        data_root=args.data_root,
        excel_root=excel_root,
        cap=args.cap,
        seed=args.seed,
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    timestamped_report = args.out_dir / f"exhaustive_report_{args.mode}_{timestamp}.txt"
    latest_report = args.out_dir / f"exhaustive_report_{args.mode}_latest.txt"

    timestamped_report.write_text(report_text, encoding="utf-8")
    latest_report.write_text(report_text, encoding="utf-8")

    print(f"\nReport exported to:\n  {timestamped_report}\n  {latest_report}")
    print(f"Excel files saved under:\n  {excel_root}")


if __name__ == "__main__":
    main()