# Automated Bank Statement Analysis System

A tool to extract, clean, and analyze bank statements to help investigators
detect suspicious transactions, trace fund movement, and identify hidden
relationships between accounts.

## Status: In Progress

Working end-to-end pipeline (upload -> parse -> clean -> categorize ->
analyze -> report) tested against real bank statements from 5+ different
formats/banks (Bandhan, IDFC, YES Bank, SBI, and others), including a
real 555-page, 11,038-transaction statement, genuine cross-account
round-trip detection across two real uploaded statements, transaction
categorization (22 rule-based patterns), and an interactive money-flow
network graph.

## Architecture

Built as a pipeline with a pluggable parser layer, so every bank's
differently-formatted statement converts into one standard schema
before any analysis happens. Nothing downstream (cleaning,
categorization, fraud detection, graph analysis) ever touches raw
files directly.

```
Raw file (PDF / markdown-table / scanned image / CSV / Excel)
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
  Cleaning & validation (duplicates, balance checks, two-tier reversed
  txns, excludes recurring fee/tax categories from duplicate flagging)
        |
        v
  Categorization (22 rule-based narration patterns: UPI, NEFT, RTGS,
  IMPS, bank charges, GST, salary, interest, court orders, etc.)
        |
        v
  Multi-account session (SESSION_STORE: {session_id: {account_id: [txns]}})
  - multiple statements can be uploaded into ONE investigation
        |
        v
  Graph analysis (counterparty extraction, cross-account linking via
  real account numbers found in narrations, round-trip / cycle
  detection with amount-consistency filtering, accumulation leads)
        |
        v
  Money Trail (FIFO tracing: "this credit -> these debits")
        |
        v
  FastAPI endpoints  -->  Case Ledger frontend (5 tabs + D3 network graph)
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
        transaction.py         # standard Transaction / ParseResult schema
      parsers/
        base.py                 # BaseParser interface + COLUMN_SYNONYMS matcher
        pdf_parser.py            # text-based PDF statements with bordered tables (pdfplumber)
        pdf_text_line_parser.py  # fallback: text-based PDFs with NO table borders (date-anchored regex)
        markdown_parser.py       # markdown/text pipe-table statements
        csv_excel_parser.py      # CSV/Excel exports, tolerant of ragged junk rows
        registry.py              # dispatches to first parser that returns >0 transactions
      services/
        cleaning.py              # duplicates, balance-chain check, two-tier reversed txns
        categorization.py        # 22-pattern rule-based transaction categorizer
        graph_analysis.py        # counterparty extraction, single + multi-account round-trip detection
        money_trail.py           # FIFO credit-to-debit tracing
        report.py                # Excel workbook + PDF summary report generation
      main.py                    # FastAPI app: /upload, /transactions, /analysis/*, /report/*
    requirements.txt
  frontend/
    case_ledger.html             # single-file UI: Ledger / Flow Leads / Money Trail / Categories / Money Flow tabs
  .gitignore
  README.md
```

## What's built so far

### 1. Standard Transaction Schema
`app/models/transaction.py`

`Transaction`: one standardized row (date, narration, debit, credit,
balance, ref_no) plus provenance metadata (source file/row, extraction
confidence), cleaning-stage flags (`is_duplicate`,
`is_reversed_transaction`, `reversal_confidence`, `balance_mismatch`),
and a `category` field assigned during categorization.

`ParseResult`: what every parser returns - a list of transactions plus
warnings.

### 2. Parser Interface + Column Synonym Matching
`app/parsers/base.py`

- `BaseParser`: abstract interface (`can_parse()`, `extract()`) every
  format-specific parser implements.
