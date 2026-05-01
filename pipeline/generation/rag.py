import os
from openai import OpenAI
from dotenv import load_dotenv
from retrieval.search import search
from generation.prompt import build_prompt

load_dotenv()

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.4-mini")


def ask(query, top_k=10, namespace="canada", model=None, use_rerank=True, base_url=None):
    # 1. Retrieve relevant chunks
    chunks = search(query, top_k=top_k, namespace=namespace, use_rerank=use_rerank)

    # 2. Build prompt with context
    messages = build_prompt(query, chunks)

    # 3. Generate answer
    client_kwargs = {"api_key": os.getenv("OPENAI_API_KEY")}
    if base_url:
        client_kwargs["base_url"] = base_url
        client_kwargs["api_key"] = "ollama"  # Ollama doesn't need a real key
    client = OpenAI(**client_kwargs)
    response = client.chat.completions.create(
        model=model or LLM_MODEL,
        messages=messages,
        temperature=0,
    )

    answer = response.choices[0].message.content

    return {
        "query": query,
        "answer": answer,
        "chunks": chunks,
        "model": model or LLM_MODEL,
        "tokens": response.usage.total_tokens if response.usage else 0,
    }


if __name__ == "__main__":
    test_questions = [
        "What are the fuel requirements for VFR flight in Canada?",
        "What is the minimum visibility for VFR flight?",
        "What are the requirements to fly at night in Canada?",
    ]

    for q in test_questions:
        print(f"\n{'='*60}")
        print(f"Q: {q}")
        print(f"{'='*60}")
        result = ask(q)
        print(f"\nA: {result['answer']}")
        print(f"\n[Tokens used: {result['tokens']}]")
        print(f"[Sources: {', '.join(c['section'] for c in result['chunks'][:3])}]")
