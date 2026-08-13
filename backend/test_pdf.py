# test_pdf.py  (put this in bank-analysis/backend/, alongside the app/ folder)
from app.parsers.registry import parse_file

result = parse_file("sample_data/your_statement.pdf", account_id="TEST001")

print("Parser used:", result.parser_used)
print("Warnings:", result.warnings)
print("Transaction count:", len(result.transactions))
print()
for t in result.transactions[:10]:
    print(t.date, "|", t.narration, "|", "Dr" if t.debit else "Cr", t.debit or t.credit, "| Bal:", t.balance)