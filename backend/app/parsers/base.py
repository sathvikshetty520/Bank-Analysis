# app/parsers/base.py
"""
Base Parser Interface
----------------------
Every bank/format gets its own parser class implementing this interface.
The pipeline tries each parser's can_parse() until one accepts the file,
then calls extract(). This isolates each format so adding a new bank
layout later never requires touching existing code.
"""


from abc import ABC, abstractmethod
from app.models.transaction import ParseResult
 
class BaseParser(ABC):
    name: str = "base"
    @abstractmethod
    def can_parse(self, file_path: str) -> bool:
        raise NotImplementedError
    @abstractmethod
    def extract(self, file_path: str, account_id: str) -> ParseResult:
        raise NotImplementedError
 
COLUMN_SYNONYMS = {
    "date": ["date", "txn date", "trans date", "transaction date", "value date", "posting date"],
    "narration": ["narration", "description", "particulars", "details", "remarks", "transaction remarks"],
    "ref_no": ["ref no", "reference no", "ref number", "cheque no", "chq no", "transaction id", "utr", "utr no",
               "cheque / instrument", "cheque/instrument", "instrument"],
    "debit": ["debit", "withdrawal", "withdrawal amt", "withdrawal amount", "dr", "debit amount", "debits"],
    "credit": ["credit", "deposit", "deposit amt", "deposit amount", "cr", "credit amount", "credits"],
    "balance": ["balance", "closing balance", "available balance", "balance amount", "running balance"],
}
 
def normalize_header(header: str) -> str:
    # Real PDF tables often break headers across lines, e.g. "TRANS\nDATE".
    # Collapse any whitespace (newlines, tabs, multiple spaces) into single spaces.
    header = " ".join(header.split())
    return header.strip().lower().replace(".", "").replace("_", " ")
 
def map_columns(headers: list[str]) -> dict[str, str]:
    normalized = {h: normalize_header(h) for h in headers}
    result = {}
    for std_field, synonyms in COLUMN_SYNONYMS.items():
        for original, norm in normalized.items():
            if norm in synonyms:
                result[std_field] = original
                break
    return result
 