- `COLUMN_SYNONYMS`: maps standard field names to the many labels banks
  use for them. Extended repeatedly as new bank formats were tested
  (e.g. "trans date", "txn dt", "transaction details", "cheque /
  instrument", "ref txn no").
- `normalize_header()`: collapses embedded newlines/whitespace in
  headers (a real quirk found in PDF table extraction).

### 3. PDF Parser (text-based statements)
`app/parsers/pdf_parser.py`

Uses `pdfplumber` to extract tables page by page, auto-detects the
header row via the synonym matcher. Strips trailing `Cr`/`Dr` balance
suffixes - a bug found and fixed after testing on a real statement
that used this format.

**Tested on:** a real 5-page, 72-transaction Bandhan Bank statement -
closing balance matched exactly, zero warnings.

### 4. Markdown Table Parser
`app/parsers/markdown_parser.py`

Handles statements already in markdown pipe-table format. Header
detection picks the *best*-matching row (not just the first partial
match), since some banks split header cells across lines using `<br>`
tags. Date parsing normalizes `-` followed by whitespace (e.g.
"02-MAY- 2025") back to a clean hyphen before parsing - a real bug
found where `<br>` tags inside a date cell (e.g. "02-MAY-<br>2025")
got converted to a stray space, silently causing **every single
transaction in the file to fail date parsing and be dropped** (0
transactions extracted, no visible error). This was found by
double-checking a suspicious "0 possible reversed" result rather than
trusting it.

**Tested on:** multiple real IDFC and Bandhan statements (up to 244
transactions in one file after the date fix).

### 4a. PDF Text-Line Parser (fallback for borderless statements)
`app/parsers/pdf_text_line_parser.py`

Some bank PDFs have no visible table borders/lines at all, so
`pdfplumber`'s `extract_tables()` finds nothing even though the data is
present as clean, well-aligned text. This parser reads raw page text
instead and uses a date-anchored regex to detect where each
transaction starts, treating non-date-starting lines as continuations
of the previous transaction's (often multi-line UPI) narration.

**Tested on:** a real 555-page, 11,038-transaction YES Bank statement
with no table borders at all - 0 balance-chain mismatches across the
entire statement.

### 4b. CSV / Excel Parser
`app/parsers/csv_excel_parser.py`

Reads CSV rows manually via Python's `csv` module rather than pandas'
C parser, since real bank exports often have "ragged" junk rows at the
top that crash pandas' fast parser outright - found via a real SBI
CASA statement export. Converts a negative debit into the equivalent
credit rather than discarding its sign via `abs()` - some banks
represent a reversal as a negative debit rather than a separate credit
entry; blindly abs()-ing this destroyed the reversal signal and broke
balance-chain validation by exactly 2x the reversed amount.

**Tested on:** a real SBI CASA statement (555 transactions spanning
2019-2025). Found and fixed two real bugs: missing "txn dt" date
synonym, and the negative-debit-as-reversal issue. After fixes: 0
duplicates, 0 balance mismatches (down from 61 and 8).

### 5. Parser Registry
`app/parsers/registry.py`

Dispatches an uploaded file to the first parser that returns at least
one transaction (not just the first parser that doesn't raise an
exception) - lets `PdfTextLineParser` act as a genuine fallback for
`PdfParser`, since both accept any `.pdf` file at the `can_parse()`
check.

### 6. Data Cleaning & Validation
`app/services/cleaning.py`

- `detect_duplicates()`: flags transactions with identical
  date/amount/narration/ref_no - **except** for categories where
  identical recurring charges are normal and expected (GST, flat
  transfer fees). Found via real data: a business account with many
  same-day transfers showed 32-37% of transactions flagged as
  duplicates, all of which were legitimate recurring GST/NEFT/RTGS/IMPS
  "outward payment" fees with no unique per-row reference number
  available in the export at all. Fixed by excluding known fee/tax
  categories from duplicate detection, informed by the categorization
  module (see below).
- `validate_balance_chain()`: checks each row's balance logically
  follows from the previous balance + debit/credit.
- `detect_reversed_transactions()`: **two-tier** detection:
  - `"confirmed"` - narration explicitly says failed/reversed/bounced.
  - `"possible"` - same amount debited then credited within a window,
    no explicit keyword. On statements over 500 transactions,
    additionally requires the counterparty to match (via
    `extract_counterparty()`), and requires the match to be non-empty
    on both sides (two real bugs found: "blank matches blank" false
    positives, and common-name collisions like two different people
    both named "Muhammed" being treated as the same counterparty -
    the latter deliberately left as a documented limitation rather
    than "fixed" with a stricter rule that risks missing genuine
    multi-app same-person transfers).

**Verified on real data at multiple scales:** 0 duplicates, 0 balance
mismatches on a 205-transaction IDFC statement; on an 11,038-
transaction statement, "possible" reversed dropped from 2,594 (pure
amount-coincidence noise) to 18 after adding counterparty matching,
then further after fixing the blank-match bug. On a 941-transaction
statement, an independent brute-force check confirmed "0 possible
reversed" was correct (80 raw amount-coincidences existed, all
between clearly unrelated real people, correctly filtered out).

### 7. Transaction Categorization
`app/services/categorization.py`

