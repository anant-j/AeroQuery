"""Compare Azure and local models on a source-cited CAR benchmark."""

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

os.environ["RAGAS_DO_NOT_TRACK"] = "true"

import ragas
from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAI
from ragas.embeddings import OpenAIEmbeddings
from ragas.llms import llm_factory
from ragas.metrics.collections import (
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
    Faithfulness,
    FactualCorrectness,
    SemanticSimilarity,
)

from eval.generate_car_benchmark import BENCHMARK_PATH, file_hash, normalize, validate_questions, write_json
from eval.statistics import metric_summary, paired_metric_summary
from ingestion.tc_car_xml.parse import CAR_XML, parse_document
from retrieval.search import RERANK_MODEL, get_pinecone_index, rerank, search

load_dotenv()

PROTOCOL_VERSION = 4
AZURE_MODELS = ("gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.5")
LOCAL_MODEL = "llama3.2:1b"
JUDGE_MODEL = "gpt-5.4-mini"
EMBEDDING_MODEL = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434/v1")
NAMESPACE = "canada-car-2026-06-17"
CANDIDATE_COUNT = 10
CONTEXT_COUNT = 5
METRIC_TIMEOUT_SECONDS = 180
CONTEXT_METRICS = ("faithfulness", "context_precision", "context_recall")
ANSWER_METRICS = ("answer_relevancy", "factual_correctness", "semantic_similarity")
ALL_METRICS = CONTEXT_METRICS + ANSWER_METRICS
CONFIGS = ("bare", "rag", "rag_rerank")
RAG_SYSTEM = """You are an assistant answering questions about the Canadian Aviation Regulations.
Use only the supplied context, cite CAR section numbers, and include relevant limitations and exceptions.
If the context is insufficient, say: "I don't have enough information in the available regulations to answer this question."
Do not treat regulations outside the provided context as established."""
BARE_SYSTEM = "You are an aviation regulation expert. Answer using your knowledge of Canadian aviation regulations."


def load_benchmark(path, allow_unreviewed=False, limit=None):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    manifest = data["manifest"]
    if manifest.get("source") != "TC_CAR" or not manifest.get("point_in_time"):
        raise ValueError("Benchmark must identify a CAR point-in-time snapshot")
    if manifest.get("xml_sha256") != file_hash(CAR_XML):
        raise ValueError("Benchmark was created from a different CAR XML snapshot")
    documents = {doc["section"]: doc for doc in parse_document(CAR_XML) if doc["kind"] == "section"}
    questions = [item for section in data["sections"].values() for item in section]
    if len(questions) != manifest["target_count"]:
        raise ValueError(f"Benchmark is incomplete: {len(questions)}/{manifest['target_count']}")
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit must be positive")
        questions = questions[:limit]
    seen = set()
    for item in questions:
        if not all(isinstance(item.get(k), str) and item[k].strip()
                   for k in ("question", "ground_truth", "section", "source_url", "evidence_quote")):
            raise ValueError("Each benchmark question needs an answer, citation, and evidence quote")
        key = normalize(item["question"]).casefold()
        if key in seen:
            raise ValueError("Benchmark contains duplicate questions")
        seen.add(key)
        document = documents.get(item["section"])
        if (not document or document["point_in_time"] != manifest["point_in_time"]
                or item["source_url"] != document["source_url"]):
            raise ValueError(f"Invalid source citation for {item['section']}")
        validate_questions([item], document, 1)
        if not allow_unreviewed and item.get("reviewed") is not True:
            raise ValueError("Benchmark contains unreviewed questions; use --allow-unreviewed for a provisional run")
    return manifest, questions


def azure_client(async_client=False, timeout=120, max_retries=2):
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    key = os.getenv("AZURE_OPENAI_API_KEY", "")
    if not endpoint or not key:
        raise ValueError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY are required")
    client_class = AsyncOpenAI if async_client else OpenAI
    return client_class(api_key=key, base_url=f"{endpoint}/openai/v1/",
                        timeout=timeout, max_retries=max_retries)


