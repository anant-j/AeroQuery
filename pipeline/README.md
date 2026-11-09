# Pipeline — Offline Ingestion & Evaluation

This offline Python pipeline builds the RAG system: it parses PDFs, chunks the text, embeds the content, upserts it to Pinecone, and benchmarks retrieval via RAGAS. It runs locally in Docker and is not deployed.

For the project overview, see the root README. Once Pinecone is populated, this pipeline only runs again for re-ingestion or evaluation.

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

# Run RAGAS eval (3 models, 3 configs, up to 6 metrics, 50 questions)
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
  test_set.json   — 50 QA pairs from TC AIM content
  run_eval.py     — RAGAS eval with 6 metrics, Azure AI Foundry judge, incremental saves
  error_analysis.py — Classify failure modes (judge disagreement vs retrieval miss vs hallucination)
  results/        — Per-model eval results (JSON), retrieval cache
```

## Stats

- **1,614 chunks** from Canada AIM (avg 295 tokens, 512 max)
- **3,072-dim** embeddings (OpenAI text-embedding-3-large)
- **Cosine similarity** search in Pinecone, `canada` namespace
- **50 questions** across 40 sections; 3 models and 3 configs
- Each bare-LLM question has 3 applicable metrics; each RAG question has 6 (2,250 score slots when complete)
- The checked-in results are exploratory point estimates, judged by one model. They have not been independently human-scored.
- Evaluation failures are stored as missing values, not zero scores. Summaries report valid sample counts, percentile-bootstrap intervals, and paired comparisons.
- The bootstrap intervals describe variation across sampled questions; they do not quantify judge bias or make a convenience sample representative.
- The target is a 500-question source-cited, human-reviewed benchmark, stratified by section, question type, and difficulty; the current set is not yet representative.
