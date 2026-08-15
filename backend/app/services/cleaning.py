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


def detect_duplicates(transactions: list[Transaction]) -> list[Transaction]:
    seen = {}
    for t in transactions:
        key = (t.date, round(t.debit, 2), round(t.credit, 2), t.narration.strip().lower(), t.ref_no)
        if key in seen:
            t.is_duplicate = True
        else:
            seen[key] = t
    return transactions


def validate_balance_chain(transactions: list[Transaction]) -> list[Transaction]:
    """
    Sort by date (statement order), then check each balance follows
    logically from the previous balance + this row's debit/credit.
    """
    sorted_txns = sorted(transactions, key=lambda t: (t.date, t.source_row))
    for i in range(1, len(sorted_txns)):
        prev, curr = sorted_txns[i - 1], sorted_txns[i]
        if prev.balance is None or curr.balance is None:
            continue
        expected = prev.balance - curr.debit + curr.credit
        if abs(expected - curr.balance) > 0.01:  # allow rounding tolerance
            curr.balance_mismatch = True
    return sorted_txns


def detect_reversed_transactions(transactions: list[Transaction], window_days: int = 3) -> list[Transaction]:
    """
    Flags pairs where the same amount is debited then credited back
    (or vice versa) within a short window - classic failed-transaction pattern.
    """
    sorted_txns = sorted(transactions, key=lambda t: t.date)
    for i, t1 in enumerate(sorted_txns):
        if t1.debit <= 0:
            continue
        for t2 in sorted_txns[i + 1:]:
            if (t2.date - t1.date).days > window_days:
                break
            if t2.credit > 0 and abs(t2.credit - t1.debit) < 0.01:
                t1.is_reversed_transaction = True
                t2.is_reversed_transaction = True
    return sorted_txns


def clean_transactions(transactions: list[Transaction]) -> list[Transaction]:
    transactions = detect_duplicates(transactions)
    transactions = validate_balance_chain(transactions)
    transactions = detect_reversed_transactions(transactions)
    return transactions