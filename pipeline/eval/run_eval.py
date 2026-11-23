"""Compare Azure and local models on a source-cited CAR benchmark."""

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
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
from ragas.metrics.collections.factual_correctness.util import (
    ClaimDecompositionInput,
    ClaimDecompositionOutput,
)

from eval.generate_car_benchmark import BENCHMARK_PATH, file_hash, normalize, validate_questions, write_json
from eval.statistics import metric_summary, paired_metric_summary
from generation.prompt import SYSTEM_PROMPT, build_prompt
from ingestion.tc_car_xml.parse import CAR_XML, parse_document
from retrieval.search import NAMESPACE, RERANK_MODEL, get_pinecone_index, rerank, search

load_dotenv()

PROTOCOL_VERSION = 9
AZURE_MODELS = ("gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.4")
LOCAL_MODEL = "llama3.2:1b"
JUDGE_MODEL = "gpt-5.4"
EMBEDDING_MODEL = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434/v1")
CANDIDATE_COUNT = 10
CONTEXT_COUNT = 5
METRIC_TIMEOUT_SECONDS = 180
LOCAL_MAX_OUTPUT_TOKENS = 1024
LOCAL_FALLBACK_TEMPERATURE = 0.2
CONTEXT_METRICS = ("faithfulness", "context_precision", "context_recall")
ANSWER_METRICS = ("answer_relevancy", "factual_correctness", "semantic_similarity")
ALL_METRICS = CONTEXT_METRICS + ANSWER_METRICS
CONFIGS = ("bare", "rag", "rag_rerank")
class _QuestionFactualCorrectness(FactualCorrectness):
    def __init__(self, llm, question):
        super().__init__(llm=llm)
        self.question = question

    async def _decompose_claims(self, text):
        input_data = ClaimDecompositionInput(
            response=text, atomicity=self.atomicity, coverage=self.coverage
        )
        prompt = (
            "The question below is background for interpreting the response. "
            "Use it to resolve short or implicit answers into standalone claims, "
            "but do not extract claims from the question or treat its premises as facts.\n"
            f"Question: {json.dumps(self.question, ensure_ascii=False)}\n\n"
            + self.prompt.to_string(input_data)
        )
        result = await self.llm.agenerate(prompt, ClaimDecompositionOutput)
        if not result.claims or any(not claim.strip() for claim in result.claims):
            raise ValueError("Factual correctness judge returned no usable claims")
        return result.claims

    async def _verify_claims(self, claims, reference):
        context = (
            "The question is background only, not evidence. Interpret the answer "
            "in light of the question; infer facts only from the answer.\n"
            f"Question: {json.dumps(self.question, ensure_ascii=False)}\n"
            f"Answer: {json.dumps(reference, ensure_ascii=False)}"
        )
        result = await super()._verify_claims(claims, context)
        if len(result.statements) != len(claims) or any(
            statement.verdict not in (0, 1) for statement in result.statements
        ):
            raise ValueError("Factual correctness judge returned incomplete or invalid verdicts")
        return result


class QuestionAwareFactualCorrectness:
    def __init__(self, llm):
        self.llm = llm

    async def ascore(self, *, question, response, reference):
        scorer = _QuestionFactualCorrectness(self.llm, question)
        return await scorer.ascore(response=response, reference=reference)


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
        "factual_correctness": QuestionAwareFactualCorrectness(llm=llm),
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
            if name == "factual_correctness":
                return await scorer.ascore(question=question, response=answer, reference=reference)
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


class GenerationLimitError(ValueError):
    pass


def generate(client, model, messages, local=False, max_tokens=None, temperature=None):
    kwargs = {"model": model, "messages": messages}
    if local:
        kwargs["temperature"] = 0 if temperature is None else temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    response = client.chat.completions.create(**kwargs)
    if not response.choices or not response.choices[0].message.content:
        raise ValueError(f"{model} returned an empty answer")
    if max_tokens is not None and response.choices[0].finish_reason != "stop":
        if response.choices[0].finish_reason == "length":
            raise GenerationLimitError(f"{model} reached the output token limit")
        raise ValueError(f"{model} did not finish normally: {response.choices[0].finish_reason}")
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


