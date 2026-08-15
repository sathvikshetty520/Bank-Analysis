# Automated Bank Statement Analysis System

A tool to extract, clean, and analyze bank statements to help investigators
detect suspicious transactions, trace fund movement, and identify hidden
relationships between accounts.

## Status: In Progress

Working end-to-end pipeline (upload -> parse -> clean -> analyze -> view)
tested against real bank statements from 3 different formats/banks.

## Architecture

Built as a pipeline with a pluggable parser layer, so every bank's
differently-formatted statement converts into one standard schema before
any analysis happens. Nothing downstream (cleaning, fraud detection,
graph analysis) ever touches raw files directly.

```
Raw file (PDF / markdown-table / scanned image)
        |
        v
   Parser plugin (format-specific: PdfParser, MarkdownTableParser, ...)
        |
        v
  Standard Transaction schema  <-- everything downstream uses ONLY this
        |
        v
  Cleaning & validation (duplicates, balance checks, reversed txns)
        |
        v
  Graph analysis (counterparty extraction, money flow, round-trip / cycle detection)
        |
        v
  Money Trail (FIFO tracing: "this credit -> these debits")
        |
        v
  FastAPI endpoints  -->  Case Ledger frontend (upload + 3 views)
```

## Project structure

```
bank-analysis/
  backend/
    app/
      models/
        transaction.py       # standard Transaction / ParseResult schema
      parsers/
        base.py               # BaseParser interface + COLUMN_SYNONYMS matcher
        pdf_parser.py          # text-based PDF statements (pdfplumber)
        markdown_parser.py     # markdown/text pipe-table statements
        registry.py            # dispatches uploaded file to the right parser
      services/
        cleaning.py            # duplicates, balance-chain check, reversed txns
        graph_analysis.py      # counterparty extraction, round-trip detection
        money_trail.py         # FIFO credit-to-debit tracing
      main.py                  # FastAPI app: /upload, /transactions, /analysis/*
    requirements.txt
  frontend/
    case_ledger.html           # single-file UI: Ledger / Flow Leads / Money Trail tabs
  .gitignore
  README.md
```

## What's built so far

### 1. Standard Transaction Schema
`app/models/transaction.py`

`Transaction`: one standardized row (date, narration, debit, credit,
balance, ref_no) plus provenance metadata (source file/row, extraction
confidence) and cleaning-stage flags (`is_duplicate`,
`is_reversed_transaction`, `reversal_confidence`, `balance_mismatch`).

`ParseResult`: what every parser returns - a list of transactions plus
warnings.

### 2. Parser Interface + Column Synonym Matching
`app/parsers/base.py`

- `BaseParser`: abstract interface (`can_parse()`, `extract()`) every
  format-specific parser implements.
- `COLUMN_SYNONYMS`: maps standard field names to the many labels banks
  use for them (e.g. "Withdrawal Amt" / "Debits" / "Dr" -> `debit`).
  Extended repeatedly as new bank formats were tested (e.g. "trans date",
  "transaction details", "cheque / instrument").
- `normalize_header()`: collapses embedded newlines/whitespace in headers
  (a real quirk found in PDF table extraction, e.g. `"TRANS\nDATE"`).

### 3. PDF Parser (text-based statements)
`app/parsers/pdf_parser.py`

Uses `pdfplumber` to extract tables page by page, auto-detects the header
row via the synonym matcher, converts rows into standardized
`Transaction`s. Strips trailing `Cr`/`Dr` balance suffixes (e.g.
`"45,000.00Cr"`) - a bug found and fixed after testing on a real
statement that used this format.

**Tested on:** a real 5-page, 72-transaction Bandhan Bank statement -
all transactions extracted correctly, closing balance matched the
statement summary exactly, zero warnings.

### 4. Markdown Table Parser
`app/parsers/markdown_parser.py`

Handles statements already in markdown pipe-table format. Header
detection scans up to 8 rows and picks the *best*-matching row (not just
the first partial match), since some banks split header cells across
multiple lines using `<br>` tags.

**Tested on:** two real IDFC First Bank statements (164 and 205
transactions) - correctly handled multi-page tables, `<br>`-split
headers, and narration text wrapped across cells.

### 5. Parser Registry
`app/parsers/registry.py`

Dispatches an uploaded file to the first parser that accepts it.
Currently registers `PdfParser` and `MarkdownTableParser`.

### 6. Data Cleaning & Validation
`app/services/cleaning.py`

- `detect_duplicates()`: flags transactions with identical
  date/amount/narration/ref_no.
- `validate_balance_chain()`: checks each row's balance logically
  follows from the previous balance + debit/credit.
