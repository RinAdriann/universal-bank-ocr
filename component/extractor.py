import argparse
import re
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from .structures import State, ExtractedRow
from .ocr import iter_pages, first_page_ocr_text
from .constants import SUPPORTED_INPUT_SUFFIXES
from .layout import process_page, apply_dates, printed_totals
from .balancecalc import reconcile, _chain_matches
from .parser import detect_account, detect_bank

def _row_date_key(row: ExtractedRow, year: int | None) -> tuple[int, int, int, int]:
    if row.is_opening:
        return (0, 0, 0, 0)
    if row.date_parts is None:
        return (2, 0, 0, 0)
    day, month, row_year = row.date_parts[:3]
    return (1, row_year or year or 0, month, day)

def _summarize_account(rows: list[ExtractedRow], printed: dict[str, float]) -> dict[str, Any]:
    info = reconcile(rows, None if any(r.is_opening for r in rows) else printed.get("opening"))
    body = [r for r in rows if not r.is_opening]
    opening = next((r.balance for r in rows if r.is_opening), printed.get("opening", info.get("inferred_opening_balance")))
    balances = [r.balance for r in body if r.balance is not None]
    total_debit, total_credit = sum(r.debit or 0.0 for r in body), sum(r.credit or 0.0 for r in body)
    closing = balances[-1] if balances else printed.get("closing")
    summary: dict[str, Any] = {
        **info, "opening_balance": opening, "closing_balance": closing,
        "total_debit": total_debit, "total_credit": total_credit,
        "rows_flagged": sum(bool(r.validation) for r in rows),
        "chain_reconciles": (abs(opening + total_credit - total_debit - closing) < 1.0 if opening is not None and closing is not None else None),
    }
    if printed:
        summary["printed_totals"] = printed
        computed = {"debit": total_debit, "credit": total_credit, "closing": closing, "opening": opening}
        summary["printed_totals_match"] = all(computed[k] is not None and abs(printed[k] - computed[k]) < 1.0 for k in printed if k in computed)
    return summary

