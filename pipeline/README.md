# Pipeline — Offline Ingestion & Eval

This folder contains the offline Python pipeline for PDF ingestion, chunking, embedding, and evaluation. It runs locally in Docker and is not deployed.

## Setup

1. Copy `.env.example` to `.env` and add your API keys:
   ```
   OPENAI_API_KEY=sk-...
   PINECONE_API_KEY=pcsk_...
   PINECONE_INDEX_NAME=aeroquery
   COHERE_API_KEY=...
   LLM_MODEL=gpt-5.4-mini
   ```

2. Place source PDFs in `data/raw/` (Canada AIM)

3. Build the Docker image:
   ```bash
   docker compose build
   ```

## Commands

### Ingestion (parse → chunk → embed → upsert to Pinecone)
```bash
docker compose run --rm app python -m ingestion.embed
```

### Test retrieval (with and without reranking)
```bash
docker compose run --rm app python -m retrieval.search
```

### Ask a question (full RAG pipeline)
```bash
docker compose run --rm app python -m generation.rag
```

### Run eval (change `LLM_MODEL` in `eval/run_eval.py` to test different models)
```bash
docker compose run --rm app python -m eval.run_eval
```

## Structure

```
ingestion/
  parse.py      — PDF extraction with PyMuPDF (block-based, handles two-column)
  clean.py      — Strip headers, footers, page numbers
  chunk.py      — Section-aware chunking (512 token max, 50 token overlap)
  embed.py      — Embed chunks with OpenAI, upsert to Pinecone

retrieval/
  search.py     — Query embedding → Pinecone search → Cohere rerank

generation/
  prompt.py     — System prompt template with context stuffing
  rag.py        — Full RAG pipeline (retrieve → prompt → generate)

eval/
  test_set.json — 25 QA pairs from actual TC AIM content
  run_eval.py   — LLM-as-judge eval (correctness + faithfulness)
  results/      — Per-model eval results (JSON)
```

## Stats

- **1,614 chunks** from Canada AIM (avg 295 tokens)
- **3,072-dim** embeddings (OpenAI text-embedding-3-large)
- **Cosine similarity** search in Pinecone, `canada` namespace
