# app/parsers/markdown_parser.py
"""
Markdown Table Parser
------------------------
Handles bank statements already in markdown pipe-table format
(| col | col | col |), e.g. from OCR/conversion tools that output
clean tables. Reuses the same column-synonym matching as other parsers.
"""

import re
from datetime import datetime
import pandas as pd

from app.models.transaction import Transaction, ParseResult
from app.parsers.base import BaseParser, map_columns


class MarkdownTableParser(BaseParser):
    name = "markdown_table_parser"

    def can_parse(self, file_path: str) -> bool:
        if not file_path.lower().endswith((".md", ".txt")):
            return False
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            sample = f.read(3000)
        return "|---|" in sample.replace(" ", "") or bool(re.search(r"\|.+\|.+\|", sample))

    def _clean_cell(self, cell: str) -> str:
        cell = re.sub(r"<br\s*/?>", " ", cell)
        cell = re.sub(r"\*\*|__|<mark>|</mark>", "", cell)
        return cell.strip()

    def _parse_amount(self, val) -> float:
        if val is None:
            return 0.0
        s = str(val).strip().replace(",", "")
        s = re.sub(r"(Cr|Dr|CR|DR)$", "", s).strip()
        if s == "" or s.lower() == "nan":
            return 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0

    def _parse_date(self, val) -> datetime.date:
        s = str(val).strip()
        for fmt in ("%d/%m/%y", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        parsed = pd.to_datetime(s, dayfirst=True, errors="coerce")
        return parsed.date() if not pd.isna(parsed) else None

    def _extract_table_blocks(self, lines: list[str]) -> list[list[list[str]]]:
        """Find every markdown table in the file and return them as lists of row-cells."""
        tables = []
        current = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                if re.fullmatch(r"\|[\s\-:|]+\|", stripped):
                    continue  # skip separator row like |---|---|
                cells = [self._clean_cell(c) for c in stripped.strip("|").split("|")]
                current.append(cells)
            else:
                if current:
                    tables.append(current)
                    current = []
        if current:
            tables.append(current)
        return tables

    def extract(self, file_path: str, account_id: str) -> ParseResult:
        warnings: list[str] = []
        transactions: list[Transaction] = []
        row_counter = 0

        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        tables = self._extract_table_blocks(lines)

        for table in tables:
            if len(table) < 2:
                continue

            header_row_idx = None
            col_map = {}
            best_score = 0
            for i in range(min(8, len(table))):
                mapping = map_columns(table[i])
                if len(mapping) > best_score:
                    best_score = len(mapping)
                    header_row_idx = i
                    col_map = mapping
            if header_row_idx is None or best_score < 4:
                continue

            headers = table[header_row_idx]
            data_rows = table[header_row_idx + 1:]

            for row in data_rows:
                row_counter += 1
                if len(row) != len(headers):
                    continue
                row_dict = dict(zip(headers, row))

                date_val = row_dict.get(col_map.get("date", ""), "")
                if not date_val or date_val.lower() == "opening balance":
                    continue

                txn_date = self._parse_date(date_val)
                if txn_date is None:
                    warnings.append(f"Row {row_counter}: unparseable date '{date_val}', skipped.")
                    continue

                narration = row_dict.get(col_map.get("narration", ""), "").strip()
                ref_no = row_dict.get(col_map.get("ref_no", ""), "").strip() or None
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
                        extraction_confidence=0.95,
                    )
                )

        if not transactions:
            warnings.append("No transactions extracted from markdown tables.")

        return ParseResult(
            transactions=transactions,
            warnings=warnings,
            parser_used=self.name,
            source_file=file_path,
        )