def extract_statement(pdf_path: str | Path | Sequence[str | Path], dpi: int = 220) -> dict[str, Any]:
    raw_paths = [pdf_path] if isinstance(pdf_path, (str, Path)) else list(pdf_path)
    if not raw_paths:
        raise ValueError("At least one input file is required.")
    input_paths = [Path(p) for p in raw_paths]
    for p in input_paths:
        if p.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
            raise ValueError(f"Unsupported file format: {p.suffix}. Needs: {', '.join(sorted(SUPPORTED_INPUT_SUFFIXES))}")

    pages: list[dict] = []
    all_years: list[int] = []
    account_rows: dict[str, list[ExtractedRow]] = {}
    account_printed: dict[str, dict[str, float]] = {}
    account_years: dict[str, list[int]] = {}
    account_banks: dict[str, str] = {}

    for path in input_paths:
        state, doc_rows, doc_pages, doc_texts = State(), [], [], []
        p1_ocr_bank = detect_bank(first_page_ocr_text(path, dpi))
        if p1_ocr_bank:
            state.bank = p1_ocr_bank

        for number, words, page_w, source in iter_pages(path, dpi):
            page_rows, diag, page_text = process_page(words, page_w, number, state)
            diag.update({"source": source, "words": len(words), **({"file": path.name} if len(input_paths) > 1 else {})})
            doc_rows.extend(page_rows)
            doc_pages.append(diag)
            doc_texts.append(page_text)

        full_text = "\n".join(doc_texts)
        fallback_acc = state.account or detect_account(full_text) or ""
        fallback_bank = p1_ocr_bank or state.bank or detect_bank(doc_texts[0] if doc_texts else "") or ""
        for r in doc_rows:
            r.account = r.account or fallback_acc
            r.bank = r.bank or fallback_bank
        for d in doc_pages:
            d["account"] = d.get("account") or fallback_acc
            d["bank"] = d.get("bank") or fallback_bank
        pages.extend(doc_pages)

        explicit = [r.date_parts[2] for r in doc_rows if r.date_parts and r.date_parts[2]]
        if not explicit:
            explicit = [int(y) for y in re.findall(r"(?<![\d:])(20[0-3]\d|199\d)(?![\d:])", full_text)]
        explicit = [y for y in explicit if 1990 <= y <= 2100]
        doc_year = Counter(explicit).most_common(1)[0][0] if explicit else None
        if doc_year:
            all_years.append(doc_year)

        apply_dates(doc_rows, doc_year)
        doc_printed = {**printed_totals(full_text), **state.printed}

        by_acc: dict[str, list[ExtractedRow]] = {}
        for r in doc_rows:
            by_acc.setdefault(r.account, []).append(r)

        for acc, rlist in by_acc.items():
            openings, body = [r for r in rlist if r.is_opening], [r for r in rlist if not r.is_opening]
            if _chain_matches(openings[:1] + body[::-1])[0] > _chain_matches(openings[:1] + body)[0]:
                rlist[:] = openings + body[::-1]
            account_rows.setdefault(acc, []).extend(rlist)
            if fallback_bank and not account_banks.get(acc):
                account_banks[acc] = fallback_bank
            if len(by_acc) == 1 and doc_printed:
                account_printed.setdefault(acc, {}).update(doc_printed)
            if doc_year:
                account_years.setdefault(acc, []).append(doc_year)

    if "" in account_rows and len(account_rows) > 1:
        for r in (unknown_rows := account_rows.pop("")):
            r.account = "Unknown"
        account_rows["Unknown"] = unknown_rows
        if "" in account_printed:
            account_printed["Unknown"] = account_printed.pop("")
        if "" in account_years:
            account_years["Unknown"] = account_years.pop("")
        if "" in account_banks:
            account_banks["Unknown"] = account_banks.pop("")

    overall_year = Counter(all_years).most_common(1)[0][0] if all_years else None
    accounts_out: dict[str, dict[str, Any]] = {}
    combined_rows: list[ExtractedRow] = []

    for acc, rlist in account_rows.items():
        acc_year = Counter(account_years.get(acc, [])).most_common(1)[0][0] if account_years.get(acc) else overall_year
        apply_dates(rlist, acc_year)
        if len(input_paths) > 1:
            rlist.sort(key=lambda row: _row_date_key(row, acc_year))
            seen_op = False
            rlist[:] = [r for r in rlist if not (r.is_opening and (seen_op or not (seen_op := True)))]
        acc_summary = _summarize_account(rlist, account_printed.get(acc, {}))
        if not rlist and not account_printed.get(acc):
            continue
        bank_name = account_banks.get(acc) or next((r.bank for r in rlist if r.bank), "")
        acc_summary["bank"] = bank_name
        acc_summary["account"] = acc
        accounts_out[acc or "Default"] = {
            "account": acc, "bank": bank_name, "rows": rlist,
            "document_year": acc_year, "summary": acc_summary,
        }
        combined_rows.extend(rlist)

    if not accounts_out:
        return {"pages": pages, "rows": [], "accounts": {}, "document_year": overall_year, "summary": _summarize_account([], {})}

    if len(accounts_out) == 1:
        only = next(iter(accounts_out.values()))
        summary = {
            **only["summary"], "account": only["account"], "bank": only["bank"],
            "accounts": {k: v["summary"] for k, v in accounts_out.items()},
        }
    else:
        reconciles = [v["summary"]["chain_reconciles"] for v in accounts_out.values() if v["summary"]["chain_reconciles"] is not None]
        totals_match = [v["summary"]["printed_totals_match"] for v in accounts_out.values() if "printed_totals_match" in v["summary"]]
        summary = {
            "total_debit": sum(v["summary"]["total_debit"] for v in accounts_out.values()),
            "total_credit": sum(v["summary"]["total_credit"] for v in accounts_out.values()),
            "rows_flagged": sum(v["summary"]["rows_flagged"] for v in accounts_out.values()),
            "chain_reconciles": all(reconciles) if reconciles else None,
            "accounts": {k: v["summary"] for k, v in accounts_out.items()},
            **({"printed_totals_match": all(totals_match)} if totals_match else {}),
        }

    return {"pages": pages, "rows": combined_rows, "accounts": accounts_out, "document_year": overall_year, "summary": summary}

extract_statements = extract_statement

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Indonesian bank statements to Excel.")
    parser.add_argument("file", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, default=Path("output"))
    args = parser.parse_args()
    result = extract_statement(args.file)
    from .export import excel_bytes
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "transactions.xlsx").write_bytes(excel_bytes(result))
    print(f"Extracted {len(result['rows'])} transaction rows to {args.output}")
    print(result["summary"])

if __name__ == "__main__":
    main()