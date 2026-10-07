"""Backward-compatible facade for the modular bank_ocr package.

Prefer importing from :mod:`bank_ocr` for application code.
"""

from component.extractor import (
    Amount,
    ExtractedRow,
    _category,
    _reconcile,
    excel_bytes,
    extract_statement,
    main,
    parse_amount,
)

__all__ = [
    "Amount",
    "ExtractedRow",
    "excel_bytes",
    "extract_statement",
    "main",
    "parse_amount",
]


if __name__ == "__main__":
    main()
