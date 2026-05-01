# AeroQuery

Production-grade RAG system over Canadian aviation regulations (TC AIM), with hybrid retrieval, Cohere reranking, automated eval pipelines, and a live demo — built to answer questions a pilot actually asks.

## Why This Project

I'm a Canadian pilot building my PPL. Aviation regulations are dense, hierarchical, and full of cross-references — a harder RAG problem than most tutorials tackle. The eval pipeline proves the system works with numbers, not vibes.

## Architecture

```
User question
  → OpenAI text-embedding-3-large (query → 3072-dim vector)
    → Pinecone (cosine similarity, top-10 chunks)
      → Cohere rerank-v4.0-pro (re-score, return top-5)
        → GPT-5.4-mini (generate cited answer from context)
```

## Project Structure

```
pipeline/      — Offline Python: PDF ingestion, chunking, embedding, eval
api/           — Azure Functions: Python API serving the RAG pipeline
web/           — Next.js + Tailwind: frontend with RAG toggle + eval display
```

See each folder's README for setup and run instructions.

## Tech Stack

| Layer | Tool |
|---|---|
| PDF Parsing | PyMuPDF |
| Embeddings | OpenAI text-embedding-3-large (3072 dims) |
| Vector DB | Pinecone (serverless, cosine, namespaces) |
| Reranking | Cohere rerank-v4.0-pro |
| LLM | OpenAI GPT-5.4-mini |
| Eval | LLM-as-judge (custom pipeline) |
| API | Azure Functions (Python) |
| Frontend | Next.js + Tailwind CSS |
| Deployment | Azure (API) + Netlify (frontend) |

## Eval Benchmark

| Config | Correctness | Faithfulness |
|---|---|---|
| GPT-5.4-mini + RAG + Rerank | **0.92** | 0.95 |
| GPT-5.4-mini + RAG (no rerank) | 0.91 | 0.98 |
| GPT-5.4-mini Bare | 0.81 | N/A |
| GPT-3.5 + RAG (no rerank) | 0.84 | 0.87 |
| GPT-3.5 Bare | 0.68 | N/A |

*25 questions, judged by GPT-5.4-mini. RAG improves GPT-3.5 correctness by +16 points.*

## Current Status

- [x] PDF ingestion pipeline (parse, clean, chunk, embed, upsert)
- [x] RAG query pipeline (retrieve, rerank, generate with citations)
- [x] Eval pipeline (LLM-as-judge, multi-model comparison)
- [x] Cohere Reranking
- [x] Azure Functions API (`/compare`, `/ask`, `/retrieve` endpoints)
- [x] Next.js frontend (side-by-side RAG vs bare LLM comparison + eval table)
- [x] Deployed — [Live Demo](https://aeroquery.netlify.app) | [API](https://aeroquery-api.azurewebsites.net/api)
- [ ] LangGraph agentic layer
- [ ] WebLLM client-side model option


