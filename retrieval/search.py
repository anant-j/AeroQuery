import os
from openai import OpenAI
from pinecone import Pinecone
from dotenv import load_dotenv

load_dotenv()

EMBEDDING_MODEL = "text-embedding-3-large"
NAMESPACE = "canada"


def get_pinecone_index():
    pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
    index_name = os.getenv("PINECONE_INDEX_NAME", "aeroquery")
    return pc.Index(index_name)


def embed_query(query, client):
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=[query])
    return response.data[0].embedding


def search(query, top_k=10, namespace=NAMESPACE):
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    index = get_pinecone_index()

    query_vector = embed_query(query, client)

    results = index.query(
        vector=query_vector,
        top_k=top_k,
        namespace=namespace,
        include_metadata=True,
    )

    chunks = []
    for match in results.matches:
        chunks.append({
            "score": match.score,
            "text": match.metadata.get("text", ""),
            "section": match.metadata.get("section", ""),
            "title": match.metadata.get("title", ""),
            "source": match.metadata.get("source", ""),
        })

    return chunks


if __name__ == "__main__":
    query = "What are the fuel requirements for VFR flight?"
    results = search(query, top_k=5)

    print(f"Query: {query}\n")
    for i, chunk in enumerate(results):
        print(f"--- Result {i+1} (score: {chunk['score']:.3f}) ---")
        print(f"Section: {chunk['section']} | Title: {chunk['title']}")
        print(chunk["text"][:300])
        print()
