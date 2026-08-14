# app/parsers/registry.py
"""
Parser Registry
-----------------
Tries each registered parser in order and uses the first one whose
can_parse() accepts the file. To add a new format later (CSV, OCR),
just write a new BaseParser subclass and add it to this list -
nothing else in the pipeline needs to change.
"""
# app/parsers/registry.py
from app.parsers.base import BaseParser
from app.parsers.pdf_parser import PdfParser
from app.parsers.markdown_parser import MarkdownTableParser
from app.models.transaction import ParseResult

REGISTERED_PARSERS: list[BaseParser] = [
    PdfParser(),
    MarkdownTableParser(),
]

def parse_file(file_path: str, account_id: str) -> ParseResult:
    for parser in REGISTERED_PARSERS:
        if parser.can_parse(file_path):
            try:
                return parser.extract(file_path, account_id)
            except Exception:
                continue
    raise ValueError(
        f"No parser could handle file '{file_path}'. "
        "Supported so far: PDF (text-based), Markdown/text tables."
    )