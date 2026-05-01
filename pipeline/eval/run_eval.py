import os
import json
import time
import litellm
from openai import OpenAI, AsyncOpenAI

# Increase LiteLLM max tokens to prevent truncation on long RAGAS judgments
litellm.max_tokens = 4096
from dotenv import load_dotenv
from ragas.llms import llm_factory
from ragas.embeddings import OpenAIEmbeddings
from ragas.metrics.collections import (
    Faithfulness,
    ContextPrecision,
    ContextRecall,
    AnswerRelevancy,
    FactualCorrectness,
    SemanticSimilarity,
)
from retrieval.search import search
from generation.prompt import build_prompt

load_dotenv()

# Models to evaluate
LLM_MODELS = [
    # ("gpt-5.4-mini", None),
    # ("gpt-3.5-turbo", None),
    ("llama3.2:1b", "http://host.docker.internal:11434/v1"),
]

# RAGAS judge — GPT-5.4-mini via LiteLLM (handles max_tokens -> max_completion_tokens)
JUDGE_MODEL = "gpt-5.4-mini"

# Metric categories by required kwargs
CONTEXT_METRICS = ["faithfulness", "context_precision", "context_recall"]  # need retrieved_contexts
ANSWER_METRICS = ["answer_relevancy"]  # need user_input + response only
REFERENCE_METRICS = ["factual_correctness", "semantic_similarity"]  # need response + reference


def load_test_set():
    with open("eval/test_set.json") as f:
        return json.load(f)


def get_llm_client(base_url=None):
    if base_url:
        return OpenAI(base_url=base_url, api_key="ollama")
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def generate(messages, model, base_url=None):
    client = get_llm_client(base_url)
    response = client.chat.completions.create(
        model=model, messages=messages, temperature=0,
    )
    return response.choices[0].message.content, (response.usage.total_tokens if response.usage else 0)


RETRIEVAL_CACHE_FILE = "eval/results/retrieval_cache.json"


def cache_retrieval(test_set):
    """Run retrieval once per question, cache to disk for reuse across runs."""
    # Load from disk if exists
    if os.path.exists(RETRIEVAL_CACHE_FILE):
        with open(RETRIEVAL_CACHE_FILE) as f:
            cache = json.load(f)
        # Check if cache matches current test set
        if len(cache) == len(test_set) and all(item["question"] in cache for item in test_set):
            print(f">> Loaded retrieval cache from {RETRIEVAL_CACHE_FILE} ({len(cache)} questions)\n")
            return cache
        print(">> Cache exists but doesn't match test set, re-running retrieval...\n")

    print(">> Caching retrieval results (embed + Pinecone + Cohere)...")
    print("   (Rate-limited for Cohere free tier)\n")
    cache = {}
    for i, item in enumerate(test_set):
        q = item["question"]
        print(f"  [{i+1}/{len(test_set)}] {q[:55]}...")
        no_rerank = search(q, top_k=10, use_rerank=False)
        time.sleep(7)
        reranked = search(q, top_k=10, use_rerank=True, rerank_top_n=5)
        time.sleep(7)
        cache[q] = {"no_rerank": no_rerank, "rerank": reranked}

    # Save to disk
    os.makedirs(os.path.dirname(RETRIEVAL_CACHE_FILE), exist_ok=True)
    with open(RETRIEVAL_CACHE_FILE, "w") as f:
        json.dump(cache, f)
    print(f"\n  Cached {len(cache)} questions to {RETRIEVAL_CACHE_FILE}\n")
    return cache


def score_ragas(scorers, user_input, response, reference, retrieved_contexts):
    """Score a single sample across all RAGAS metrics (sync, no nested async)."""
    scores = {}
    for name, scorer in scorers.items():
        try:
            if name in CONTEXT_METRICS:
                if name == "faithfulness":
                    result = scorer.score(user_input=user_input, response=response, retrieved_contexts=retrieved_contexts)
                else:  # context_precision, context_recall
                    result = scorer.score(user_input=user_input, reference=reference, retrieved_contexts=retrieved_contexts)
            elif name in ANSWER_METRICS:
                result = scorer.score(user_input=user_input, response=response)
            else:  # REFERENCE_METRICS
                result = scorer.score(response=response, reference=reference)
            scores[name] = round(result.value, 4) if result.value is not None else 0.0
        except Exception as e:
            print(f"    Warning: {name} failed: {e}")
            scores[name] = 0.0
    return scores


