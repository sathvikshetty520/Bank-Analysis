"""
PDF Text-Line Parser (fallback for borderless statements)
-------------------------------------------------------------
Some bank PDFs have no visible table borders/lines, so pdfplumber's
extract_tables() finds nothing even though the data is present as
clean, well-aligned text (each transaction on one line, ending in
three trailing amount columns: debit, credit, balance).

This parser reads raw page text instead of trying to detect a table,
and uses a date-anchored regex to find where each transaction starts.
Any line that doesn't start with a date is treated as a continuation
of the previous transaction's narration (common for UPI/NEFT narrations
that wrap across multiple lines).

This is registered AFTER PdfParser in the registry and is only tried
if the table-based parser finds zero transactions - bordered-table
PDFs should keep using the more structurally reliable table parser.
"""

import re
import pdfplumber
from datetime import datetime
import pandas as pd

from app.models.transaction import Transaction, ParseResult
from app.parsers.base import BaseParser

# Matches a transaction start line: "23-FEB-2025 23-FEB-2025 <narration...>"
# Extend this pattern if other banks use different date formats
# (e.g. DD/MM/YYYY) - this is inherently format-specific.
LINE_PATTERN = re.compile(r'^(\d{2}-[A-Z]{3}-\d{4})\s+(\d{2}-[A-Z]{3}-\d{4})\s+(.*)$')

# Matches the three trailing amount columns at the end of a transaction line.
AMOUNT_PATTERN = re.compile(r'([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$')

# Lines that should never be treated as narration continuation, even
# though they don't match LINE_PATTERN (repeated headers/footers on
# every page).
SKIP_PREFIXES = ("TXN DATE", "STATEMENT", "PERIOD", "BRANCH", "PAGE ", "A/C ", "CUSTOMER ID")


class PdfTextLineParser(BaseParser):
    name = "pdf_text_line_parser"

    def can_parse(self, file_path: str) -> bool:
        return file_path.lower().endswith(".pdf")

    def _parse_amount(self, val) -> float:
        if not val:
            return 0.0
        s = str(val).replace(",", "").strip()
        try:
            return float(s)
        except ValueError:
            return 0.0

    def _parse_date(self, val: str):
        try:
            return datetime.strptime(val.strip(), "%d-%b-%Y").date()
        except ValueError:
            parsed = pd.to_datetime(val, dayfirst=True, errors="coerce")
            return parsed.date() if not pd.isna(parsed) else None

    def extract(self, file_path: str, account_id: str) -> ParseResult:
        warnings: list[str] = []
        transactions: list[Transaction] = []
        row_counter = 0

        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text()
                if not text:
                    continue

                current = None  # holds the in-progress transaction dict for this page

                for raw_line in text.splitlines():
                    line = raw_line.strip()
                    if not line:
                        continue

                    m = LINE_PATTERN.match(line)
                    if m:
                        # finalize the previous transaction before starting a new one
                        if current:
                            transactions.append(self._finalize(current, account_id, file_path, row_counter))
                            row_counter += 1

                        txn_date_str, _value_date_str, rest = m.groups()
                        amt_match = AMOUNT_PATTERN.search(rest)
                        if amt_match:
                            debit, credit, balance = amt_match.groups()
                            narration = rest[:amt_match.start()].strip()
                        else:
                            debit = credit = balance = None
                            narration = rest
                            warnings.append(
                                f"Page {page_num+1}: could not find debit/credit/balance on line: '{line[:60]}...'"
                            )

                        current = {
                            "date_str": txn_date_str,
                            "narration": narration,
                            "debit": debit,
                            "credit": credit,
                            "balance": balance,
                        }
                    else:
                        # continuation line - append to current transaction's narration,
                        # unless it's a repeated header/footer line
                        if current and not line.upper().startswith(SKIP_PREFIXES):
                            current["narration"] += " " + line

                if current:
                    transactions.append(self._finalize(current, account_id, file_path, row_counter))
                    row_counter += 1

        if not transactions:
            warnings.append("No transactions extracted via text-line parsing either.")

        return ParseResult(
            transactions=transactions,
            warnings=warnings,
            parser_used=self.name,
            source_file=file_path,
        )

    def _finalize(self, current: dict, account_id: str, file_path: str, row_counter: int) -> Transaction:
        txn_date = self._parse_date(current["date_str"])
        return Transaction(
            account_id=account_id,
            date=txn_date,
            narration=current["narration"].strip(),
            ref_no=None,
            debit=self._parse_amount(current["debit"]),
            credit=self._parse_amount(current["credit"]),
            balance=self._parse_amount(current["balance"]) if current["balance"] else None,
            source_file=file_path,
            source_row=row_counter,
            extraction_confidence=0.85,  # lower than table extraction - line-based heuristic
        )