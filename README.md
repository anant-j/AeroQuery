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
data/raw/      — Source aviation regulation PDFs (Canada AIM)
```

## Current Status

- [x] Project scaffold (Docker-based dev environment)
- [x] PDF parsing & cleaning (PyMuPDF)
- [x] Section-aware chunking (1,614 chunks, avg 295 tokens)
- [x] Embeddings & Pinecone upsert (1,614 vectors)
- [x] RAG query pipeline (retrieval + GPT-5.4-mini generation with citations)
- [ ] Eval pipeline (RAGAS)
- [ ] Hybrid search & reranking
- [ ] Agentic layer (LangGraph)
- [ ] API & frontend
