import pdfplumber
from ingestion.clean import clean_canada_page
from ingestion.chunk import chunk_document

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
    # 1. Extract
    pages = extract_text_from_pdf(CANADA_PDF)
    print(f"Pages extracted: {len(pages)}")

    # 2. Clean and concatenate (skip first 60 pages — TOC/index)
    cleaned_pages = [clean_canada_page(text) for _, text in pages[60:]]
    full_text = "\n".join(cleaned_pages)
    print(f"Total characters: {len(full_text)}")

    # 3. Chunk
    chunks = chunk_document(full_text)
    print(f"Total chunks: {len(chunks)}")

    # Stats
    token_counts = [len(c["text"]) // 4 for c in chunks]
    print(f"Avg tokens per chunk: {sum(token_counts) // len(token_counts)}")
    print(f"Min tokens: {min(token_counts)}, Max tokens: {max(token_counts)}")

    # Show a few sample chunks
    for i in [0, len(chunks) // 2, -1]:
        c = chunks[i]
        print(f"\n{'='*60}")
        print(f"Chunk {i} | Section: {c['metadata']['section']} | Title: {c['metadata']['title']}")
        print(f"Tokens: ~{len(c['text']) // 4}")
        print(f"{'='*60}")
        print(c["text"][:500])
        print("...")