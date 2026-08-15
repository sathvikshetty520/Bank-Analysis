# app/services/cleaning.py
"""
Data Cleaning & Validation
-----------------------------
Takes a list of standardized Transactions and flags (not deletes -
investigators need to see everything) issues:
  1. Duplicate transactions
  2. Balance-chain breaks (debit/credit doesn't match balance movement)
  3. Reversed/failed transactions (same amount debited then credited back shortly after)
"""
from app.models.transaction import Transaction
from app.services.graph_analysis import extract_counterparty

REVERSAL_KEYWORDS = ["FAILED", "REVERSAL", "REVERSED", "BOUNCE", "RETURNED", "REV TXN", "REJECT"]
COUNTERPARTY_MATCH_THRESHOLD = 500

def detect_duplicates(transactions):
    seen = {}
    for t in transactions:
        key = (t.date, round(t.debit, 2), round(t.credit, 2), t.narration.strip().lower(), t.ref_no)
        if key in seen:
            t.is_duplicate = True
        else:
            seen[key] = t
    return transactions

def validate_balance_chain(transactions):
    sorted_txns = sorted(transactions, key=lambda t: (t.date, t.source_row))
    for i in range(1, len(sorted_txns)):
        prev, curr = sorted_txns[i - 1], sorted_txns[i]
        if prev.balance is None or curr.balance is None:
            continue
        expected = prev.balance - curr.debit + curr.credit
        if abs(expected - curr.balance) > 0.01:
            curr.balance_mismatch = True
    return sorted_txns

def _has_reversal_keyword(narration: str) -> bool:
    n = narration.upper()
    return any(kw in n for kw in REVERSAL_KEYWORDS)

def detect_reversed_transactions(transactions, window_days=3):
    sorted_txns = sorted(transactions, key=lambda t: t.date)
    require_counterparty_match = len(sorted_txns) > COUNTERPARTY_MATCH_THRESHOLD

    for t in sorted_txns:
        if _has_reversal_keyword(t.narration):
            t.is_reversed_transaction = True
            t.reversal_confidence = "confirmed"

    for i, t1 in enumerate(sorted_txns):
        if t1.debit <= 0 or t1.reversal_confidence == "confirmed":
            continue
        t1_counterparty = extract_counterparty(t1.narration) if require_counterparty_match else None
        for t2 in sorted_txns[i + 1:]:
            if (t2.date - t1.date).days > window_days:
                break
            if t2.reversal_confidence == "confirmed":
                continue
            if t2.credit > 0 and abs(t2.credit - t1.debit) < 0.01:
                if require_counterparty_match:
                    t2_counterparty = extract_counterparty(t2.narration)
                    if t1_counterparty != t2_counterparty:
                        continue
                t1.is_reversed_transaction = True
                t2.is_reversed_transaction = True
                if not t1.reversal_confidence:
                    t1.reversal_confidence = "possible"
                if not t2.reversal_confidence:
                    t2.reversal_confidence = "possible"

    return sorted_txns

def clean_transactions(transactions):
    transactions = detect_duplicates(transactions)
    transactions = validate_balance_chain(transactions)
    transactions = detect_reversed_transactions(transactions)
    return transactions