def load_reusable_results(source, models, questions):
    source = Path(source).resolve()
    source_manifest = json.loads((source / "manifest.json").read_text())
    if source_manifest.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("Answer reuse requires a source run with the current protocol version")
    if not set(models).issubset(source_manifest.get("models", [])):
        raise ValueError("Source run does not include every requested model")
    benchmark = {item["question"]: item for item in questions}
    fingerprints, results = {}, {}
    for model in models:
        slug = re.sub(r"[^a-zA-Z0-9._-]", "-", model)
        results[model] = {}
        for config in CONFIGS:
            relative = f"{slug}/{config}_results.json"
            path = source / relative
            if not path.exists():
                fingerprints[relative] = None
                results[model][config] = {}
                continue
            data = path.read_bytes()
            fingerprints[relative] = hashlib.sha256(data).hexdigest()
            rows = json.loads(data)
            if not isinstance(rows, list):
                raise ValueError(f"Invalid source results: {path}")
            by_question = {}
            for row in rows:
                question = row.get("question")
                item = benchmark.get(question)
                if (not item or question in by_question
                        or any(row.get(key) != item[key] for key in ("ground_truth", "section", "source_url"))):
                    raise ValueError(f"Source results do not match this benchmark: {path}")
                by_question[question] = row
            results[model][config] = by_question
    cache_path = source / "retrieval_cache.json"
    cache_data = cache_path.read_bytes() if cache_path.exists() else None
    fingerprints["retrieval_cache.json"] = hashlib.sha256(cache_data).hexdigest() if cache_data else None
    cache = json.loads(cache_data) if cache_data is not None else {}
    return source_manifest, results, cache, {"directory": str(source), "files": fingerprints}


def namespace_vector_count(namespace):
    stats = get_pinecone_index().describe_index_stats()
    namespace_info = stats.namespaces.get(namespace)
    if stats.dimension != 3072 or not namespace_info or not namespace_info.vector_count:
        raise ValueError("CAR namespace is missing, empty, or not 3072-dimensional")
    return namespace_info.vector_count