def make_scorers():
    client = azure_client(async_client=True)
    llm = llm_factory(JUDGE_MODEL, provider="openai", client=client,
                      max_completion_tokens=4096, temperature=1.0)
    # RAGAS 0.4.3 does not recognize dotted GPT-5 deployment names as reasoning models.
    llm.model_args.pop("max_tokens", None)
    llm.model_args.pop("top_p", None)
    embeddings = OpenAIEmbeddings(client=client, model=EMBEDDING_MODEL)
    similarity_client = azure_client(timeout=30, max_retries=1)
    similarity_embeddings = OpenAIEmbeddings(client=similarity_client, model=EMBEDDING_MODEL)
    scorers = {
        "faithfulness": Faithfulness(llm=llm),
        "context_precision": ContextPrecision(llm=llm),
        "context_recall": ContextRecall(llm=llm),
        "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=embeddings),
        "factual_correctness": FactualCorrectness(llm=llm),
        "semantic_similarity": SemanticSimilarity(embeddings=similarity_embeddings),
    }
    return scorers, client, similarity_client


async def score_ragas(scorers, question, answer, reference, contexts):
    scores, errors = {}, {}

    async def score_one(name, scorer):
        if not contexts and name in CONTEXT_METRICS:
            return
        start = time.monotonic()
        print(f"    {name}: started", flush=True)

        async def calculate():
            if name == "semantic_similarity":
                # RAGAS calls synchronous embedding methods inside ascore().
                return await asyncio.to_thread(scorer.score, response=answer, reference=reference)
            if name == "faithfulness":
                return await scorer.ascore(user_input=question, response=answer, retrieved_contexts=contexts)
            if name in ("context_precision", "context_recall"):
                return await scorer.ascore(user_input=question, reference=reference, retrieved_contexts=contexts)
            if name == "answer_relevancy":
                return await scorer.ascore(user_input=question, response=answer)
            return await scorer.ascore(response=answer, reference=reference)

        try:
            result = await asyncio.wait_for(calculate(), timeout=METRIC_TIMEOUT_SECONDS)
            value = result.value
            if value is None or not math.isfinite(value):
                raise ValueError("non-finite or missing metric")
            scores[name] = round(value, 4)
            print(f"    {name}: completed in {time.monotonic() - start:.1f}s", flush=True)
        except Exception as error:
            scores[name] = None
            errors[name] = f"{type(error).__name__}: {error}"
            print(f"    {name}: failed after {time.monotonic() - start:.1f}s: {type(error).__name__}: {error}",
                  flush=True)
    await asyncio.gather(*(score_one(name, scorer) for name, scorer in scorers.items()))
    if errors and not any(value is not None for value in scores.values()):
        raise RuntimeError(f"All applicable metrics failed: {', '.join(errors)}")
    return scores, errors


def get_contexts(item, cache, namespace, point_in_time, delay):
    question = item["question"]
    if question in cache:
        return cache[question]
    candidates = search(question, top_k=CANDIDATE_COUNT, namespace=namespace, use_rerank=False)
    if not candidates:
        raise ValueError(f"No CAR retrieval results for {question!r}")
    if any(chunk.get("source") != "TC_CAR" or chunk.get("point_in_time") != point_in_time
           for chunk in candidates):
        raise ValueError("Retrieved vectors do not match the benchmark CAR snapshot")
    ranked = rerank(question, candidates, top_n=CONTEXT_COUNT)
    if not ranked:
        raise ValueError(f"No Cohere reranking results for {question!r}")
    if delay:
        time.sleep(delay)
    return {"rag": candidates[:CONTEXT_COUNT], "rag_rerank": ranked}


def messages_for(question, contexts):
    if contexts is None:
        return [{"role": "system", "content": BARE_SYSTEM}, {"role": "user", "content": question}]
    context = "\n\n---\n\n".join(f"[CAR {c['section']}]\n{c['text']}" for c in contexts)
    return [
        {"role": "system", "content": RAG_SYSTEM},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
    ]


