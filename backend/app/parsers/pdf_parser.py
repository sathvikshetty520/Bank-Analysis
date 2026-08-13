# app/parsers/pdf_parser.py
"""
PDF Parser (text-based bank statement PDFs)
---------------------------------------------
Handles PDFs where text can be selected/copied (i.e. NOT scanned images).
Uses pdfplumber to extract tables page by page, then reuses the same
column-synonym mapping logic as the CSV parser so both formats funnel
into the identical standardized Transaction schema.

If a PDF has no extractable tables (common with scanned documents),
this parser will produce zero transactions and a warning - that's the
signal to fall back to the OCR parser (built later).
"""
import pdfplumber
from datetime import datetime
import pandas as pd
 
from app.models.transaction import Transaction, ParseResult
from app.parsers.base import BaseParser, map_columns
 
MIN_MATCHED_FIELDS_FOR_HEADER = 3
 
 
class PdfParser(BaseParser):
    name = "pdf_parser"
 
    def can_parse(self, file_path: str) -> bool:
        return file_path.lower().endswith(".pdf")
 
    def _parse_amount(self, val) -> float:
        if val is None:
            return 0.0
        s = str(val).strip().replace(",", "")
        if s == "" or s.lower() == "nan":
            return 0.0
        negative = s.startswith("(") and s.endswith(")")
        s = s.strip("()")
        try:
            amount = float(s)
            return -amount if negative else amount
        except ValueError:
            return 0.0
 
    def _parse_date(self, val):
        # Real PDFs sometimes wrap dates across lines too, e.g. "23-FEB-\n2025"
        s = " ".join(str(val).split()).replace("- ", "-").strip()
        for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        parsed = pd.to_datetime(s, dayfirst=True, errors="coerce")
        return parsed.date() if not pd.isna(parsed) else None
 
    def extract(self, file_path: str, account_id: str) -> ParseResult:
        warnings: list[str] = []
        transactions: list[Transaction] = []
        row_counter = 0
 
        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                tables = page.extract_tables()
                if not tables:
                    continue
 
                for table in tables:
                    if len(table) < 2:
                        continue
 
                    header_row_idx = None
                    col_map = {}
                    for i in range(min(3, len(table))):
                        candidate = [str(c) if c else "" for c in table[i]]
                        mapping = map_columns(candidate)
                        if len(mapping) >= MIN_MATCHED_FIELDS_FOR_HEADER:
                            header_row_idx = i
                            col_map = mapping
                            break
 
                    if header_row_idx is None:
                        continue
 
                    headers = [str(c) if c else "" for c in table[header_row_idx]]
                    data_rows = table[header_row_idx + 1:]
 
                    for row in data_rows:
                        row_counter += 1
                        row_dict = dict(zip(headers, row))
 
                        date_val = row_dict.get(col_map.get("date", ""), None)
                        if not date_val or str(date_val).strip() == "":
                            continue
 
                        txn_date = self._parse_date(date_val)
                        if txn_date is None:
                            warnings.append(f"Page {page_num+1}: unparseable date '{date_val}', skipped.")
                            continue
 
                        narration = str(row_dict.get(col_map.get("narration", ""), "") or "").replace("\n", " ").strip()
                        ref_no = str(row_dict.get(col_map.get("ref_no", ""), "") or "").strip() or None
                        debit = self._parse_amount(row_dict.get(col_map.get("debit", ""), 0))
                        credit = self._parse_amount(row_dict.get(col_map.get("credit", ""), 0))
                        balance_raw = row_dict.get(col_map.get("balance", ""), None)
                        balance = self._parse_amount(balance_raw) if balance_raw else None
 
                        transactions.append(
                            Transaction(
                                account_id=account_id,
                                date=txn_date,
                                narration=narration,
                                ref_no=ref_no,
                                debit=abs(debit),
                                credit=abs(credit),
                                balance=balance,
                                source_file=file_path,
                                source_row=row_counter,
                                extraction_confidence=0.9,
                            )
                        )
 
        if not transactions:
            warnings.append(
                "No transactions extracted. This PDF may be a scanned image - OCR fallback needed."
            )
 
        return ParseResult(
            transactions=transactions,
            warnings=warnings,
            parser_used=self.name,
            source_file=file_path,
        )
 