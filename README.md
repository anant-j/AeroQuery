# AeroQuery

Production-grade RAG system over FAA and Nav Canada aviation regulations, with hybrid retrieval, automated eval pipelines, and full observability — built to answer questions a student pilot actually asks.

## Setup

1. Copy `.env.example` to `.env` and add your API keys
2. Build and run:
   ```bash
   docker compose build
   docker compose run --rm app python -m ingestion.embed    # Parse, chunk, embed, upsert
   docker compose run --rm app python -m generation.rag     # Ask questions
   ```

## Project Structure

```
ingestion/     — PDF parsing, cleaning, chunking, and embedding pipeline
retrieval/     — Query embedding and Pinecone vector search
generation/    — Prompt template and LLM generation with citations
eval/          — QA test set and eval pipeline
data/raw/      — Source aviation regulation PDFs (Canada AIM)
```

## Current Status

- [x] Project scaffold (Docker-based dev environment)
- [x] PDF parsing & cleaning (PyMuPDF)
- [x] Section-aware chunking (1,614 chunks, avg 295 tokens)
- [x] Embeddings & Pinecone upsert (1,614 vectors)
- [x] RAG query pipeline (retrieval + GPT-5.4-mini generation with citations)
- [x] Eval pipeline (25 QA pairs, LLM-as-judge)
- [ ] Hybrid search & reranking
- [ ] Agentic layer (LangGraph)
- [ ] Next.js frontend on Netlify (search + RAG toggle + eval display)

## Eval Benchmark

| Metric | GPT-5.4-mini RAG | GPT-5.4-mini Bare | GPT-3.5 RAG | GPT-3.5 Bare |
|---|---|---|---|---|
| **Correctness** | 0.91 | 0.85 | 0.84 | 0.68 |
| **Faithfulness** | 0.90 | N/A | 0.87 | N/A |

*25 questions, judged by GPT-5.4-mini. RAG improves GPT-3.5 correctness by +16 points.*
