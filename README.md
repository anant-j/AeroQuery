# AeroQuery

Production-grade RAG system over FAA and Nav Canada aviation regulations, with hybrid retrieval, automated eval pipelines, and full observability — built to answer questions a student pilot actually asks.

## Setup

1. Copy `.env.example` to `.env` and add your API keys
2. Build and run:
   ```bash
   docker compose build
   docker compose run app python -m ingestion.parse
   ```

## Project Structure

```
ingestion/     — PDF parsing, cleaning, and chunking pipeline
data/raw/      — Source aviation regulation PDFs (Canada AIM)
```

## Current Status

- [x] Project scaffold (Docker-based dev environment)
- [x] PDF parsing & cleaning
- [x] Section-aware chunking (1,518 chunks, avg 317 tokens)
- [x] Embeddings & Pinecone upsert (1,518 vectors)
- [ ] RAG query pipeline
- [ ] Eval pipeline (RAGAS)
- [ ] Hybrid search & reranking
- [ ] Agentic layer (LangGraph)
- [ ] API & frontend