def run_eval():
    test_set = load_test_set()
    all_summaries = []

    # Set up RAGAS scorers with judge model
    print(f"Setting up RAGAS metrics (judge: {JUDGE_MODEL} via LiteLLM)...\n")
    llm = llm_factory(JUDGE_MODEL, provider="litellm", client=litellm.acompletion)
    emb = OpenAIEmbeddings(client=AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY")), model="text-embedding-3-small")

    scorers = {
        "faithfulness": Faithfulness(llm=llm),
        "context_precision": ContextPrecision(llm=llm),
        "context_recall": ContextRecall(llm=llm),
        "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=emb),
        "factual_correctness": FactualCorrectness(llm=llm),
        "semantic_similarity": SemanticSimilarity(embeddings=emb),
    }

    # Cache retrieval once
    retrieval_cache = cache_retrieval(test_set)

    for llm_model, base_url in LLM_MODELS:
        model_slug = llm_model.replace("/", "-").replace(":", "-")
        results_dir = f"eval/results/{model_slug}"
        os.makedirs(results_dir, exist_ok=True)

        print(f"\n{'#'*60}")
        print(f"# Eval: {llm_model} ({len(test_set)} questions)")
        if base_url:
            print(f"# via {base_url}")
        print(f"{'#'*60}")

        # Load existing results to resume from crash
        def load_existing(filename):
            path = f"{results_dir}/{filename}"
            if os.path.exists(path):
                with open(path) as f:
                    return json.load(f)
            return []

        bare_results = load_existing("bare_results.json")
        rag_results = load_existing("rag_no_rerank_results.json")
        rerank_results = load_existing("rag_rerank_results.json")
        start_idx = min(len(bare_results), len(rag_results), len(rerank_results))

        if start_idx > 0:
            print(f"  Resuming from question {start_idx + 1} ({start_idx} already scored)")

        for i, item in enumerate(test_set):
            if i < start_idx:
                continue
            q = item["question"]
            gt = item["ground_truth"]
            print(f"\n  [{i+1}/{len(test_set)}] {q[:55]}...")

            cached = retrieval_cache[q]

            # --- Bare LLM ---
            bare_messages = [
                {"role": "system", "content": "You are an aviation regulation expert. Answer based on your knowledge of Canadian aviation regulations."},
                {"role": "user", "content": q},
            ]
            bare_answer, bare_tokens = generate(bare_messages, llm_model, base_url)
            bare_scores = score_ragas(
                {k: v for k, v in scorers.items() if k in REFERENCE_METRICS + ANSWER_METRICS},
                user_input=q, response=bare_answer, reference=gt, retrieved_contexts=[],
            )
            bare_results.append({
                "question": q, "ground_truth": gt, "answer": bare_answer,
                **bare_scores, "tokens": bare_tokens,
            })

            # --- RAG (no rerank) ---
            rag_chunks = cached["no_rerank"]
            rag_messages = build_prompt(q, rag_chunks)
            rag_answer, rag_tokens = generate(rag_messages, llm_model, base_url)
            rag_contexts = [c["text"] for c in rag_chunks]
            rag_scores = score_ragas(
                scorers, user_input=q, response=rag_answer,
                reference=gt, retrieved_contexts=rag_contexts,
            )
            rag_results.append({
                "question": q, "ground_truth": gt, "answer": rag_answer,
                **rag_scores, "tokens": rag_tokens,
            })

            # --- RAG + Rerank ---
            rr_chunks = cached["rerank"]
            rr_messages = build_prompt(q, rr_chunks)
            rr_answer, rr_tokens = generate(rr_messages, llm_model, base_url)
            rr_contexts = [c["text"] for c in rr_chunks]
            rr_scores = score_ragas(
                scorers, user_input=q, response=rr_answer,
                reference=gt, retrieved_contexts=rr_contexts,
            )
            rerank_results.append({
                "question": q, "ground_truth": gt, "answer": rr_answer,
                **rr_scores, "tokens": rr_tokens,
            })

            print(f"    bare_fc={bare_scores.get('factual_correctness', 0):.2f}  "
                  f"rag_faith={rag_scores.get('faithfulness', 0):.2f}  "
                  f"rr_faith={rr_scores.get('faithfulness', 0):.2f}")

            # Save after each question (incremental, prevents data loss)
            with open(f"{results_dir}/bare_results.json", "w") as f:
                json.dump(bare_results, f, indent=2)
            with open(f"{results_dir}/rag_no_rerank_results.json", "w") as f:
                json.dump(rag_results, f, indent=2)
            with open(f"{results_dir}/rag_rerank_results.json", "w") as f:
                json.dump(rerank_results, f, indent=2)

        # Aggregate
        all_metric_names = CONTEXT_METRICS + ANSWER_METRICS + REFERENCE_METRICS
        bare_metric_names = ANSWER_METRICS + REFERENCE_METRICS

        def avg(results, metric):
            vals = [r.get(metric, 0) for r in results]
            return round(sum(vals) / len(vals), 3) if vals else 0

        summary = {
            "num_questions": len(test_set),
            "model": llm_model,
            "bare": {m: avg(bare_results, m) for m in bare_metric_names},
            "rag": {m: avg(rag_results, m) for m in all_metric_names},
            "rag_rerank": {m: avg(rerank_results, m) for m in all_metric_names},
        }

        # Print
        print(f"\n{'='*70}")
        print(f"EVAL RESULTS — {llm_model} ({len(test_set)} questions, RAGAS metrics)")
        print(f"{'='*70}")
        print(f"\n{'Metric':<25} {'Bare':>10} {'RAG':>10} {'Rerank':>10}")
        print(f"{'-'*55}")
        for m in all_metric_names:
            bare_val = summary["bare"].get(m, "N/A")
            rag_val = summary["rag"].get(m, "N/A")
            rr_val = summary["rag_rerank"].get(m, "N/A")
            bare_str = f"{bare_val:.3f}" if isinstance(bare_val, float) else bare_val
            rag_str = f"{rag_val:.3f}" if isinstance(rag_val, float) else rag_val
            rr_str = f"{rr_val:.3f}" if isinstance(rr_val, float) else rr_val
            print(f"{m:<25} {bare_str:>10} {rag_str:>10} {rr_str:>10}")
        print()

        # Save
        with open(f"{results_dir}/bare_results.json", "w") as f:
            json.dump(bare_results, f, indent=2)
        with open(f"{results_dir}/rag_no_rerank_results.json", "w") as f:
            json.dump(rag_results, f, indent=2)
        with open(f"{results_dir}/rag_rerank_results.json", "w") as f:
            json.dump(rerank_results, f, indent=2)
        with open(f"{results_dir}/summary.json", "w") as f:
            json.dump(summary, f, indent=2)

        all_summaries.append(summary)
        print(f"Saved to {results_dir}/")

    # Combined
    with open("eval/results/all_summaries.json", "w") as f:
        json.dump(all_summaries, f, indent=2)

    # Final table
    print(f"\n{'#'*70}")
    print(f"# FINAL COMPARISON (RAGAS)")
    print(f"{'#'*70}")
    for m in all_metric_names:
        print(f"\n  {m}:")
        print(f"  {'Model':<20} {'Bare':>8} {'RAG':>8} {'Rerank':>8}")
        print(f"  {'-'*44}")
        for s in all_summaries:
            bare_v = s["bare"].get(m, "—")
            rag_v = s["rag"].get(m, "—")
            rr_v = s["rag_rerank"].get(m, "—")
            b = f"{bare_v:.3f}" if isinstance(bare_v, float) else str(bare_v)
            r = f"{rag_v:.3f}" if isinstance(rag_v, float) else str(rag_v)
            rr = f"{rr_v:.3f}" if isinstance(rr_v, float) else str(rr_v)
            print(f"  {s['model']:<20} {b:>8} {r:>8} {rr:>8}")
    print()


if __name__ == "__main__":
    run_eval()
