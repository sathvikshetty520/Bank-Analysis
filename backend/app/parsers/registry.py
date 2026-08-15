# app/parsers/registry.py
"""
Parser Registry
-----------------
Tries each registered parser in order. A parser "succeeds" only if it
returns at least one transaction - an empty result (even with no
exception) is treated as a miss, so the pipeline falls through to the
next parser. This matters for PDFs specifically: some have bordered
tables (PdfParser handles these), others have no table structure at
all but well-formatted text (PdfTextLineParser handles these) - both
register can_parse()=True for .pdf files, so this "try next on empty
result" behavior is what lets the fallback actually kick in.
"""

from app.parsers.base import BaseParser
from app.parsers.pdf_parser import PdfParser
from app.parsers.markdown_parser import MarkdownTableParser
from app.parsers.pdf_text_line_parser import PdfTextLineParser
from app.models.transaction import ParseResult

REGISTERED_PARSERS: list[BaseParser] = [
    PdfParser(),            # try bordered-table extraction first
    MarkdownTableParser(),
    PdfTextLineParser(),    # fallback for PDFs with no table borders
]


def parse_file(file_path: str, account_id: str) -> ParseResult:
    last_result = None
    for parser in REGISTERED_PARSERS:
        if not parser.can_parse(file_path):
            continue
        try:
            result = parser.extract(file_path, account_id)
        except Exception:
            continue
        if result.transactions:
            return result
        last_result = result  # keep the most recent empty result in case ALL parsers fail

    if last_result is not None:
        return last_result  # return the empty result with its warnings, rather than a bare error

    raise ValueError(f"No parser could handle file '{file_path}'.")