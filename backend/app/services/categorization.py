import re
from app.models.transaction import Transaction

CATEGORY_RULES = [
    ("OPENING_BALANCE", r"^B/F\b|BROUGHT FORWARD|OPENING BALANCE"),
    ("LEGAL_COURT_ORDER", r"COURT ORDER|CRL\.M\.P|GARNISHEE|ATTACHMENT ORDER"),
    ("BANK_CHARGES_FEES", r"SMS (ALERT )?CHARGES|SERVICE CHARGE|ANNUAL CHARGES|SLABWISE|DEBIT CARD ANNUAL|STOP CHEQUE"),
    ("ATM_INSUFFICIENT_FUNDS_FEE", r"ATM INSUFFICIENT FUND"),
    ("ATM_TRANSACTION_FEE", r"ATM\s*/\s*IMPS TRANSACTION CHARGE"),
    ("REVERSAL_FAILED_TXN", r"\bREV\b|REVERSAL|REVERSED|FAILED CR TXN|BOUNCE|RETURNED|CHQ DEPOSIT BOUNCE|REJECT"),
    ("INTEREST_CREDIT", r"\bSBINT\b|INTEREST CREDIT|INTEREST FOR THE PERIOD"),
    ("SALARY_CREDIT", r"\bSALARY\b|\bSAL\s*CREDIT\b"),
    ("PENSION_CONTRIBUTION", r"\bAPY\b|ATAL PENSION|PENSION"),
    ("CHEQUE_DEPOSIT", r"CHQ DEP|CHEQUE DEPOSIT"),
    ("CASH_WITHDRAWAL", r"\bATW\b|ATM CASH|ATM WITHDRAWAL|ATM-NFS|ATM Cash|CASH WITHDRAWAL"),
    ("CASH_DEPOSIT", r"CASH DEPOSIT"),
    ("POS_PURCHASE", r"\bPOS\b.*PURCHASE|POS\. Normal Purchase"),
    ("BULK_PAYMENT_BATCH", r"BLKRTGS|BLKNEFT|BLKIFT"),
    ("INTERNAL_FUND_TRANSFER", r"\bIFT/"),
    ("UPI_TRANSFER", r"^UPI/|UPI/CR/|UPI/DR/|UPI/MOB/"),
    ("IMPS_TRANSFER", r"^IMPS[-/]|IMPS-OPW|IMPS-OPM"),
    ("NEFT_TRANSFER", r"NEFT[/-]|NEFT DR|NEFT CR|NEFT Cr"),
    ("RTGS_TRANSFER", r"RTGS[/-]|RTGS DR|RTGS CR"),
    ("MOBILE_BANKING_TRANSFER", r"^MB/|MOB-IMPS|INET-IMPS"),
    ("ATM_CARD_TRANSFER", r"ATM TRANSFER"),
    ("GST_TAX", r"\bGST\b"),
    ("KERALA_FLOOD_CESS", r"FLOOD CESS"),
]

COMPILED_RULES = [(label, re.compile(pattern)) for label, pattern in CATEGORY_RULES]


def categorize_transaction(narration: str) -> str:
    n = (narration or "").upper()
    for label, pattern in COMPILED_RULES:
        if pattern.search(n):
            return label
    return "OTHER"


def categorize_transactions(transactions: list[Transaction]) -> list[Transaction]:
    for t in transactions:
        t.category = categorize_transaction(t.narration)
    return transactions


def category_summary(transactions: list[Transaction]) -> list[dict]:
    summary = {}
    for t in transactions:
        cat = t.category or "OTHER"
        if cat not in summary:
            summary[cat] = {"category": cat, "count": 0, "total_debit": 0.0, "total_credit": 0.0}
        summary[cat]["count"] += 1
        summary[cat]["total_debit"] += t.debit
        summary[cat]["total_credit"] += t.credit
    return sorted(summary.values(), key=lambda x: x["count"], reverse=True)