def generate(client, model, messages, local=False):
    kwargs = {"model": model, "messages": messages}
    if local:
        kwargs["temperature"] = 0
    response = client.chat.completions.create(**kwargs)
    if not response.choices or not response.choices[0].message.content:
        raise ValueError(f"{model} returned an empty answer")
    return response.choices[0].message.content, response.usage.total_tokens if response.usage else 0


def summarize(results, model, manifest):
    summary = {"model": model, "run": manifest, "num_questions": len(results["bare"])}
    for config in CONFIGS:
        metrics = ANSWER_METRICS if config == "bare" else ALL_METRICS
        summary[config] = {
            metric: metric_summary([row.get(metric) for row in results[config]])
            for metric in metrics
        }
    summary["paired_comparisons"] = {
        "rag_minus_bare": {
            metric: paired_metric_summary(results["bare"], results["rag"], metric)
            for metric in ANSWER_METRICS
        },
        "rerank_minus_rag": {
            metric: paired_metric_summary(results["rag"], results["rag_rerank"], metric)
            for metric in ALL_METRICS
        },
    }
    return summary


def print_comparison(summaries):
    print("\nCAR evaluation comparison (mean; valid sample count)", flush=True)
    if any(summary["run"]["provisional"] for summary in summaries):
        print("Provisional: the benchmark has not been fully human-reviewed.", flush=True)
    for metric in ALL_METRICS:
        print(f"\n{metric}")
        for summary in summaries:
            values = []
            for config in CONFIGS:
                stats = summary[config].get(metric)
                if stats is None or stats["mean"] is None:
                    values.append(f"{config}=n/a")
                else:
                    values.append(f"{config}={stats['mean']:.3f} (n={stats['n']})")
            print(f"  {summary['model']}: {'; '.join(values)}")


