import os
from openai import OpenAI
from pinecone import Pinecone
import cohere
from dotenv import load_dotenv

load_dotenv()

EMBEDDING_MODEL = "text-embedding-3-large"
NAMESPACE = "canada"
RERANK_MODEL = "rerank-v4.0-pro"


def get_pinecone_index():
    pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
    index_name = os.getenv("PINECONE_INDEX_NAME", "aeroquery")
    return pc.Index(index_name)


def embed_query(query, client):
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=[query])
    return response.data[0].embedding


def rerank(query, chunks, top_n=5):
    """Rerank chunks using Cohere cross-encoder."""
    co = cohere.ClientV2(api_key=os.getenv("COHERE_API_KEY"))
    docs = [c["text"] for c in chunks]

    response = co.rerank(
        model=RERANK_MODEL,
        query=query,
        documents=docs,
        top_n=top_n,
    )

    reranked = []
    for result in response.results:
        chunk = chunks[result.index].copy()
        chunk["rerank_score"] = result.relevance_score
        reranked.append(chunk)

    return reranked


def search(query, top_k=10, namespace=NAMESPACE, use_rerank=True, rerank_top_n=5):
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

    if use_rerank and chunks:
        chunks = rerank(query, chunks, top_n=rerank_top_n)

    return chunks


if __name__ == "__main__":
    query = "What are the fuel requirements for VFR flight?"

    print("=== Without Reranking ===")
    results = search(query, top_k=5, use_rerank=False)
    for i, chunk in enumerate(results):
        print(f"  {i+1}. [{chunk['section']}] {chunk['title'][:50]} (score: {chunk['score']:.3f})")

    print("\n=== With Cohere Reranking ===")
    results = search(query, top_k=10, use_rerank=True, rerank_top_n=5)
    for i, chunk in enumerate(results):
        print(f"  {i+1}. [{chunk['section']}] {chunk['title'][:50]} (rerank: {chunk['rerank_score']:.3f})")
