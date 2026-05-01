# Pipeline — Offline Ingestion & Eval

Offline Python pipeline that *builds* the RAG system: parse PDFs, chunk, embed, upsert to Pinecone, and benchmark with RAGAS. Runs locally in Docker. Not deployed.

Once Pinecone is populated, this only runs again for re-ingestion or eval.

## Setup

1. Copy `.env.example` to `.env` and add API keys:
   ```
   OPENAI_API_KEY=sk-...
   PINECONE_API_KEY=pcsk_...
   PINECONE_INDEX_NAME=aeroquery
   COHERE_API_KEY=...
   ```

2. Place source PDFs in `data/raw/` (Canada AIM)

3. Build:
   ```bash
   docker compose build
   ```

## Commands

```bash
# Ingestion: parse → chunk → embed → upsert to Pinecone
docker compose run --rm app python -m ingestion.embed

# Test retrieval (with and without reranking)
docker compose run --rm app python -m retrieval.search

# Ask a question (full RAG pipeline)
docker compose run --rm app python -m generation.rag

# Run RAGAS eval (3 models × 3 configs × 6 metrics × 50 questions)
docker compose run --rm app python -m eval.run_eval

# Error analysis (diagnose worst-scoring questions)
docker compose run --rm app python -m eval.error_analysis
```

## Structure

```
ingestion/
  parse.py        — PDF extraction with PyMuPDF (block-based, handles two-column)
  clean.py        — Strip headers, footers, page numbers, skip TOC
  chunk.py        — Section-aware chunking (512 token max, 50 token overlap)
  embed.py        — Embed chunks with OpenAI, upsert to Pinecone in batches

retrieval/
  search.py       — Query embedding → Pinecone search → Cohere rerank

generation/
  prompt.py       — System prompt + context stuffing template
  rag.py          — Full RAG pipeline for manual testing (retrieve → prompt → generate)

eval/
  test_set.json   — 50 QA pairs from actual TC AIM content
  run_eval.py     — RAGAS eval with 6 metrics, Azure AI Foundry judge, incremental saves
  error_analysis.py — Classify failure modes (judge disagreement vs retrieval miss vs hallucination)
  results/        — Per-model eval results (JSON), retrieval cache
```

## Stats

- **1,614 chunks** from Canada AIM (avg 295 tokens, 512 max)
- **3,072-dim** embeddings (OpenAI text-embedding-3-large)
- **Cosine similarity** search in Pinecone, `canada` namespace
- **50 questions** × 3 models × 3 configs × 6 RAGAS metrics = 2,700 scores
