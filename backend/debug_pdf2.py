# debug_pdf2.py — put in backend/, run: python debug_pdf2.py
import pdfplumber

FILE_PATH = r"C:\Bank-Analysis\backend\uploaded_files\a25db672-e848-4e8e-a40e-b3f29d8ebccd_098030016134598.pdf"  # <-- update

with pdfplumber.open(FILE_PATH) as pdf:
    page = pdf.pages[0]
    text = page.extract_text()
    print("Extracted text length:", len(text) if text else 0)
    print()
    print("--- First 800 chars ---")
    print(text[:800] if text else "(NO TEXT EXTRACTED - likely a scanned image)")