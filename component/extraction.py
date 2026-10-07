"""Statement extraction API.

The implementation remains behind this boundary so callers do not depend on
the parser's layout and OCR internals.
"""

from .extractor import extract_statement

__all__ = ["extract_statement"]