Rule-based classifier with 22 ordered regex patterns (first match
wins), covering UPI/NEFT/RTGS/IMPS transfers, bank charges and GST,
salary and interest credit, pension contributions, cash withdrawal/
deposit, cheque deposits, POS purchases, bulk payment batches, mobile
banking transfers, court-ordered recovery, and more. Patterns are
drawn directly from real narrations seen across every bank statement
tested this session, not generic guesses.

**Tested on real data:** 204/205 (99.5%) of transactions on a real
IDFC statement categorized correctly on first pass after fixing two
bugs found via testing (opening balance "B/F" wrongly matched cheque
deposit; plain "Cash Withdrawal" wasn't caught). The one remaining
uncategorized row is a genuinely unrecoverable case where the
identifying prefix landed in a different markdown table row due to a
text-wrapping artifact.

`category_summary()` aggregates count/debit/credit totals per
category - used by both the API (`/analysis/categories/{session_id}`)
and the Excel report's Category Breakdown sheet.

### 8. Money Flow Graph & Round-Trip Detection
`app/services/graph_analysis.py`

- `extract_counterparty()`: best-effort regex extraction of the other
  party's name from narration text. Explicitly bank-specific and
  fragile by nature. Excludes internal bulk-payment batch references
  and generic UPI app names from being mistaken for real
  counterparties.
- `NON_ENTITY_PLACEHOLDERS`: a set of labels (`INTERNAL_BULK_PAYMENT_
  BATCH`, `UNKNOWN_UPI_COUNTERPARTY`, `CASH_WITHDRAWAL`) that do NOT
  represent a single real entity, excluded from round-trip and
  accumulation detection. Found via a real false-positive round trip:
  every bulk payment to a completely different real recipient was
  being lumped into one fake node, making two unrelated bulk payments
  look like a "round trip" through a shared bucket.
- `build_transaction_graph()`: single-account graph.
- `build_multi_account_graph()`: graph spanning MULTIPLE uploaded
  accounts in one investigation session. When a transaction's
  narration contains another uploaded account's real account number,
  the edge links to the REAL account node instead of a
  narration-guessed name, with a `confirmed_link` flag distinguishing
  verified links from inferred ones.
- `detect_round_trips()`: finds cycles within a time window, now with
  an **amount-consistency check** (`MIN_AMOUNT_CONSISTENCY_RATIO =
  0.5`) - the smallest leg of a cycle must be at least half the
  largest leg. Found via a real false positive: a ₹700 debit and a
  completely unrelated ₹1 credit three days later, to a node with the
  same extracted label, was being flagged as a "round trip" purely
  because edges existed in both directions - amount size was never
  checked before this fix.
- `find_accumulation_accounts()`: ranks counterparties by net amount
  received, excluding placeholder buckets.

**Tested and confirmed working on real data:** uploaded two related
real bank statements into one session and detected 2 genuine
round-trip patterns between the real accounts (later refined to 1 after
excluding a bulk-payment-bucket false positive, then further verified
after the amount-consistency fix removed an unrelated ₹700/₹1
mismatch). Also verified with a synthetic 3-hop A->B->C->A cycle
across 3 separate simulated uploads via the real API.

### 9. Money Trail Analysis (FIFO Tracing)
`app/services/money_trail.py`

Given a specific credit, walks forward through subsequent debits,
FIFO-consuming the credited amount until exhausted, to answer "this
money came in - where did it go?"

**Tested on real data:** a ₹14,50,000 RTGS credit was 100% traced
same-day to two large onward transfers. Also tested at scale: 75/75
credits (100%) fully traced on a 205-transaction statement.

### 10. FastAPI Backend
`app/main.py`

- `POST /upload` - extract, clean, categorize, store. Accepts an
  optional `session_id` - if provided and it exists, this file's
  transactions are ADDED to that session under its own `account_id`,
  enabling multi-account investigations.
- `GET /transactions/{session_id}` - all transactions across every
  account in the session.
- `GET /analysis/graph/{session_id}` - round-trips, accumulation
  leads, and full node/edge lists for visualization.
- `GET /analysis/money-trail/{session_id}` - FIFO trace of every credit.
- `GET /analysis/categories/{session_id}` - category breakdown.
- `GET /report/excel/{session_id}` - downloads a full Excel workbook.
- `GET /report/pdf/{session_id}` - downloads a summary PDF report.

`SESSION_STORE` is `{session_id: {account_id: [transactions]}}` to
support multiple accounts per investigation. Still in-memory - any
server restart wipes all uploaded data.

### 11. Case Ledger Frontend
`frontend/case_ledger.html`

Single-file HTML/CSS/JS UI styled as an evidence ledger. Five tabs:
- **Ledger** - all transactions with category column and ink-stamp
  flags (Duplicate, Reversed confirmed/possible, Balance break).
- **Flow Leads** - round-trip patterns (account chain, per-hop
  amounts, confirmed-link status) displayed persistently as cards,
  plus the accumulation-account ranking.
- **Money Trail** - every credit expanded to show its FIFO-traced debits.
- **Categories** - per-category transaction count and debit/credit totals.
- **Money Flow** - interactive D3.js force-directed network graph:
  uploaded accounts as larger dark nodes, counterparties as smaller
  outlined nodes, round-trip-involved nodes highlighted in red, edge
  thickness scaled by transaction amount (capped to avoid clutter),
  draggable nodes, hover tooltips.

"+ Add Statement to Case" supports uploading multiple statements into
one investigation, prompting for each statement's real account ID.
"Download Excel" / "Download PDF Report" buttons enable once a session
exists.

### 12. Report Generation (Excel + PDF)
`app/services/report.py`

- `generate_excel_report()`: 6-sheet workbook (Summary, full
  Transaction Ledger, Round Trips, Accumulation Leads, Category
  Breakdown, Money Trail). Debit/credit totals use real `=SUM()`
  formulas, verified with the mandatory recalc check (0 formula
  errors) and cross-checked against Python's independently-computed
  sums (exact match).
- `generate_pdf_report()`: human-readable summary (key stats,
  round-trip chains, top accumulation leads, capped flagged-
  transactions table). Table cells wrapped in ReportLab `Paragraph`
  objects rather than plain strings - plain strings don't wrap and
  overflow into neighboring cells, a real rendering bug found by
  rendering the PDF to an image and visually inspecting it.

## Not yet built

- OCR + LLM fallback parser (for scanned statements and PDFs where
  table structure is destroyed on extraction)
- LLM-based narration/counterparty extraction (would generalize better
  than hand-written regex per bank - regex approach keeps needing new
  patterns per bank format, and is the deliberately unresolved cause
  of the common-name false-match limitation)
- Structuring/smurfing, velocity anomaly, and round-amount-clustering
  fraud detectors (from external comparison against a hackathon
  report's spec - different detector types than what's built)

## Setup

```bash
cd backend
pip install -r requirements.txt --break-system-packages
uvicorn app.main:app --reload
```

Open `frontend/case_ledger.html` directly in a browser (double-click
it - do NOT use VS Code Live Server or similar dev-server extensions,
which was found to cause confusing multi-tab/stale-session bugs during
testing) and use "+ Add Statement to Case" to test against the running
backend at `http://127.0.0.1:8000`.

## Known limitations / honest notes

- Counterparty name extraction from narrations is regex-based per
  transaction type/bank and will need extending for formats not yet
  seen - the clearest case for eventually adding an LLM-based
  fallback rather than hand-writing more patterns.
- Common-name false matches: two different real people sharing a
  common name (e.g. "Muhammed") can be treated as the same
  counterparty for possible-reversal matching. Deliberately not fixed
  further since a stricter match risks missing genuine same-person
  transfers across different UPI apps/handles.
- Round-trip detection involving a narration-text fallback node (not a
  real account number match) should be treated as "needs manual
  review," not a confirmed finding - e.g. a court-order-recovery
  narration appearing as both a debit and credit target got flagged as
  a round trip; this is a real limitation of using raw narration text
  as a graph node when extraction can't find a real name.
- "Possible" reversed-transaction flags are heuristic signals only and
  are not confirmed fraud/failure - always review before treating as fact.
- Some PDF-to-text conversions destroy row/table structure entirely -
  these need coordinate-based extraction or an LLM fallback, not more
  rule-based parsing.
- Session data is stored in memory - any server restart wipes all
  uploaded data. Fine for local dev, would need a real database before
  this could be a deployed tool.
- Date parsing is sensitive to how PDF-to-markdown/text conversion
  tools handle wrapped multi-line cells (e.g. `<br>` tags before a
  year) - found to silently produce 0 extracted transactions in one
  real case before being fixed; worth spot-checking transaction counts
  against the source document's apparent size whenever a new
  conversion tool or format is used.

## Security note

Uploaded statements may contain real personal/financial data. Never
commit files from `backend/uploaded_files/` or any real statement
files to version control - see `.gitignore`.