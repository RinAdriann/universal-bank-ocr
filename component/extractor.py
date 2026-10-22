import argparse
import re
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from .balancecalc import chain_matches, reconcile
from .constants import SUPPORTED_INPUT_SUFFIXES
from .export import excel_bytes
from .layout import apply_dates, printed_totals, process_page
from .ocr import extract_header_info, iter_pages
from .structures import ExtractedRow, State


def row_date_key(row: ExtractedRow, year: int | None) -> tuple[int, int, int, int]:
    if row.is_opening:
        return (0, 0, 0, 0)
    if row.date_parts is None:
        return (2, 0, 0, 0)
    day, month, row_year = row.date_parts[:3]
    return (1, row_year or year or 0, month, day)


def group_key_label(bank: str, acc: str) -> str:
    if bank and acc:
        return f"{bank} - {acc}"
    return bank or acc or "Default"


def dedupe_openings(rows: list[ExtractedRow]) -> list[ExtractedRow]:
    kept: list[ExtractedRow] = []
    seen_opening = False
    for row in rows:
        if row.is_opening:
            if seen_opening:
                continue
            seen_opening = True
        kept.append(row)
    return kept


def summarize_account(rows: list[ExtractedRow], printed: dict[str, float]) -> dict[str, Any]:
    info = reconcile(rows, None if any(r.is_opening for r in rows) else printed.get("opening"))
    body = [r for r in rows if not r.is_opening]
    opening = next((r.balance for r in rows if r.is_opening), printed.get("opening", info.get("inferred_opening_balance")))
    balances = [r.balance for r in body if r.balance is not None]
    total_debit = sum(r.debit or 0.0 for r in body)
    total_credit = sum(r.credit or 0.0 for r in body)
    closing = balances[-1] if balances else printed.get("closing")
    reconciles = abs(opening + total_credit - total_debit - closing) < 1.0 if opening is not None and closing is not None else None

    summary: dict[str, Any] = {
        **info,
        "opening_balance": opening,
        "closing_balance": closing,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "rows_flagged": sum(bool(r.validation) for r in rows),
        "chain_reconciles": reconciles,
    }
    if printed:
        summary["printed_totals"] = printed
        computed = {"debit": total_debit, "credit": total_credit, "closing": closing, "opening": opening}
        summary["printed_totals_match"] = all(
            computed[k] is not None and abs(printed[k] - computed[k]) < 1.0
            for k in printed
            if k in computed
        )
    return summary


