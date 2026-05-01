import os
import json
from openai import OpenAI
from dotenv import load_dotenv
from retrieval.search import search
from generation.prompt import build_prompt

load_dotenv()

# Each model config: (model_name, base_url or None for default OpenAI)
LLM_MODELS = [
    ("gpt-5.4-mini", None),
    ("gpt-3.5-turbo", None),
    ("llama3.2:1b", "http://host.docker.internal:11434/v1"),  # Ollama (same as WebLLM)
]
JUDGE_MODEL = "gpt-5.4-mini"  # Always use a strong model for judging


def load_test_set():
    with open("eval/test_set.json") as f:
        return json.load(f)


def get_llm_client(base_url=None):
    if base_url:
        return OpenAI(base_url=base_url, api_key="ollama")
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def generate(messages, model, base_url=None):
    """Generate a response from any model."""
    client = get_llm_client(base_url)
    response = client.chat.completions.create(
        model=model, messages=messages, temperature=0,
    )
    return response.choices[0].message.content, (response.usage.total_tokens if response.usage else 0)


def judge_faithfulness(answer, contexts, client):
    """LLM-as-judge: is the answer grounded in the provided context?"""
    context_text = "\n---\n".join(contexts)
    response = client.chat.completions.create(
        model=JUDGE_MODEL,
        messages=[
            {"role": "system", "content": "You are an evaluation judge. Score whether the answer is fully grounded in the provided context. Return ONLY a JSON object with 'score' (0.0 to 1.0) and 'reason' (one sentence)."},
            {"role": "user", "content": f"Context:\n{context_text}\n\nAnswer:\n{answer}"},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    try:
        return json.loads(response.choices[0].message.content)
    except json.JSONDecodeError:
        return {"score": 0.0, "reason": "Failed to parse judge response"}


def judge_correctness(answer, ground_truth, client):
    """LLM-as-judge: does the answer match the ground truth?"""
    response = client.chat.completions.create(
        model=JUDGE_MODEL,
        messages=[
            {"role": "system", "content": "You are an evaluation judge. Score whether the answer correctly addresses the key points in the ground truth. Return ONLY a JSON object with 'score' (0.0 to 1.0) and 'reason' (one sentence)."},
            {"role": "user", "content": f"Ground Truth:\n{ground_truth}\n\nAnswer:\n{answer}"},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    try:
        return json.loads(response.choices[0].message.content)
    except json.JSONDecodeError:
        return {"score": 0.0, "reason": "Failed to parse judge response"}


def cache_retrieval(test_set):
    """Run retrieval once per question, cache chunks for reuse across models."""
    print(">> Caching retrieval results (embed + Pinecone + Cohere)...")
    cache = {}
    for i, item in enumerate(test_set):
        q = item["question"]
        print(f"  [{i+1}/{len(test_set)}] {q[:55]}...")
        no_rerank = search(q, top_k=10, use_rerank=False)
        reranked = search(q, top_k=10, use_rerank=True, rerank_top_n=5)
        cache[q] = {"no_rerank": no_rerank, "rerank": reranked}
    print(f"  Cached {len(cache)} questions\n")
    return cache


def run_eval():
    judge_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    test_set = load_test_set()
    all_summaries = []

    # Cache retrieval once — reuse across all models
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

        bare_results = []
        rag_results = []
        rerank_results = []

        for i, item in enumerate(test_set):
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
            bare_correct = judge_correctness(bare_answer, gt, judge_client)
            bare_results.append({
                "question": q, "ground_truth": gt, "answer": bare_answer,
                "correctness": bare_correct["score"],
            })

            # --- RAG (no rerank) ---
            rag_chunks = cached["no_rerank"]
            rag_messages = build_prompt(q, rag_chunks)
            rag_answer, rag_tokens = generate(rag_messages, llm_model, base_url)
            rag_contexts = [c["text"] for c in rag_chunks]
            rag_faith = judge_faithfulness(rag_answer, rag_contexts, judge_client)
            rag_correct = judge_correctness(rag_answer, gt, judge_client)
            rag_results.append({
                "question": q, "ground_truth": gt, "answer": rag_answer,
                "faithfulness": rag_faith["score"], "correctness": rag_correct["score"],
                "tokens": rag_tokens,
            })

            # --- RAG + Rerank ---
            rr_chunks = cached["rerank"]
            rr_messages = build_prompt(q, rr_chunks)
            rr_answer, rr_tokens = generate(rr_messages, llm_model, base_url)
            rr_contexts = [c["text"] for c in rr_chunks]
            rr_faith = judge_faithfulness(rr_answer, rr_contexts, judge_client)
            rr_correct = judge_correctness(rr_answer, gt, judge_client)
            rerank_results.append({
                "question": q, "ground_truth": gt, "answer": rr_answer,
                "faithfulness": rr_faith["score"], "correctness": rr_correct["score"],
                "tokens": rr_tokens,
            })

            print(f"    bare={bare_correct['score']:.1f}  rag={rag_correct['score']:.1f}  rerank={rr_correct['score']:.1f}")

        # Aggregate
        bare_avg = round(sum(r["correctness"] for r in bare_results) / len(bare_results), 3)
        rag_avg = round(sum(r["correctness"] for r in rag_results) / len(rag_results), 3)
        rag_faith_avg = round(sum(r["faithfulness"] for r in rag_results) / len(rag_results), 3)
        rr_avg = round(sum(r["correctness"] for r in rerank_results) / len(rerank_results), 3)
        rr_faith_avg = round(sum(r["faithfulness"] for r in rerank_results) / len(rerank_results), 3)

        print(f"\n{'='*60}")
        print(f"EVAL RESULTS — {llm_model} ({len(test_set)} questions)")
        print(f"{'='*60}")
        print(f"\n{'Config':<30} {'Correct':>10} {'Faithful':>10}")
        print(f"{'-'*50}")
        print(f"{'Bare LLM':<30} {bare_avg:>10} {'N/A':>10}")
        print(f"{'RAG (no rerank)':<30} {rag_avg:>10} {rag_faith_avg:>10}")
        print(f"{'RAG + Rerank':<30} {rr_avg:>10} {rr_faith_avg:>10}")
        print()

        # Save
        with open(f"{results_dir}/bare_results.json", "w") as f:
            json.dump(bare_results, f, indent=2)
        with open(f"{results_dir}/rag_no_rerank_results.json", "w") as f:
            json.dump(rag_results, f, indent=2)
        with open(f"{results_dir}/rag_rerank_results.json", "w") as f:
            json.dump(rerank_results, f, indent=2)

        summary = {
            "num_questions": len(test_set),
            "model": llm_model,
            "bare_correctness": bare_avg,
            "rag_correctness": rag_avg,
            "rag_faithfulness": rag_faith_avg,
            "rag_rerank_correctness": rr_avg,
            "rag_rerank_faithfulness": rr_faith_avg,
        }
        with open(f"{results_dir}/summary.json", "w") as f:
            json.dump(summary, f, indent=2)

        all_summaries.append(summary)
        print(f"Detailed results saved to {results_dir}/")

    # Save combined
    with open("eval/results/all_summaries.json", "w") as f:
        json.dump(all_summaries, f, indent=2)

    # Final comparison
    print(f"\n{'#'*60}")
    print(f"# FINAL COMPARISON")
    print(f"{'#'*60}")
    print(f"\n{'Model':<20} {'Bare':>8} {'RAG':>8} {'Rerank':>8} {'Faith':>8}")
    print(f"{'-'*52}")
    for s in all_summaries:
        print(f"{s['model']:<20} {s['bare_correctness']:>8} {s['rag_correctness']:>8} {s['rag_rerank_correctness']:>8} {s['rag_rerank_faithfulness']:>8}")
    print()


if __name__ == "__main__":
    run_eval()