- `detect_reversed_transactions()`: **two-tier** detection added after
  testing revealed pure amount-matching produced too many false
  positives:
  - `reversal_confidence = "confirmed"` - narration explicitly says
    failed/reversed/bounced (e.g. `"FAILED CR TXN"`) - high confidence,
    bank-stated fact.
  - `reversal_confidence = "possible"` - same amount debited then
    credited within a time window, no explicit keyword - a coincidence-
    level signal only, needs manual review.

**Tested on real data (205 transactions):** 0 balance mismatches,
11 duplicates, 45 reversed-flagged - of which only 3 were keyword-
confirmed and 42 were amount-coincidence only, confirming the two-tier
split was necessary.

### 7. Money Flow Graph & Round-Trip Detection
`app/services/graph_analysis.py`

- `extract_counterparty()`: best-effort regex extraction of the other
  party's name from narration text. Explicitly bank-specific and
  fragile by nature - handles multiple formats found so far (Bandhan
  hyphen-style, IDFC slash-style), explicitly excludes internal
  bulk-payment batch references (`BLKRTGS`/`BLKNEFT`/`BLKIFT`) and
  generic UPI app names from being mistaken for real counterparties
  (a real bug found and fixed during testing).
- `build_transaction_graph()`: builds a directed graph (NetworkX) -
  nodes are accounts/entities, edges are transactions.
- `detect_round_trips()`: finds cycles in the graph within a
  configurable time window - the core round-trip fraud pattern.
- `find_accumulation_accounts()`: ranks counterparties by net amount
  received - surfaces potential destination/mule accounts.

**Known limitation:** round-trip detection requires statements from
multiple linked accounts in the same investigation session to be
meaningful; a single account's statement can only show money leaving/
arriving, not whether it later returns through another account.

### 8. Money Trail Analysis (FIFO Tracing)
`app/services/money_trail.py`

Given a specific credit, walks forward through subsequent debits,
FIFO-consuming the credited amount until exhausted, to answer
"this money came in - where did it go?"

**Tested on real data:** a ₹14,50,000 RTGS credit was 100% traced same-day
to two large onward transfers - a real, actionable investigative lead
surfaced automatically. Also tested at scale: 75/75 credits (100%)
fully traced on a 205-transaction statement.

### 9. FastAPI Backend
`app/main.py`

- `POST /upload` - extract, clean, store; returns transactions +
  duplicate/mismatch/reversed counts + warnings.
- `GET /transactions/{session_id}` - fetch stored transactions.
- `GET /analysis/graph/{session_id}` - round-trips + accumulation leads.
- `GET /analysis/money-trail/{session_id}` - FIFO trace of every credit.

Session data currently stored in memory (swap for a real DB later).

### 10. Case Ledger Frontend
`frontend/case_ledger.html`

Single-file HTML/CSS/JS UI styled as an evidence ledger. Three tabs:
- **Ledger** - all transactions with ink-stamp flags (Duplicate,
  Reversed confirmed/possible, Balance break).
- **Flow Leads** - accumulation-account ranking from graph analysis.
- **Money Trail** - every credit expanded to show its FIFO-traced debits.

"+ Upload Statement" calls the FastAPI backend directly
(`http://127.0.0.1:8000`) - works fully offline/locally, no separate
frontend server needed.

## Not yet built

- CSV/Excel parser (Requirement 1 - still missing)
- OCR + LLM fallback parser (for scanned statements and PDFs where table
  structure is destroyed on extraction - confirmed necessary after
  testing one real statement that failed this way)
- LLM-based narration/counterparty extraction (would generalize better
  than hand-written regex per bank - regex approach is showing real
  scaling limits after 3 bank formats)
- PDF/Excel report generation & export (Requirement 6)
- Multi-file / multi-account investigation sessions (needed for real
  round-trip detection across linked accounts)

## Setup

```bash
cd backend
pip install -r requirements.txt --break-system-packages
uvicorn app.main:app --reload
```

Then open `frontend/case_ledger.html` directly in a browser (no server
needed for the frontend itself) and use "+ Upload Statement" to test
against the running backend at `http://127.0.0.1:8000`.

## Known limitations / honest notes

- Counterparty name extraction from narrations is regex-based per
  transaction type/bank and will need extending for formats not yet
  seen - this is the clearest case for eventually adding an LLM-based
  fallback rather than hand-writing more patterns.
- Round-trip detection needs statements from multiple related accounts
  uploaded into the same investigation session to be meaningful.
- "Possible" reversed-transaction flags are amount-coincidence matches
  only and are not confirmed fraud/failure - always review before
  treating as fact.
- Some PDF-to-text conversions destroy row/table structure entirely
  (confirmed with a real statement) - these will need coordinate-based
  extraction or an LLM fallback, not more rule-based parsing.

## Security note

Uploaded statements may contain real personal/financial data. Never
commit files from `backend/uploaded_files/` or any real statement files
to version control - see `.gitignore`.