def extract_statement(pdf_path: str | Path | Sequence[str | Path], dpi: int = 220) -> dict[str, Any]:
    raw_paths = [pdf_path] if isinstance(pdf_path, (str, Path)) else list(pdf_path)
    if not raw_paths:
        raise ValueError("At least one input file is required.")
    input_paths = [Path(p) for p in raw_paths]
    for path in input_paths:
        if path.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
            raise ValueError(f"Unsupported file format: {path.suffix}. Needs: {', '.join(sorted(SUPPORTED_INPUT_SUFFIXES))}")

    pages: list[dict] = []
    all_years: list[int] = []
    account_rows: dict[tuple[str, str], list[ExtractedRow]] = {}
    account_printed: dict[tuple[str, str], dict[str, float]] = {}
    account_years: dict[tuple[str, str], list[int]] = {}

    for path in input_paths:
        state = State()
        doc_rows: list[ExtractedRow] = []
        doc_pages: list[dict] = []
        doc_texts: list[str] = []

        hdr_bank, hdr_acc = extract_header_info(path)
        if hdr_bank:
            state.bank = hdr_bank
        if hdr_acc:
            state.account = hdr_acc

        for page_no, words, page_w, source in iter_pages(path, dpi):
            page_rows, diag, page_text = process_page(words, page_w, page_no, state)
            diag["source"] = source
            diag["words"] = len(words)
            if len(input_paths) > 1:
                diag["file"] = path.name
            doc_rows.extend(page_rows)
            doc_pages.append(diag)
            doc_texts.append(page_text)

        doc_acc = hdr_acc or state.account or ""
        doc_bank = hdr_bank or state.bank or ""

        for row in doc_rows:
            row.account = doc_acc
            row.bank = doc_bank
        for diag in doc_pages:
            diag["account"] = doc_acc
            diag["bank"] = doc_bank
        pages.extend(doc_pages)

        full_text = "\n".join(doc_texts)
        explicit = [r.date_parts[2] for r in doc_rows if r.date_parts and r.date_parts[2]]
        if not explicit:
            explicit = [int(y) for y in re.findall(r"(?<![\d:])(20[0-3]\d|199\d)(?![\d:])", full_text)]
        explicit = [y for y in explicit if 1990 <= y <= 2100]
        doc_year = Counter(explicit).most_common(1)[0][0] if explicit else None
        if doc_year:
            all_years.append(doc_year)

        apply_dates(doc_rows, doc_year)
        doc_printed = {**printed_totals(full_text), **state.printed}

        group_key = (doc_bank, doc_acc)
        openings = [r for r in doc_rows if r.is_opening]
        body = [r for r in doc_rows if not r.is_opening]
        if chain_matches(openings[:1] + body[::-1])[0] > chain_matches(openings[:1] + body)[0]:
            doc_rows[:] = openings + body[::-1]
        account_rows.setdefault(group_key, []).extend(doc_rows)
        if doc_printed:
            account_printed.setdefault(group_key, {}).update(doc_printed)
        if doc_year:
            account_years.setdefault(group_key, []).append(doc_year)

    for bnk, acc in list(account_rows.keys()):
        if not bnk and acc:
            match_key = next(((b, a) for b, a in account_rows if b and a == acc), None)
            if match_key:
                rows_to_move = account_rows.pop((bnk, acc))
                for row in rows_to_move:
                    row.bank = match_key[0]
                account_rows[match_key].extend(rows_to_move)
                if (bnk, acc) in account_printed:
                    account_printed.setdefault(match_key, {}).update(account_printed.pop((bnk, acc)))
                if (bnk, acc) in account_years:
                    account_years.setdefault(match_key, []).extend(account_years.pop((bnk, acc)))

    if ("", "") in account_rows and len(account_rows) > 1:
        unknown_rows = account_rows.pop(("", ""))
        for row in unknown_rows:
            row.account = "Unknown"
        account_rows[("", "Unknown")] = unknown_rows
        if ("", "") in account_printed:
            account_printed[("", "Unknown")] = account_printed.pop(("", ""))
        if ("", "") in account_years:
            account_years[("", "Unknown")] = account_years.pop(("", ""))

    overall_year = Counter(all_years).most_common(1)[0][0] if all_years else None
    accounts_out: dict[str, dict[str, Any]] = {}
    combined_rows: list[ExtractedRow] = []

    for group_key, rows_list in account_rows.items():
        bank_name, acc = group_key
        years = account_years.get(group_key, [])
        acc_year = Counter(years).most_common(1)[0][0] if years else overall_year
        apply_dates(rows_list, acc_year)
        if len(input_paths) > 1:
            rows_list.sort(key=lambda row: row_date_key(row, acc_year))
            rows_list[:] = dedupe_openings(rows_list)
        acc_summary = summarize_account(rows_list, account_printed.get(group_key, {}))
        if not rows_list and not account_printed.get(group_key):
            continue
        bank_name = bank_name or next((r.bank for r in rows_list if r.bank), "")
        acc_summary["bank"] = bank_name
        acc_summary["account"] = acc
        out_key = group_key_label(bank_name, acc)
        accounts_out[out_key] = {
            "account": acc,
            "bank": bank_name,
            "rows": rows_list,
            "document_year": acc_year,
            "summary": acc_summary,
        }
        combined_rows.extend(rows_list)

    if not accounts_out:
        return {
            "pages": pages,
            "rows": [],
            "accounts": {},
            "document_year": overall_year,
            "summary": summarize_account([], {}),
        }

    if len(accounts_out) == 1:
        only = next(iter(accounts_out.values()))
        summary = {
            **only["summary"],
            "account": only["account"],
            "bank": only["bank"],
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
        }
        if totals_match:
            summary["printed_totals_match"] = all(totals_match)

    return {
        "pages": pages,
        "rows": combined_rows,
        "accounts": accounts_out,
        "document_year": overall_year,
        "summary": summary,
    }


extract_statements = extract_statement


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Indonesian bank statements to Excel.")
    parser.add_argument("file", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, default=Path("output"))
    args = parser.parse_args()
    result = extract_statement(args.file)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.xlsx").write_bytes(excel_bytes(result))
    print(f"Extracted {len(result['rows'])} transaction rows to {args.output}")
    print(result["summary"])


if __name__ == "__main__":
    main()
