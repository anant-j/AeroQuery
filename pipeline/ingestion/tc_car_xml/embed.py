import argparse
import os
import time

from ingestion.tc_car_xml.chunk import chunk_document, token_count
from ingestion.tc_car_xml.parse import CAR_XML, parse_document

EMBEDDING_DIM = 3072
BATCH_SIZE = 20


def embed_texts(texts, client, model):
    response = client.embeddings.create(model=model, input=texts)
    items = sorted(response.data, key=lambda item: item.index)
    if [item.index for item in items] != list(range(len(texts))):
        raise ValueError("Embedding response does not match the input batch")
    if any(len(item.embedding) != EMBEDDING_DIM for item in items):
        raise ValueError(f"Expected {EMBEDDING_DIM}-dimensional embeddings")
    return [item.embedding for item in items]


def run_ingestion(path, namespace, dry_run=False):
    documents = parse_document(path)
    chunks = chunk_document(documents)
    print(f"Parsed {len(documents)} sections and schedules; generated {len(chunks)} chunks")
    if dry_run:
        print(f"Maximum chunk size: {max(token_count(c['text']) for c in chunks)} tokens")
        print(f"Images omitted: {sum('[Image not included:' in c['text'] for c in chunks)} chunks")
        print(f"Formula-containing chunks: {sum('[Formula, consult source' in c['text'] for c in chunks)}")
        return
    if not namespace:
        raise ValueError("A new, empty Pinecone namespace is required")

    from dotenv import load_dotenv
    from openai import OpenAI
    from pinecone import Pinecone

    load_dotenv()
    required = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "PINECONE_API_KEY", "PINECONE_INDEX_NAME")
    missing = [key for key in required if not os.getenv(key, "").strip()]
    if missing:
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")
    model = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
    client = OpenAI(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        base_url=f"{os.environ['AZURE_OPENAI_ENDPOINT'].rstrip('/')}/openai/v1/",
    )
    index = Pinecone(api_key=os.environ["PINECONE_API_KEY"]).Index(os.environ["PINECONE_INDEX_NAME"])
    stats = index.describe_index_stats()
    if stats.dimension != EMBEDDING_DIM:
        raise ValueError(f"Index dimension {stats.dimension} does not match {EMBEDDING_DIM}")
    if namespace in stats.namespaces:
        raise ValueError(f"Namespace {namespace!r} already exists; choose a new namespace")

    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start:start + BATCH_SIZE]
        vectors = embed_texts([chunk["text"] for chunk in batch], client, model)
        response = index.upsert(
            vectors=[
                {"id": chunk["id"], "values": vector,
                 "metadata": {**chunk["metadata"], "text": chunk["text"], "embedding_model": model}}
                for chunk, vector in zip(batch, vectors)
            ],
            namespace=namespace,
        )
        if response.upserted_count != len(batch):
            raise RuntimeError(f"Pinecone accepted {response.upserted_count} of {len(batch)} vectors")
        print(f"Upserted {start + len(batch)}/{len(chunks)}")
    deadline = time.monotonic() + 60
    while True:
        stats = index.describe_index_stats()
        namespace_stats = stats.namespaces.get(namespace)
        if namespace_stats and namespace_stats.vector_count == len(chunks):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Could not verify {len(chunks)} vectors in namespace {namespace!r}")
        time.sleep(2)
    print(f"Verified {len(chunks)} vectors in namespace {namespace!r}")


def main():
    parser = argparse.ArgumentParser(description="Stage CAR XML embeddings in a new Pinecone namespace")
    parser.add_argument("--xml", default=CAR_XML, help="Path to SOR-96-433 XML")
    parser.add_argument("--namespace", help="New, empty Pinecone namespace")
    parser.add_argument("--dry-run", action="store_true", help="Parse and chunk without contacting services")
    args = parser.parse_args()
    run_ingestion(args.xml, args.namespace, args.dry_run)


if __name__ == "__main__":
    main()
