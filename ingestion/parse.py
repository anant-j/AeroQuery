import pdfplumber
from ingestion.clean import clean_canada_page

CANADA_PDF = "data/raw/CANADA_AIM.pdf"


def extract_text_from_pdf(pdf_path):
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append((page.page_number, text))
    return pages


if __name__ == "__main__":
    pages = extract_text_from_pdf(CANADA_PDF)
    print(f"Total pages extracted: {len(pages)}")

    sample_indices = [99, 149, 199]
    for i in sample_indices:
        if i < len(pages):
            page_num, text = pages[i]
            cleaned = clean_canada_page(text)
            print(f"\n--- Page {page_num} (raw) ---")
            print(text[:800])
            print(f"\n--- Page {page_num} (cleaned) ---")
            print(cleaned[:800])
            print("...")