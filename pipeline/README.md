# Pipeline — Offline Ingestion & Evaluation

This offline Python pipeline builds the RAG system: it parses PDFs, chunks the text, embeds the content, upserts it to Pinecone, and benchmarks retrieval via RAGAS. It runs locally in Docker and is not deployed.

For the project overview, see the root README. Once Pinecone is populated, this pipeline only runs again for re-ingestion or evaluation.

## Setup

1. Copy `.env.example` to `.env` and fill in the values. The pipeline uses separate Azure settings for retrieval/generation and for the evaluator's LiteLLM judge:
   ```
  OPENAI_API_KEY=sk-...
  AZURE_OPENAI_API_KEY=...
  AZURE_OPENAI_ENDPOINT=https://your-openai-resource-name.openai.azure.com
  AZURE_OPENAI_DEPLOYMENT=gpt-5.4-mini
  AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large
  AZURE_API_KEY=...
  AZURE_API_BASE=...
  AZURE_API_VERSION=2025-04-01-preview
   PINECONE_API_KEY=pcsk_...
   PINECONE_INDEX_NAME=aeroquery
   COHERE_API_KEY=...
   ```

`OPENAI_API_KEY` is used directly for the GPT-3.5-turbo evaluation model and RAGAS `text-embedding-3-small` embeddings. `AZURE_API_*` configures the GPT-5.4-mini evaluation model and judge. `AZURE_OPENAI_*` configures retrieval and the manual generation/retrieval commands. Pinecone and Cohere are needed when retrieval runs rather than loading a valid cache. The Ollama model runs locally; when using Docker, make sure Ollama is serving `llama3.2:1b` on the host.

2. Place source PDFs in `data/raw/` (Canada AIM).

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
  embed.py        — Embed chunks with Azure OpenAI text-embedding-3-large, upsert to Pinecone in batches

retrieval/
  search.py       — Query embedding → Pinecone search → Cohere rerank

generation/
  prompt.py       — System prompt + context stuffing template
  rag.py          — Full RAG pipeline for manual testing (retrieve → prompt → generate)

eval/
  test_set.json   — 50 QA pairs from TC AIM content
  run_eval.py     — 50-question RAGAS eval; Azure GPT-5.4-mini, OpenAI GPT-3.5-turbo, and local Ollama Llama 3.2 1B generators; Azure GPT-5.4-mini judge
  error_analysis.py — Classify failure modes (judge disagreement vs retrieval miss vs hallucination)
  results/        — Per-model eval results (JSON), retrieval cache
```

## Stats

- **1,614 chunks** from Canada AIM (avg 295 tokens, 512 max)
- **3,072-dim** embeddings (Azure OpenAI text-embedding-3-large deployment)
- **Cosine similarity** search in Pinecone, `canada` namespace
- **50 questions** across 40 sections; 3 models and 3 configs
- Each bare-LLM question has 3 applicable metrics; each RAG question has 6 (2,250 score slots when complete)
- The checked-in results are exploratory point estimates, judged by one model. They have not been independently human-scored.
- Evaluation failures are stored as missing values, not zero scores. Current evaluator summaries report valid sample counts, percentile-bootstrap intervals, and paired comparisons; checked-in historical summaries predate these fields.
- The bootstrap intervals describe variation across sampled questions; they do not quantify judge bias or make a convenience sample representative.
- The target is a 500-question source-cited, human-reviewed benchmark, stratified by section, question type, and difficulty; the current set is not yet representative.
- Checked-in per-question rows predate the current evaluator protocol and lack its protocol-version field. Running the current evaluator regenerates those rows; it reuses the retrieval cache when that cache matches the test set.
