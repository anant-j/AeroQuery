# AeroQuery Pipeline

Offline Python pipeline. It ingests the CARs into Pinecone and benchmarks answer quality. It runs locally in Docker and isn't deployed.

## Setup

1. `cp .env.example .env`, then fill in the Azure OpenAI, Pinecone, and Cohere keys.
2. Put the CARs XML ([SOR-96-433](https://laws-lois.justice.gc.ca/eng/XML/SOR-96-433.xml)) at `data/raw/SOR-96-433.xml`.
3. For the Llama eval runs, serve `llama3.2:1b` with Ollama on the host.
4. `docker compose build`

## Commands

```bash
# Ingest: parse XML → chunk (≤512 tokens) → embed → upsert into a NEW namespace
docker compose run --rm app python -m ingestion.tc_car_xml.embed --dry-run
docker compose run --rm app python -m ingestion.tc_car_xml.embed --namespace canada-car-YYYY-MM-DD

# Try retrieval or a full RAG answer
docker compose run --rm app python -m retrieval.search
docker compose run --rm app python -m generation.rag

# Evaluate (paid API calls; resumes existing results)
docker compose run --rm app python -m eval.run_eval --allow-unreviewed --limit 95

# Flag the worst-scoring answers for manual review
docker compose run --rm app python -m eval.error_analysis --run eval/results/car
```

If you change the namespace, update `NAMESPACE` in `retrieval/search.py` and `api/src/constants.py` (currently `canada-car-2026-06-17`, 2,045 chunks).

## Evaluation

- **Matrix:** 4 models (GPT-5.4, -mini, -nano, Llama 3.2 1B) × 3 configs (bare, RAG, RAG + rerank). GPT-5.4 judges with RAGAS metrics.
- **Benchmark:** `eval/car_test_set.json` has 250 machine-generated questions with cited sources, created by `eval/generate_car_benchmark.py`. It hasn't been reviewed by a human, so runs require `--allow-unreviewed`.
- **Results:** stored in `eval/results/car/`, one folder per model, plus `manifest.json` and `all_summaries.json`.
- **Resuming:** runs pick up where they left off. If the models, prompts, or namespace change, the evaluator refuses to mix results; pass `--results-dir` to start a new run.
- **Statistics:** failed metrics are recorded as missing, not zero. Summaries include bootstrap CIs and paired comparisons.

## Layout

```
ingestion/tc_car_xml/  parse, clean, chunk, embed
retrieval/search.py    embed → Pinecone → Cohere rerank
generation/            prompt + manual RAG runner
eval/                  benchmark, runner, statistics, error analysis
```

Tests:

```bash
docker compose run --rm app python -m unittest eval.test_car_eval eval.test_statistics ingestion.tc_car_xml.test_pipeline
```
