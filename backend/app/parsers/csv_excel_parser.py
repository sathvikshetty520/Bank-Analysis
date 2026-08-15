"""
CSV / Excel Parser
-------------------
Handles .csv, .xls, .xlsx bank statement exports.

Bank exports often have a few junk rows at the top (bank name/address/
account summary) before the real header row, so we scan the first N
rows looking for the one that best matches our known column synonyms -
same approach as the PDF/markdown parsers, reusing map_columns() so
all format parsers behave consistently. Picks the BEST-matching row,
not just the first partial match - lesson learned from a real bug in
the markdown parser where a partial-match-first approach picked the
wrong header row.
"""

import pandas as pd
from datetime import datetime

from app.models.transaction import Transaction, ParseResult
from app.parsers.base import BaseParser, map_columns

MAX_HEADER_SEARCH_ROWS = 15
MIN_MATCHED_FIELDS_FOR_HEADER = 3


class CsvExcelParser(BaseParser):
    name = "csv_excel_parser"

    def can_parse(self, file_path: str) -> bool:
        return file_path.lower().endswith((".csv", ".xls", ".xlsx"))

    def _load_raw(self, file_path: str) -> pd.DataFrame:
        if file_path.lower().endswith(".csv"):
            # Real bank CSV exports often have "ragged" junk rows at the
            # top (inconsistent column counts per line) before the real
            # transaction table starts. pandas' fast C parser throws a
            # hard error the moment row widths are inconsistent - found
            # via a real SBI statement export that crashed here. Reading
            # rows manually via the csv module and padding to the widest
            # row avoids this entirely.
            import csv
            rows = []
            max_cols = 0
            with open(file_path, "r", encoding="utf-8-sig", errors="ignore") as f:
                reader = csv.reader(f)
                for row in reader:
                    rows.append(row)
                    max_cols = max(max_cols, len(row))
            padded = [row + [""] * (max_cols - len(row)) for row in rows]
            return pd.DataFrame(padded, dtype=str)
        return pd.read_excel(file_path, header=None, dtype=str)

    def _find_header_row(self, raw: pd.DataFrame):
        best_score = 0
        best_idx = None
        best_map = {}
        for row_idx in range(min(MAX_HEADER_SEARCH_ROWS, len(raw))):
            candidate_headers = [str(v) for v in raw.iloc[row_idx].tolist()]
            mapping = map_columns(candidate_headers)
            if len(mapping) > best_score:
                best_score = len(mapping)
                best_idx = row_idx
                best_map = mapping
        if best_idx is None or best_score < MIN_MATCHED_FIELDS_FOR_HEADER:
            raise ValueError(
                f"Could not detect a header row with recognizable columns "
                f"in the first {MAX_HEADER_SEARCH_ROWS} rows."
            )
        return best_idx, best_map

    def _parse_amount(self, val) -> float:
        if val is None:
            return 0.0
        s = str(val).strip().replace(",", "")
        if s[-2:].lower() in ("cr", "dr"):
            s = s[:-2].strip()
        if s == "" or s.lower() == "nan":
            return 0.0
        negative = s.startswith("(") and s.endswith(")")
        s = s.strip("()")
        try:
            amount = float(s)
            return -amount if negative else amount
        except ValueError:
            return 0.0

    def _parse_date(self, val) -> datetime.date:
        s = str(val).strip()
        for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        parsed = pd.to_datetime(s, dayfirst=True, errors="coerce")
        return parsed.date() if not pd.isna(parsed) else None

    def extract(self, file_path: str, account_id: str) -> ParseResult:
        warnings: list[str] = []
        raw = self._load_raw(file_path)
        header_row_idx, col_map = self._find_header_row(raw)

        headers = [str(v) for v in raw.iloc[header_row_idx].tolist()]
        data = raw.iloc[header_row_idx + 1:].copy()
        data.columns = headers

        for required in ["date", "narration"]:
            if required not in col_map:
                warnings.append(f"Could not find a '{required}' column - rows may be unreliable.")

        transactions: list[Transaction] = []
        for i, row in data.iterrows():
            if row.astype(str).str.strip().eq("").all():
                continue

            date_val = row.get(col_map.get("date", ""), None)
            if date_val is None or str(date_val).strip() == "":
                continue

            txn_date = self._parse_date(date_val)
            if txn_date is None:
                warnings.append(f"Row {i}: unparseable date '{date_val}', skipped.")
                continue

            narration = str(row.get(col_map.get("narration", ""), "")).strip()
            ref_no = str(row.get(col_map.get("ref_no", ""), "")).strip() or None
            raw_debit = self._parse_amount(row.get(col_map.get("debit", ""), 0))
            raw_credit = self._parse_amount(row.get(col_map.get("credit", ""), 0))

            # Some banks represent a reversal as a NEGATIVE debit (rather than
            # a credit entry) - e.g. a "UPI/REV/..." row with debit=-1.00.
            # Blindly taking abs() of this turns a reversal into what looks
            # like another charge, breaking the balance-chain math (found on
            # a real SBI statement: this exact pattern caused every reversed
            # transaction to show a balance mismatch of 2x the reversal
            # amount). Convert a negative debit/credit into the opposite
            # column instead of discarding its sign.
            debit = raw_debit
            credit = raw_credit
            if debit < 0:
                credit += abs(debit)
                debit = 0.0
            elif credit < 0:
                debit += abs(credit)
                credit = 0.0

            balance_raw = row.get(col_map.get("balance", ""), None)
            balance = self._parse_amount(balance_raw) if balance_raw is not None else None

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
                    source_row=int(i),
                    extraction_confidence=1.0,
                )
            )

        return ParseResult(
            transactions=transactions,
            warnings=warnings,
            parser_used=self.name,
            source_file=file_path,
        )