def run_eval(benchmark=BENCHMARK_PATH, namespace=NAMESPACE, allow_unreviewed=False,
             limit=None, delay=7, models=(*AZURE_MODELS, LOCAL_MODEL), run_tag="default",
             results_root=Path("eval/results/car"), concurrency=4, reuse_answers_from=None):
    slugs = [re.sub(r"[^a-zA-Z0-9._-]", "-", model) for model in models]
    if not namespace or not models or len(slugs) != len(set(slugs)):
        raise ValueError("A namespace and uniquely named models are required")
    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    if LOCAL_MODEL in models and len(models) > 1 and concurrency < 2:
        raise ValueError("concurrency must be at least 2 when evaluating local and hosted models together")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    benchmark_manifest, all_questions = load_benchmark(benchmark, allow_unreviewed=True)
    questions = all_questions[:limit] if limit is not None else all_questions
    if not allow_unreviewed and any(q.get("reviewed") is not True for q in questions):
        raise ValueError("Benchmark contains unreviewed questions; use --allow-unreviewed for a provisional run")
    directory = Path(results_root)
    source_manifest, reusable, source_cache, provenance = None, {}, {}, None
    if reuse_answers_from is not None:
        if Path(reuse_answers_from).resolve() == directory.resolve():
            raise ValueError("Source and destination results directories must differ")
        source_manifest, reusable, source_cache, provenance = load_reusable_results(
            reuse_answers_from, models, all_questions
        )
    vector_count = source_manifest["namespace_vector_count"] if source_manifest else namespace_vector_count(namespace)
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    manifest = {
        "protocol_version": PROTOCOL_VERSION, "benchmark": benchmark_manifest,
        "questions_sha256": hashlib.sha256(json.dumps(all_questions, sort_keys=True).encode()).hexdigest(),
        "namespace": namespace, "index": os.getenv("PINECONE_INDEX_NAME", "aeroquery"),
        "namespace_vector_count": vector_count,
        "azure_endpoint_sha256": hashlib.sha256(endpoint.encode()).hexdigest(),
        "embedding_model": EMBEDDING_MODEL, "judge_model": JUDGE_MODEL,
        "ragas_version": ragas.__version__, "reranker": RERANK_MODEL,
        "candidate_count": CANDIDATE_COUNT, "context_count": CONTEXT_COUNT,
        "models": list(models), "rag_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "bare_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "run_tag": run_tag, "provisional": any(not q.get("reviewed") for q in all_questions),
    }
    if source_manifest:
        for key, value in manifest.items():
            if key not in ("protocol_version", "models") and source_manifest.get(key) != value:
                raise ValueError(f"Source run has incompatible evaluation settings: {key}")
        manifest["reused_answers"] = provenance
        manifest["missing_local_generation"] = {
            "max_output_tokens": LOCAL_MAX_OUTPUT_TOKENS, "initial_temperature": 0,
            "fallback_temperature": LOCAL_FALLBACK_TEMPERATURE,
        }
    elif (directory / "manifest.json").exists():
        existing = json.loads((directory / "manifest.json").read_text())
        if any(existing.get(key) != value for key, value in manifest.items()):
            raise ValueError("Existing results use different evaluation settings or benchmark; choose a different --results-dir")
        manifest = existing
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text()) != manifest:
            raise ValueError("Existing results use different evaluation settings or benchmark; choose a different --results-dir")
    else:
        if (directory / "retrieval_cache.json").exists() or (directory / "all_summaries.json").exists() or any(
            directory.glob("*/bare_results.json")
        ) or any(directory.glob("*/rag_results.json")) or any(directory.glob("*/rag_rerank_results.json")):
            raise ValueError("Existing results have no manifest; choose a different --results-dir")
        write_json(manifest_path, manifest)
    print(f"CAR evaluation run: {directory} ({len(questions)} questions"
          f"{', provisional' if manifest['provisional'] else ''})", flush=True)
    cache_path = directory / "retrieval_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else source_cache
    if source_manifest and not cache_path.exists():
        write_json(cache_path, cache)
    scorers, judge_client, similarity_client = make_scorers()
    azure = azure_client()
    local = OpenAI(api_key="ollama", base_url=OLLAMA_BASE_URL, timeout=120) if LOCAL_MODEL in models else None
    with asyncio.Runner() as runner:
        try:
            results_by_model = {}
            model_dirs = {}
            for model in models:
                slug = re.sub(r"[^a-zA-Z0-9._-]", "-", model)
                model_dir = directory / slug
                model_dir.mkdir(exist_ok=True)
                model_dirs[model] = model_dir
                results = {}
                for config in CONFIGS:
                    path = model_dir / f"{config}_results.json"
                    results[config] = json.loads(path.read_text()) if path.exists() else []
                    if len(results[config]) > len(all_questions) or any(
                        row["question"] != all_questions[i]["question"] for i, row in enumerate(results[config])
                    ):
                        raise ValueError(f"Stored results do not match this benchmark: {path}")
                    if len(results[config]) > len(questions):
                        raise ValueError("Stored results exceed the requested limit; use a larger --limit or a different --results-dir")
                results_by_model[model] = results

            summaries = {}

            def save_completed_summaries():
                for model in models:
                    if model in summaries or any(len(results_by_model[model][config]) < len(questions) for config in CONFIGS):
                        continue
                    summary = summarize(results_by_model[model], model, manifest)
                    write_json(model_dirs[model] / "summary.json", summary)
                    summaries[model] = summary
                    write_json(directory / "all_summaries.json", [summaries[name] for name in models if name in summaries])
                    print(f"Completed {model}; results in {model_dirs[model]}", flush=True)

            generated_answers = {}
            if source_manifest:
                for model in models:
                    generated_answers[model] = {}
                    for config in CONFIGS:
                        path = model_dirs[model] / f"{config}_generated.json"
                        generated = json.loads(path.read_text()) if path.exists() else {}
                        generated_answers[model][config] = generated
                        completed = {row["question"] for row in results_by_model[model][config]}
                        for item in questions:
                            question = item["question"]
                            row = reusable[model][config].get(question)
                            if question in completed or (row and isinstance(row.get("answer"), str)
                                                         and row["answer"].strip()):
                                continue
                            if question in generated:
                                if not isinstance(generated[question].get("answer"), str) or not generated[question]["answer"].strip():
                                    raise ValueError(f"Invalid generated answer checkpoint: {path}")
                                continue
                            if question not in cache:
                                if namespace_vector_count(namespace) != vector_count:
                                    raise ValueError("CAR namespace changed since the source run")
                                cache[question] = get_contexts(
                                    item, cache, namespace, benchmark_manifest["point_in_time"], delay
                                )
                                write_json(cache_path, cache)
                            chunks = None if config == "bare" else cache[question][config]
                            is_local = model == LOCAL_MODEL
                            print(f"{model} {config}: generating missing answer for {question}", flush=True)
                            messages = build_prompt(question, chunks)
                            settings = {}
                            if is_local:
                                settings = {"temperature": 0, "max_tokens": LOCAL_MAX_OUTPUT_TOKENS}
                                try:
                                    answer, tokens = generate(local, model, messages, local=True, **settings)
                                except GenerationLimitError:
                                    settings["temperature"] = LOCAL_FALLBACK_TEMPERATURE
                                    print(f"{model} {config}: retrying with temperature {LOCAL_FALLBACK_TEMPERATURE}",
                                          flush=True)
                                    answer, tokens = generate(local, model, messages, local=True, **settings)
                            else:
                                answer, tokens = generate(azure, model, messages)
                            generated[question] = {"answer": answer, "tokens": tokens}
                            if settings:
                                generated[question]["generation_settings"] = settings
                            write_json(path, generated)

            hosted_workers = concurrency - 1 if local and len(models) > 1 else concurrency
            with ThreadPoolExecutor(max_workers=hosted_workers) as hosted_executor, \
                    ThreadPoolExecutor(max_workers=1) as local_executor:
                futures = {}

                def schedule_next(model, config):
                    rows = results_by_model[model][config]
                    while len(rows) < len(questions):
                        batch = []
                        for item in questions[len(rows):len(rows) + concurrency]:
                            row = reusable.get(model, {}).get(config, {}).get(item["question"])
                            if not row or not isinstance(row.get("answer"), str) or not row["answer"].strip():
                                break
                            batch.append((item, row))
                        if not batch:
                            break
                        print(f"{model} {config} [{len(rows) + 1}-{len(rows) + len(batch)}/{len(questions)}]: "
                              "rescoring saved answers", flush=True)

                        async def score_batch():
                            return await asyncio.gather(*(
                                score_ragas(
                                    {"factual_correctness": scorers["factual_correctness"]},
                                    item["question"], row["answer"], item["ground_truth"], [],
                                ) for item, row in batch
                            ), return_exceptions=True)

                        for (_, row), result in zip(batch, runner.run(score_batch())):
                            if isinstance(result, BaseException):
                                raise result
                            scores, errors = result
                            metric_errors = dict(row.get("metric_errors", {}))
                            metric_errors.pop("factual_correctness", None)
                            metric_errors.update(errors)
                            rows.append({**row, **scores, "metric_errors": metric_errors})
                            write_json(model_dirs[model] / f"{config}_results.json", rows)
                    save_completed_summaries()
                    if len(rows) >= len(questions):
                        return
                    i = len(rows)
                    item = questions[i]
                    if item["question"] in cache:
                        contexts = cache[item["question"]]
                    else:
                        if source_manifest and namespace_vector_count(namespace) != vector_count:
                            raise ValueError("CAR namespace changed since the source run")
                        contexts = get_contexts(item, cache, namespace, benchmark_manifest["point_in_time"], delay)
                        cache[item["question"]] = contexts
                        write_json(cache_path, cache)
                    chunks = None if config == "bare" else contexts[config]
                    is_local = model == LOCAL_MODEL
                    executor = local_executor if is_local else hosted_executor
                    if source_manifest:
                        pending = generated_answers[model][config][item["question"]]
                        future = Future()
                        future.set_result((pending["answer"], pending["tokens"]))
                        print(f"{model} {config} [{i + 1}/{len(questions)}]: scoring generated answer", flush=True)
                    else:
                        print(f"{model} {config} [{i + 1}/{len(questions)}]: generating", flush=True)
                        future = executor.submit(
                            generate, local if is_local else azure, model, build_prompt(item["question"], chunks),
                            local=is_local,
                        )
                    futures[future] = (model, config, item, chunks)

                save_completed_summaries()
                for model in models:
                    for config in CONFIGS:
                        schedule_next(model, config)
                failures = []
                while futures:
                    completed, _ = wait(futures, return_when=FIRST_COMPLETED)
                    for future in completed:
                        model, config, item, chunks = futures.pop(future)
                        try:
                            answer, tokens = future.result()
                        except Exception as error:
                            print(f"  {model} {config}: generation failed: {type(error).__name__}: {error}", flush=True)
                            failures.append((model, config, error))
                            continue
                        print(f"  {model} {config}: scoring", flush=True)
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
                        result_row = {
                            "question": item["question"], "ground_truth": item["ground_truth"],
                            "section": item["section"], "source_url": item["source_url"],
                            "answer": answer, "tokens": tokens, **scores, "metric_errors": errors,
                        }
                        if source_manifest:
                            settings = generated_answers[model][config][item["question"]].get("generation_settings")
                            if settings:
                                result_row["generation_settings"] = settings
                        results_by_model[model][config].append(result_row)
                        write_json(model_dirs[model] / f"{config}_results.json", results_by_model[model][config])
                        print(f"  {model} {config}: checkpoint saved", flush=True)
                        save_completed_summaries()
                        schedule_next(model, config)
                if failures:
                    model, config, error = failures[0]
                    raise RuntimeError(
                        f"{len(failures)} generation call(s) failed; first: {model} {config}. Rerun to resume."
                    ) from error

            all_summaries = [summaries[model] for model in models]
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
    parser.add_argument("--limit", type=int, help="Evaluate the first N questions, resuming existing results")
    parser.add_argument("--cohere-delay", type=float, default=7)
    parser.add_argument("--models", nargs="+", default=[*AZURE_MODELS, LOCAL_MODEL])
    parser.add_argument("--run-tag", default="default", help="Identify the index state; use a different results directory after changing it")
    parser.add_argument("--results-dir", default="eval/results/car")
    parser.add_argument("--reuse-answers-from", help="Rescore saved answers from a current-protocol run into a separate results directory")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="Maximum concurrent answer-generation calls; one slot is reserved for local when selected (default: 4)")
    args = parser.parse_args()
    if args.cohere_delay < 0:
        parser.error("--cohere-delay cannot be negative")
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")
    run_eval(args.benchmark, args.namespace, args.allow_unreviewed, args.limit,
             args.cohere_delay, args.models, args.run_tag, args.results_dir, args.concurrency,
             args.reuse_answers_from)


if __name__ == "__main__":
    main()
