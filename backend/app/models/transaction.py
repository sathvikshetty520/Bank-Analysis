# app/models/transaction.py
"""
Standard Transaction Schema
----------------------------
Every parser (CSV, Excel, PDF, OCR) must convert its bank-specific format
into THIS schema. Nothing downstream ever touches raw bank files again.
"""

from datetime import date as date_type
from typing import Optional
from pydantic import BaseModel, Field


class Transaction(BaseModel):
    account_id: str
    date: date_type
    narration: str
    ref_no: Optional[str] = None
    debit: float = 0.0
    credit: float = 0.0
    balance: Optional[float] = None

    source_file: str
    source_row: int
    extraction_confidence: float = Field(default=1.0)

    is_duplicate: bool = False
    is_reversed_transaction: bool = False
    reversal_confidence: Optional[str] = None  # "confirmed" (narration keyword) or "possible" (amount match only)
    balance_mismatch: bool = False
    category: Optional[str] = None  # assigned by categorization.py after cleaning


class ParseResult(BaseModel):
    transactions: list[Transaction]
    warnings: list[str] = []
    parser_used: str
    source_file: str