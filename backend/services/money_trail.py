# app/services/money_trail.py
"""
Money Trail Analysis (FIFO Tracing)
--------------------------------------
Given a specific credit transaction, trace forward through the account's
subsequent debit transactions to show where that money was spent - using
FIFO: the oldest unspent credit is drawn down first by each debit,
until the traced credit amount is fully consumed.

This answers: "This ₹X came in on date D - where did it go?"
"""

from app.models.transaction import Transaction


def trace_money_trail(transactions: list[Transaction], target_credit_ref: str = None,
                       target_credit_index: int = None) -> dict:
    """
    Traces one specific credit forward through subsequent debits.

    Identify the target credit either by ref_no or by its position
    (index) in the date-sorted transaction list - ref_no is preferred
    since it's unambiguous, but not every bank narration includes one.
    """
    sorted_txns = sorted(transactions, key=lambda t: (t.date, t.source_row))

    target = None
    target_pos = None
    for i, t in enumerate(sorted_txns):
        if t.credit <= 0:
            continue
        if target_credit_ref and t.ref_no == target_credit_ref:
            target = t
            target_pos = i
            break
        if target_credit_index is not None and i == target_credit_index:
            target = t
            target_pos = i
            break

    if target is None:
        return {"error": "Target credit transaction not found."}

    remaining = target.credit
    trail = []

    # Walk forward through debits only, FIFO-consuming the credited amount
    for t in sorted_txns[target_pos + 1:]:
        if remaining <= 0.01:
            break
        if t.debit <= 0:
            continue

        consumed = min(t.debit, remaining)
        trail.append({
            "date": t.date,
            "narration": t.narration,
            "debit_amount": t.debit,
            "amount_from_this_credit": consumed,
            "ref_no": t.ref_no,
        })
        remaining -= consumed

    return {
        "source_credit": {
            "date": target.date,
            "narration": target.narration,
            "amount": target.credit,
            "ref_no": target.ref_no,
            "balance_before": target.balance - target.credit if target.balance else None,
        },
        "amount_traced": target.credit - remaining,
        "amount_untraced": round(remaining, 2),
        "fully_traced": remaining <= 0.01,
        "trail": trail,
    }


def trace_all_credits(transactions: list[Transaction]) -> list[dict]:
    """Runs trace_money_trail for every credit transaction in the account."""
    sorted_txns = sorted(transactions, key=lambda t: (t.date, t.source_row))
    results = []
    for i, t in enumerate(sorted_txns):
        if t.credit > 0:
            results.append(trace_money_trail(transactions, target_credit_index=i))
    return results