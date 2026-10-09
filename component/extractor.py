"""Main extractor."""

import argparse
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .models import State, ExtractedRow
from .vision import iter_pages
from .constants import SUPPORTED_INPUT_SUFFIXES
from .layout import process_page, apply_dates, printed_totals
from .reconciliation import reconcile

def _row_date_key(row: ExtractedRow, year: int | None) -> tuple[int, int, int, int]:
    if row.date_parts is None:
        return (1, 0, 0, 0)
    day, month, row_year = row.date_parts
    return (0, row_year or year or 0, month, day)

def extract_statement(pdf_path: str | Path, dpi: int = 220) -> dict[str, Any]:
    input_path = Path(pdf_path)
    if input_path.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
        raise ValueError(f"Unsupported file format. Needs: {', '.join(sorted(SUPPORTED_INPUT_SUFFIXES))}")

    rows: list[ExtractedRow] = []
    pages: list[dict] = []
    all_text: list[str] = []
    state = State()

    for number, words, page_w, source in iter_pages(input_path, dpi):
        page_rows, diag, page_text = process_page(words, page_w, number, state)
        diag["source"] = source
        diag["words"] = len(words)
        rows.extend(page_rows)
        pages.append(diag)
        all_text.append(page_text)

    full_text = "\n".join(all_text)
    explicit = [r.date_parts[2] for r in rows if r.date_parts and r.date_parts[2]]
    if not explicit:
        explicit = [int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", full_text)]
    explicit = [y for y in explicit if 2000 <= y <= 2100]
    year = Counter(explicit).most_common(1)[0][0] if explicit else None

    apply_dates(rows, year)
    rows.sort(key=lambda row: _row_date_key(row, year))
    printed = {**printed_totals(full_text), **state.printed}
    has_opening_row = any(r.is_opening for r in rows)
    info = reconcile(rows, None if has_opening_row else printed.get("opening"))

    body = [r for r in rows if not r.is_opening]
    opening = next(
        (r.balance for r in rows if r.is_opening),
        printed.get("opening", info.get("inferred_opening_balance")),
    )
    balances = [r.balance for r in (body[::-1] if info["order"] == "newest_first" else body) if r.balance is not None]
    total_debit = sum(r.debit or 0.0 for r in body)
    total_credit = sum(r.credit or 0.0 for r in body)
    closing = balances[-1] if balances else printed.get("closing")
    summary: dict[str, Any] = {
        **info,
        "opening_balance": opening, "closing_balance": closing,
        "total_debit": total_debit, "total_credit": total_credit,
        "rows_flagged": sum(bool(r.validation) for r in rows),
        "chain_reconciles": (abs(opening + total_credit - total_debit - closing) < 1.0
                             if opening is not None and closing is not None else None),
    }
    if printed:
        summary["printed_totals"] = printed
        computed = {"debit": total_debit, "credit": total_credit, "closing": closing, "opening": opening}
        summary["printed_totals_match"] = all(
            computed[k] is not None and abs(printed[k] - computed[k]) < 1.0 for k in printed if k in computed
        )
    return {"pages": pages, "rows": rows, "document_year": year, "summary": summary}

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Indonesian bank statements to Excel.")
    parser.add_argument("file", type=Path)
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