def run_eval(benchmark=BENCHMARK_PATH, namespace=NAMESPACE, allow_unreviewed=False,
             limit=None, delay=7, models=(*AZURE_MODELS, LOCAL_MODEL), run_tag="default",
             results_root=Path("eval/results/car")):
    slugs = [re.sub(r"[^a-zA-Z0-9._-]", "-", model) for model in models]
    if not namespace or not models or len(slugs) != len(set(slugs)):
        raise ValueError("A namespace and uniquely named models are required")
    benchmark_manifest, questions = load_benchmark(benchmark, allow_unreviewed, limit)
    stats = get_pinecone_index().describe_index_stats()
    namespace_info = stats.namespaces.get(namespace)
    if stats.dimension != 3072 or not namespace_info or not namespace_info.vector_count:
        raise ValueError("CAR namespace is missing, empty, or not 3072-dimensional")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    manifest = {
        "protocol_version": PROTOCOL_VERSION, "benchmark": benchmark_manifest,
        "questions_sha256": hashlib.sha256(json.dumps(questions, sort_keys=True).encode()).hexdigest(),
        "namespace": namespace, "index": os.getenv("PINECONE_INDEX_NAME", "aeroquery"),
        "namespace_vector_count": namespace_info.vector_count,
        "azure_endpoint_sha256": hashlib.sha256(endpoint.encode()).hexdigest(),
        "embedding_model": EMBEDDING_MODEL, "judge_model": JUDGE_MODEL,
        "ragas_version": ragas.__version__, "reranker": RERANK_MODEL,
        "candidate_count": CANDIDATE_COUNT, "context_count": CONTEXT_COUNT,
        "models": list(models), "rag_prompt_sha256": hashlib.sha256(RAG_SYSTEM.encode()).hexdigest(),
        "bare_prompt_sha256": hashlib.sha256(BARE_SYSTEM.encode()).hexdigest(),
        "run_tag": run_tag, "provisional": any(not q.get("reviewed") for q in questions),
    }
    run_id = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:16]
    directory = Path(results_root) / run_id
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "manifest.json", manifest)
    print(f"CAR evaluation run: {directory} ({len(questions)} questions"
          f"{', provisional' if manifest['provisional'] else ''})", flush=True)
    cache_path = directory / "retrieval_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    scorers, judge_client, similarity_client = make_scorers()
    azure = azure_client()
    local = OpenAI(api_key="ollama", base_url=OLLAMA_BASE_URL, timeout=120) if LOCAL_MODEL in models else None
    all_summaries = []

    with asyncio.Runner() as runner:
        try:
            for model in models:
                slug = re.sub(r"[^a-zA-Z0-9._-]", "-", model)
                model_dir = directory / slug
                model_dir.mkdir(exist_ok=True)
                results = {}
                for config in CONFIGS:
                    path = model_dir / f"{config}_results.json"
                    results[config] = json.loads(path.read_text()) if path.exists() else []
                    if len(results[config]) > len(questions) or any(
                        row["question"] != questions[i]["question"] for i, row in enumerate(results[config])
                    ):
                        raise ValueError(f"Stored results do not match this benchmark: {path}")
                for i, item in enumerate(questions):
                    if all(len(results[config]) > i for config in CONFIGS):
                        continue
                    contexts = get_contexts(item, cache, namespace, benchmark_manifest["point_in_time"], delay)
                    if item["question"] not in cache:
                        cache[item["question"]] = contexts
                        write_json(cache_path, cache)
                    print(f"{model} [{i + 1}/{len(questions)}]", flush=True)
                    for config in CONFIGS:
                        if len(results[config]) > i:
                            continue
                        chunks = None if config == "bare" else contexts[config]
                        client = local if model == LOCAL_MODEL else azure
                        print(f"  {config}: generating", flush=True)
                        answer, tokens = generate(client, model, messages_for(item["question"], chunks),
                                                  local=model == LOCAL_MODEL)
                        print(f"  {config}: scoring", flush=True)
                        shared_scores = cache[item["question"]].get("context_scores", {}).get(config)
                        selected_scorers = {
                            name: scorer for name, scorer in scorers.items()
                            if not shared_scores or name not in ("context_precision", "context_recall")
                        }
                        scores, errors = runner.run(score_ragas(
                            selected_scorers, item["question"], answer, item["ground_truth"],
                            [chunk["text"] for chunk in chunks] if chunks else [],
                        ))
                        if shared_scores:
                            scores.update(shared_scores)
                        elif config != "bare" and all(
                            scores.get(name) is not None for name in ("context_precision", "context_recall")
                        ):
                            shared_scores = {name: scores[name] for name in ("context_precision", "context_recall")}
                            cache[item["question"]].setdefault("context_scores", {})[config] = shared_scores
                            write_json(cache_path, cache)
                        results[config].append({
                            "question": item["question"], "ground_truth": item["ground_truth"],
                            "section": item["section"], "source_url": item["source_url"],
                            "answer": answer, "tokens": tokens, **scores, "metric_errors": errors,
                        })
                        write_json(model_dir / f"{config}_results.json", results[config])
                        print(f"  {config}: checkpoint saved", flush=True)
                summary = summarize(results, model, manifest)
                write_json(model_dir / "summary.json", summary)
                all_summaries.append(summary)
                write_json(directory / "all_summaries.json", all_summaries)
                print(f"Completed {model}; results in {model_dir}", flush=True)
        finally:
            runner.run(judge_client.close())
            similarity_client.close()
            azure.close()
            if local:
                local.close()
    print_comparison(all_summaries)
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", default=BENCHMARK_PATH)
    parser.add_argument("--namespace", default=NAMESPACE)
    parser.add_argument("--allow-unreviewed", action="store_true")
    parser.add_argument("--limit", type=int, help="Evaluate only the first N questions in a separate run")
    parser.add_argument("--cohere-delay", type=float, default=7)
    parser.add_argument("--models", nargs="+", default=[*AZURE_MODELS, LOCAL_MODEL])
    parser.add_argument("--run-tag", default="default", help="Use a new tag after updating vectors in place")
    parser.add_argument("--results-dir", default="eval/results/car")
    args = parser.parse_args()
    if args.cohere_delay < 0:
        parser.error("--cohere-delay cannot be negative")
    run_eval(args.benchmark, args.namespace, args.allow_unreviewed, args.limit,
             args.cohere_delay, args.models, args.run_tag, args.results_dir)


if __name__ == "__main__":
    main()
