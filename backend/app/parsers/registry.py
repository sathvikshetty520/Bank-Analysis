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
from app.parsers.pdf_text_line_parser import PdfTextLineParser
from app.parsers.csv_excel_parser import CsvExcelParser
from app.models.transaction import ParseResult

REGISTERED_PARSERS: list[BaseParser] = [
    CsvExcelParser(),
    PdfParser(),
    MarkdownTableParser(),
    PdfTextLineParser(),
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
        last_result = result
    if last_result is not None:
        return last_result
    raise ValueError(f"No parser could handle file '{file_path}'.")