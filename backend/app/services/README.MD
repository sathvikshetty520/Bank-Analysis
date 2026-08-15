# Automated Bank Statement Analysis System

A tool to extract, clean, and analyze bank statements to help investigators
detect suspicious transactions, trace fund movement, and identify hidden
relationships between accounts.

## Status: In Progress (Phase 1-3 of roadmap)

## Architecture

The system is built as a pipeline with a pluggable parser layer, so that
every bank's differently-formatted statement converts into one standard
schema before any analysis happens. Nothing downstream (cleaning, fraud
detection, graph analysis) ever touches raw files directly.

```
Raw file (PDF/CSV/Excel/scanned image)
        |
        v
   Parser plugin (format-specific)
        |
        v
  Standard Transaction schema  <-- everything downstream uses ONLY this
        |
        v
  Cleaning & validation (duplicates, balance checks, reversed txns)
        |
        v
  Graph analysis (money flow, round-trip / cycle detection)
        |
        v
  Reporting (PDF/Excel export) -- not yet built
```

## What's built so far

### 1. Standard Transaction Schema
`app/models/transaction.py`

Defines `Transaction` (one standardized row: date, narration, debit,
credit, balance, ref_no, plus provenance metadata like source file/row
and extraction confidence) and `ParseResult` (what every parser returns:
a list of transactions + warnings).

### 2. Parser Interface + Column Synonym Matching
`app/parsers/base.py`

- `BaseParser`: abstract interface (`can_parse()`, `extract()`) that every
  format-specific parser implements.
- `COLUMN_SYNONYMS`: dictionary mapping standard field names (date,
  narration, debit, credit, balance, ref_no) to the many different labels
  banks use for them (e.g. "Withdrawal Amt" vs "Debits" vs "Dr").
- `normalize_header()` / `map_columns()`: matches a file's raw headers
  against the synonym dictionary. Handles real-world quirks like headers
  with embedded line breaks (e.g. `"TRANS\nDATE"` from PDF table extraction).

### 3. PDF Parser (text-based statements)
`app/parsers/pdf_parser.py`

Uses `pdfplumber` to extract tables from PDF bank statements page by page,
auto-detects the header row via the synonym matcher, and converts each
row into a standardized `Transaction`.

**Tested on a real 5-page, 72-transaction Bandhan Bank statement:**
all transactions extracted correctly, closing balance matched the
statement summary (₹48.00) exactly, zero warnings.

### 4. Parser Registry
`app/parsers/registry.py`

Dispatches an uploaded file to the first parser that accepts it. Currently
only `PdfParser` is registered; CSV/Excel and OCR parsers are planned
extension points, addable without touching existing code.

### 5. Data Cleaning & Validation
`app/services/cleaning.py`

- `detect_duplicates()`: flags transactions with identical
  date/amount/narration/ref_no.
- `validate_balance_chain()`: checks that each row's balance logically
  follows from the previous balance + debit/credit.
- `detect_reversed_transactions()`: flags same-amount debit/credit pairs
  within a short time window (naive heuristic - flags amount coincidences,
  not confirmed fraud).

**Tested on real data:** 0 duplicates, 0 balance mismatches (clean bank
export, as expected), 13 same-amount debit/credit pairs flagged for
review.

### 6. Money Flow Graph & Round-Trip Detection
`app/services/graph_analysis.py`

- `extract_counterparty()`: best-effort regex extraction of the other
  party's name from narration text, with different logic per transaction
  type (UPI/NEFT/RTGS/IMPS/ATM). Known to be fragile - narration formats
  vary by bank and this will need refinement as more formats are tested.
- `build_transaction_graph()`: builds a directed graph (NetworkX) where
  nodes are accounts/entities and edges are transactions.
- `detect_round_trips()`: finds cycles in the graph within a configurable
  time window - the core round-trip fraud pattern.
- `find_accumulation_accounts()`: ranks counterparties by net amount
  received - flags potential destination/mule accounts.

**Tested on real data:** correctly extracted counterparty names from
messy narrations, correctly excluded ATM withdrawals (cash, not
traceable) from the graph. 0 round trips found - expected, since
round-trip detection requires multiple accounts' statements to see
money return; single-statement view only shows outgoing/incoming to
this one account. Accumulation ranking did surface real leads (e.g.
₹10,30,000 net to one counterparty across two RTGS transfers).

## Not yet built

- CSV/Excel parser
- OCR + LLM fallback parser (for scanned statement images)
- Money trail / FIFO tracing (Requirement 5: "when this credit arrived,
  where did the money go")
- PDF/Excel report generation & export
- Frontend (upload UI, graph visualization)
- Multi-file / multi-account investigation session (currently tested
  against a single statement only)

## Setup

```bash
pip install -r requirements.txt --break-system-packages
```

## Known limitations / honest notes

- Counterparty name extraction from narrations is regex-based per
  transaction type and will break on bank formats not yet seen.
- Round-trip detection needs statements from multiple related accounts
  uploaded into the same investigation session to be meaningful.
- Reversed-transaction detection is amount-matching only; it will flag
  legitimate coincidental same-amount transactions as false positives.