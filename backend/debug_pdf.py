# debug_pdf.py — put this in backend/, run with: python debug_pdf.py
import pdfplumber

FILE_PATH = r"C:\Bank-Analysis\backend\uploaded_files\a25db672-e848-4e8e-a40e-b3f29d8ebccd_098030016134598.pdf"  # <-- update this

with pdfplumber.open(FILE_PATH) as pdf:
    print("Total pages:", len(pdf.pages))

    # Check first 3 pages for table structure
    for page_num in [0, 1, 2]:
        if page_num >= len(pdf.pages):
            break
        page = pdf.pages[page_num]
        tables = page.extract_tables()
        print(f"\n--- Page {page_num+1}: {len(tables)} tables found ---")
        for i, table in enumerate(tables):
            print(f"  Table {i}: {len(table)} rows")
            for row in table[:3]:
                print("   ", row)

    # Also dump raw text of page 1 in case it's not a bordered table at all
    print("\n--- Raw text sample, page 1 ---")
    print(pdf.pages[0].extract_text()[:800])