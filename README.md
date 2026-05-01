# AeroQuery

Production-grade RAG system over Canadian aviation regulations (TC AIM), with dense retrieval, Cohere reranking, automated eval pipelines, and a live demo — built to answer questions a pilot actually asks.

## Why This Project

I'm a Canadian pilot building my PPL. Aviation regulations are dense, hierarchical, and full of cross-references — a harder RAG problem than most tutorials tackle. The eval pipeline proves the system works with numbers, not vibes.

## Architecture

```
User question
  → OpenAI text-embedding-3-large (query → 3072-dim vector)
    → Pinecone (cosine similarity, top-10 chunks)
      → Cohere rerank-v4.0-pro (re-score, return top-5)
        → GPT-5.4-mini (stream cited answer via SSE)
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
| LLM | OpenAI GPT-5.4-mini (server), WebLLM Llama 3.2 1B (browser) |
| Eval | RAGAS v0.4 (6 metrics), Azure AI Foundry (judge) |
| Streaming | SSE via Next.js API route (OpenAI), WebLLM SDK (browser) |
| Orchestration | LiteLLM (multi-provider routing) |
| API | Azure Functions (retrieval), Next.js API route (generation) |
| Frontend | Next.js + Tailwind CSS |
| Deployment | Azure (API + AI Foundry) + Netlify (frontend) |

## Eval Benchmark (RAGAS v0.4 — 6 Industry-Standard Metrics)

**Factual Correctness** (does the answer match ground truth?):

| Model | Bare LLM | RAG | RAG + Rerank | Improvement |
|---|---|---|---|---|
| GPT-5.4-mini | 0.32 | 0.40 | **0.43** | +34% |
| GPT-3.5-turbo | 0.23 | **0.44** | 0.44 | +91% |
| Llama 3.2 1B | 0.07 | 0.18 | **0.22** | +203% |

**Retrieval Quality** (same across models — retrieval is model-independent):

| Metric | RAG | RAG + Rerank |
|---|---|---|
| Context Precision | 0.78 | **0.92** |
| Context Recall | **0.92** | 0.92 |

**Generation Quality:**

| Metric | GPT-5.4-mini | GPT-3.5 | Llama 1B |
|---|---|---|---|
| Faithfulness (RAG+Rerank) | **0.88** | 0.85 | 0.55 |
| Answer Relevancy (RAG+Rerank) | 0.79 | **0.87** | 0.62 |
| Semantic Similarity (RAG+Rerank) | 0.77 | **0.79** | 0.69 |

*50 questions, 6 RAGAS metrics, judged by GPT-5.4-mini via Azure AI Foundry. Key findings:*
- *RAG improves factual correctness for every model — Llama by +203%, GPT-3.5 by +91%*
- *Cohere reranking boosts context precision from 0.78 to 0.92 (+18%)*
- *Retrieval quality is strong (0.92 recall/precision) — generation capability is the bottleneck*
- *Bare Llama 1B hallucinates confidently (0.07 factual correctness); with RAG it triples to 0.22*

## Current Status

- [x] PDF ingestion pipeline (parse, clean, chunk, embed, upsert)
- [x] RAG query pipeline (retrieve, rerank, generate with citations)
- [x] Eval pipeline (LLM-as-judge, multi-model comparison)
- [x] Cohere Reranking
- [x] Azure Functions API (`/compare`, `/ask`, `/retrieve` endpoints)
- [x] Next.js frontend (side-by-side RAG vs bare LLM comparison + eval table)
- [x] Deployed — [Live Demo](https://aeroquery.netlify.app) | [API](https://aeroquery-api.azurewebsites.net/api)
- [x] WebLLM client-side model (Llama 3.2 1B, runs in browser via WebGPU)
- [x] Streaming responses (SSE for OpenAI, SDK streaming for WebLLM)
- [x] Expandable source citations (click §section to see retrieved chunk text)
- [ ] LangGraph agentic layer


