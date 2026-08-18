# Automated Bank Statement Analysis System

A tool to extract, clean, and analyze bank statements to help investigators
detect suspicious transactions, trace fund movement, and identify hidden
relationships between accounts.

## Status: In Progress

Working end-to-end pipeline (upload -> parse -> clean -> analyze -> report)
tested against real bank statements from 4 different formats/banks,
including a real 555-page, 11,038-transaction statement, and a real
genuine cross-account round-trip detected across two uploaded statements.

## Architecture

Built as a pipeline with a pluggable parser layer, so every bank's
differently-formatted statement converts into one standard schema before
any analysis happens. Nothing downstream (cleaning, fraud detection,
graph analysis) ever touches raw files directly.

```
Raw file (PDF / markdown-table / scanned image)
        |
        v
   Parser plugin (format-specific: PdfParser, MarkdownTableParser,
                  PdfTextLineParser, CsvExcelParser) - tried in order,
                  falls through to the next parser if one returns zero
                  transactions (not just on exceptions)
        |
        v
  Standard Transaction schema  <-- everything downstream uses ONLY this
        |
        v
  Cleaning & validation (duplicates, balance checks, two-tier reversed txns)
        |
        v
  Multi-account session (SESSION_STORE: {session_id: {account_id: [txns]}})
  - multiple statements can be uploaded into ONE investigation
        |
        v
  Graph analysis (counterparty extraction, cross-account linking via
  real account numbers found in narrations, round-trip / cycle detection)
        |
        v
  Money Trail (FIFO tracing: "this credit -> these debits")
        |
        v
  FastAPI endpoints  -->  Case Ledger frontend (upload + 3 views)
        |
        v
  Report export (Excel workbook + PDF summary report)
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
        pdf_parser.py          # text-based PDF statements with bordered tables (pdfplumber)
        pdf_text_line_parser.py # fallback: text-based PDFs with NO table borders (date-anchored regex)
        markdown_parser.py     # markdown/text pipe-table statements
        csv_excel_parser.py    # CSV/Excel exports, tolerant of ragged junk rows
        registry.py            # dispatches to first parser that returns >0 transactions
      services/
        cleaning.py            # duplicates, balance-chain check, two-tier reversed txns
        graph_analysis.py      # counterparty extraction, single + multi-account round-trip detection
        money_trail.py         # FIFO credit-to-debit tracing
        report.py              # Excel workbook + PDF summary report generation
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

### 4a. PDF Text-Line Parser (fallback for borderless statements)
`app/parsers/pdf_text_line_parser.py`

Some bank PDFs have no visible table borders/lines at all, so
`pdfplumber`'s `extract_tables()` finds nothing even though the data is
present as clean, well-aligned text. This parser reads raw page text
instead and uses a date-anchored regex (`LINE_PATTERN`) to detect where
each transaction starts, treating any non-date-starting line as a
continuation of the previous transaction's (often multi-line UPI)
narration.

Registered *after* `PdfParser` in the registry and only reached because
the registry now treats a zero-transaction result as a "try the next
parser" case, not just exceptions - this required a small registry
logic change (see below).

**Tested on:** a real 555-page, 11,038-transaction YES Bank statement
with no table borders at all - `PdfParser` correctly found 0
transactions on every page (proving the fallback logic works), and
`PdfTextLineParser` then extracted all 11,038 correctly, with **0
balance-chain mismatches across the entire statement** - strong
validation that date/amount extraction held up at real scale, not
just on small test files.

### 4b. CSV / Excel Parser
`app/parsers/csv_excel_parser.py`

Handles .csv/.xls/.xlsx exports. Reads CSV rows manually via Python's
`csv` module rather than pandas' C parser, since real bank exports
often have "ragged" junk rows at the top (inconsistent column counts
per line) that crash pandas' fast parser outright - found via a real
SBI CASA statement export. Header detection picks the best-matching
row (not first-match), same lesson as the markdown parser. Strips
trailing Cr/Dr balance suffixes, and converts a negative debit into
the equivalent credit rather than discarding its sign via `abs()` -
some banks (confirmed on the same SBI statement) represent a reversal
as a negative debit rather than a separate credit entry; blindly
abs()-ing this destroyed the reversal signal and broke balance-chain
validation by exactly 2x the reversed amount.

**Tested on:** a real SBI CASA statement (555 transactions spanning
2019-2025). Found and fixed two real bugs via this file: missing
"txn dt" date-column synonym, and the negative-debit-as-reversal issue
above. After fixes: 0 duplicates, 0 balance mismatches on the full
statement (down from 61 and 8 respectively before the fixes) - both
confirmed as false positives caused by the two bugs, not real data
issues, by re-testing against the exact real reconstructed row
sequences from the source file.


`app/parsers/registry.py`

Dispatches an uploaded file to the first parser that returns at least
one transaction (not just the first parser that doesn't raise an
exception) - this "try next on empty result" behavior is what lets
`PdfTextLineParser` act as a genuine fallback for `PdfParser`, since
both accept any `.pdf` file at the `can_parse()` check.

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
  - **Volume-based tightening:** on statements over 500 transactions,
    "possible" matches additionally require the counterparty (via
    `extract_counterparty()`) to match, not just the amount. Pure
    amount-matching alone doesn't scale - confirmed on the real
    11,038-transaction statement, where it initially flagged 2,594
    transactions (23.5%) as "possible", almost entirely coincidental
    small UPI amounts recurring by chance. Adding the counterparty
    check is expected to cut this dramatically while still catching
    genuine reversal pairs (verified against a synthetic 650-transaction
    test case with known genuine vs. coincidental pairs).

**Tested on real data (205 transactions):** 0 balance mismatches,
11 duplicates, 45 reversed-flagged - of which only 3 were keyword-
confirmed and 42 were amount-coincidence only, confirming the two-tier
split was necessary.

**Tested on real data (11,038 transactions):** 0 balance mismatches,
0 duplicates, 3 keyword-confirmed reversals - the "possible" tier
required the counterparty-matching fix above to stay useful at this
scale.

### 7. Money Flow Graph & Round-Trip Detection
`app/services/graph_analysis.py`

- `extract_counterparty()`: best-effort regex extraction of the other
  party's name from narration text. Explicitly bank-specific and
  fragile by nature - handles multiple formats found so far (Bandhan
  hyphen-style, IDFC slash-style), explicitly excludes internal
  bulk-payment batch references (`BLKRTGS`/`BLKNEFT`/`BLKIFT`) and
  generic UPI app names from being mistaken for real counterparties
  (a real bug found and fixed during testing). Known remaining
  limitation: common first names (e.g. "Muhammed") can cause two
  different real people to be treated as the same counterparty -
  found via manual review, deliberately not "fixed" further since a
  stricter match risks missing genuine same-person transfers across
  different UPI apps/handles (see Known Limitations below).
- `build_transaction_graph()`: builds a directed graph (NetworkX) for
  a SINGLE account - nodes are that account plus narration-derived
  counterparties, edges are transactions.
- `build_multi_account_graph()`: builds a graph spanning MULTIPLE
  uploaded accounts (one investigation session can now hold several
  statements). Critically, when a transaction's narration contains
  another uploaded account's real account number, that edge links to
  the REAL account node instead of a narration-guessed name - this
  turns round-trip detection from a guess into something based on a
  confirmed match. Each edge carries a `confirmed_link` flag so the
  report/UI can distinguish verified links from inferred ones.
- `detect_round_trips()`: finds cycles in the graph within a
  configurable time window - the core round-trip fraud pattern. Now
  genuinely capable of finding multi-hop cycles (A -> B -> C -> A)
  that are structurally invisible from any single statement.
- `find_accumulation_accounts()`: ranks counterparties by net amount
  received - surfaces potential destination/mule accounts.

**Tested and confirmed working on real data:** uploaded two related
real bank statements into one investigation session (via the new
multi-file session support in `main.py`) and the system detected 2
genuine round-trip patterns between the real accounts - not a
synthetic test, an actual finding on real data. Also verified with a
synthetic 3-hop A->B->C->A cycle across 3 separate simulated uploads
to confirm the cross-statement linking logic works end-to-end via the
real API (not just the isolated function).

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

- `POST /upload` - extract, clean, store. Accepts an optional
  `session_id` - if provided and it exists, this file's transactions
  are ADDED to that session under its own `account_id`, enabling
  multi-account investigations, rather than starting a new session
  each time.
- `GET /transactions/{session_id}` - fetch all transactions across
  every account in the session.
- `GET /analysis/graph/{session_id}` - round-trips (now genuinely
  cross-account) + accumulation leads, computed via
  `build_multi_account_graph()` over every account in the session.
- `GET /analysis/money-trail/{session_id}` - FIFO trace of every
  credit across all accounts in the session.
- `GET /report/excel/{session_id}` - downloads a full Excel workbook.
- `GET /report/pdf/{session_id}` - downloads a summary PDF report.

`SESSION_STORE` is now `{session_id: {account_id: [transactions]}}`
(previously a flat list per session) to support multiple accounts per
investigation. Still in-memory (swap for a real DB later) - confirmed
during testing that any server restart (including `--reload` picking
up a saved file mid-session) wipes all uploaded data.

### 10. Case Ledger Frontend
`frontend/case_ledger.html`

Single-file HTML/CSS/JS UI styled as an evidence ledger. Three tabs:
- **Ledger** - all transactions with ink-stamp flags (Duplicate,
  Reversed confirmed/possible, Balance break).
- **Flow Leads** - round-trip patterns (account chain, per-hop amounts,
  confirmed-link status) displayed persistently as cards, plus the
  accumulation-account ranking from graph analysis.
- **Money Trail** - every credit expanded to show its FIFO-traced debits.

"+ Add Statement to Case" supports uploading multiple statements into
one investigation: each upload prompts for that statement's real
account ID, and subsequent uploads are appended to the same session
(tracked client-side via `CURRENT_SESSION_ID`) rather than replacing
it. "Download Excel" / "Download PDF Report" buttons enable once a
session exists and open the corresponding report endpoint.

"+ Upload Statement" calls the FastAPI backend directly
(`http://127.0.0.1:8000`) - works fully offline/locally, no separate
frontend server needed.

