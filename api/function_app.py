import azure.functions as func
import json
import logging
import os
from openai import OpenAI
from pinecone import Pinecone
import cohere

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

EMBEDDING_MODEL = "text-embedding-3-large"
RERANK_MODEL = "rerank-v4.0-pro"
NAMESPACE = "canada"

SYSTEM_PROMPT = """You are an expert aviation regulation assistant specializing in Canadian aviation regulations (TC AIM - Transport Canada Aeronautical Information Manual).

Rules:
1. Answer ONLY based on the provided context. Do not use any outside knowledge.
2. Cite specific section numbers (e.g., "Section 2.3.1") in your answer.
3. If the context does not contain enough information to answer the question, say: "I don't have enough information in the available regulations to answer this question."
4. Be precise and concise. Pilots need clear, unambiguous answers.
5. If multiple sections are relevant, reference all of them."""


def embed_query(query):
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=[query])
    return response.data[0].embedding


def search_pinecone(query_vector, top_k=10):
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index = pc.Index(os.environ.get("PINECONE_INDEX_NAME", "aeroquery"))

    results = index.query(
        vector=query_vector,
        top_k=top_k,
        namespace=NAMESPACE,
        include_metadata=True,
    )

    chunks = []
    for match in results.matches:
        chunks.append({
            "score": match.score,
            "text": match.metadata.get("text", ""),
            "section": match.metadata.get("section", ""),
            "title": match.metadata.get("title", ""),
        })
    return chunks


def rerank_chunks(query, chunks, top_n=5):
    co = cohere.ClientV2(api_key=os.environ["COHERE_API_KEY"])
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


def build_prompt(query, chunks):
    context_parts = []
    for chunk in chunks:
        section_label = f"[Section {chunk['section']}]" if chunk["section"] else ""
        context_parts.append(f"{section_label}\n{chunk['text']}")

    context = "\n\n---\n\n".join(context_parts)

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context from Canadian Aviation Regulations:\n\n{context}\n\n---\n\nQuestion: {query}"},
    ]


def generate_answer(messages, model):
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
    )
    return response.choices[0].message.content, response.usage.total_tokens


@app.route(route="ask", methods=["POST"])
def ask(req: func.HttpRequest) -> func.HttpResponse:
    """Full RAG pipeline: retrieve + rerank + generate answer."""
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(json.dumps({"error": "Invalid JSON"}), status_code=400)

    query = body.get("query", "")
    use_rag = body.get("use_rag", True)
    model = body.get("model", os.environ.get("LLM_MODEL", "gpt-5.4-mini"))

    if not query:
        return func.HttpResponse(json.dumps({"error": "query is required"}), status_code=400)

    if not use_rag:
        # Bare LLM — no retrieval
        messages = [
            {"role": "system", "content": "You are an aviation regulation expert. Answer based on your knowledge of Canadian aviation regulations."},
            {"role": "user", "content": query},
        ]
        answer, tokens = generate_answer(messages, model)
        return func.HttpResponse(
            json.dumps({"query": query, "answer": answer, "model": model, "tokens": tokens, "use_rag": False}),
            mimetype="application/json",
        )

    # RAG pipeline
    query_vector = embed_query(query)
    chunks = search_pinecone(query_vector)
    chunks = rerank_chunks(query, chunks)
    messages = build_prompt(query, chunks)
    answer, tokens = generate_answer(messages, model)

    return func.HttpResponse(
        json.dumps({
            "query": query,
            "answer": answer,
            "model": model,
            "tokens": tokens,
            "use_rag": True,
            "sources": [{"section": c["section"], "title": c["title"]} for c in chunks],
        }),
        mimetype="application/json",
    )


@app.route(route="retrieve", methods=["POST"])
def retrieve(req: func.HttpRequest) -> func.HttpResponse:
    """Retrieve + rerank only — returns chunks for WebLLM mode."""
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(json.dumps({"error": "Invalid JSON"}), status_code=400)

    query = body.get("query", "")
    if not query:
        return func.HttpResponse(json.dumps({"error": "query is required"}), status_code=400)

    query_vector = embed_query(query)
    chunks = search_pinecone(query_vector)
    chunks = rerank_chunks(query, chunks)

    return func.HttpResponse(
        json.dumps({
            "query": query,
            "chunks": [{"section": c["section"], "title": c["title"], "text": c["text"]} for c in chunks],
        }),
        mimetype="application/json",
    )