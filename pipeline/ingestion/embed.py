import os
import hashlib
from openai import OpenAI
from pinecone import Pinecone, ServerlessSpec
from dotenv import load_dotenv
from ingestion.parse import extract_text_from_pdf, CANADA_PDF
from ingestion.clean import clean_canada_page
from ingestion.chunk import chunk_document

load_dotenv()

EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIM = 3072
BATCH_SIZE = 100  # Pinecone upsert batch size
NAMESPACE = "canada"


def get_pinecone_index():
    pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
    index_name = os.getenv("PINECONE_INDEX_NAME", "aeroquery")

    # Create index if it doesn't exist
    if not pc.has_index(index_name):
        pc.create_index(
            name=index_name,
            dimension=EMBEDDING_DIM,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
        print(f"Created Pinecone index: {index_name}")

    return pc.Index(index_name)


def embed_texts(texts, client):
    """Embed a batch of texts using OpenAI."""
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]


def make_chunk_id(chunk):
    """Deterministic ID from chunk content so re-runs don't duplicate."""
    content = chunk["metadata"]["section"] + chunk["text"][:200]
    return hashlib.md5(content.encode()).hexdigest()


def run_ingestion():
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    index = get_pinecone_index()

    # 0. Clear old vectors
    print(f"Clearing namespace '{NAMESPACE}'...")
    index.delete(delete_all=True, namespace=NAMESPACE)

    # 1. Extract and clean
    print("Extracting PDF...")
    pages = extract_text_from_pdf(CANADA_PDF)
    cleaned_pages = [clean_canada_page(text) for _, text in pages[60:]]
    full_text = "\n".join(cleaned_pages)

    # 2. Chunk
    chunks = chunk_document(full_text)
    print(f"Total chunks to embed: {len(chunks)}")

    # 3. Embed and upsert in batches
    total_upserted = 0
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        texts = [c["text"] for c in batch]

        embeddings = embed_texts(texts, openai_client)

        vectors = []
        for chunk, embedding in zip(batch, embeddings):
            vectors.append({
                "id": make_chunk_id(chunk),
                "values": embedding,
                "metadata": {
                    **chunk["metadata"],
                    "text": chunk["text"],
                },
            })

        index.upsert(vectors=vectors, namespace=NAMESPACE)
        total_upserted += len(vectors)
        print(f"  Upserted {total_upserted}/{len(chunks)}")

    print(f"\nDone. {total_upserted} vectors in namespace '{NAMESPACE}'")

    # 4. Verify
    stats = index.describe_index_stats()
    print(f"Index stats: {stats}")


if __name__ == "__main__":
    run_ingestion()