### 11. Report Generation (Excel + PDF)
`app/services/report.py`

Requirement 6 - the investigation deliverable.

- `generate_excel_report()`: 5-sheet workbook (Summary, full
  Transaction Ledger with flag columns, Round Trips, Accumulation
  Leads, Money Trail). Debit/credit totals use real `=SUM()` formulas,
  not hardcoded Python totals - verified with the mandatory recalc
  check (0 formula errors) and cross-checked against Python's
  independently-computed sums (exact match: Rs. 15,083,920 both ways
  on the real 205-transaction test file).
- `generate_pdf_report()`: 4-page human-readable summary (not the full
  ledger - that's what Excel is for): key stats, round-trip chains
  with confirmed-link status, top accumulation leads, and a capped
  table of flagged transactions. Table cells are wrapped in ReportLab
  `Paragraph` objects rather than plain strings - plain strings don't
  wrap and overflow into neighboring cells (a real rendering bug found
  and fixed by rendering the PDF to an image and visually inspecting
  it, not just trusting the code ran without errors).

**Tested on real data:** generated and verified both reports against
the real 205-transaction IDFC statement, and against a synthetic
3-account round-trip case to confirm the round-trip table renders
correctly with real cross-account data.

## Not yet built

- OCR + LLM fallback parser (for scanned statements and PDFs where table
  structure is destroyed on extraction - confirmed necessary after
  testing one real statement that failed this way)
- LLM-based narration/counterparty extraction (would generalize better
  than hand-written regex per bank - regex approach is showing real
  scaling limits after 4+ bank formats, and is now the deliberately
  unresolved cause of the common-name false-match limitation below)

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
  fallback rather than hand-writing more patterns. It's also now a
  dependency of reversal detection on large statements, so extraction
  quality there affects cleaning accuracy too.
- **Common-name false matches in counterparty comparison** (found via
  real data): `extract_counterparty()` for UPI narrations returns only
  the bare first name (e.g. "MUHAMMED"), not the UPI handle. Two
  different real people sharing a common name (very common with names
  like "Muhammed"/"Mohammed" in this dataset) can be incorrectly
  treated as the "same counterparty" for possible-reversal matching,
  even though their UPI handles and bank codes clearly differ (e.g.
  `**shrey@okhdfcbank` vs `**321-2@okhdfcbank`). A stricter fix (also
  requiring the UPI handle to match) was considered and deliberately
  NOT implemented, because it risks the opposite failure: missing
  genuine reversals where the same person legitimately uses two
  different UPI apps/handles (plausibly seen in the same dataset with
  a different counterparty pair, "AMANU RAH", using `**09017@ybl` on
  one side and `**prade@okhdfcbank` on the other). This is a real,
  unresolved precision/recall tradeoff - "possible" reversal flags on
  large statements should be manually reviewed with this in mind,
  especially for common names.
- Round-trip detection needs statements from multiple related accounts
  uploaded into the same investigation session to be meaningful.
- "Possible" reversed-transaction flags are amount-coincidence matches
  only (or amount+counterparty on large statements) and are not
  confirmed fraud/failure - always review before treating as fact.
- Some PDF-to-text conversions destroy row/table structure entirely
  (confirmed with a real statement) - these will need coordinate-based
  extraction or an LLM fallback, not more rule-based parsing. This is
  different from the "no table borders but text is well-formatted"
  case, which `PdfTextLineParser` now handles.
- Session data is stored in memory (`SESSION_STORE` dict in `main.py`)
  - any server restart (including `uvicorn --reload` triggered by
    saving a file mid-session) wipes all uploaded data. Confirmed as a
    real issue during testing - fine for local dev, would need a real
    database before this could be a deployed tool.
- `PdfTextLineParser`'s date-anchored regex (`LINE_PATTERN`) is tuned to
  one date format (`DD-MON-YYYY`) seen so far - other date formats in
  borderless PDFs will need the pattern extended, similar to how
  `COLUMN_SYNONYMS` gets extended for table-based formats.

## Security note

Uploaded statements may contain real personal/financial data. Never
commit files from `backend/uploaded_files/` or any real statement files
to version control - see `.gitignore`.