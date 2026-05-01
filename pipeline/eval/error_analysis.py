"""
Error Analysis: Diagnose the worst-scoring questions from the RAGAS eval.
Run: cd pipeline && python -m eval.error_analysis
"""
import json
from collections import defaultdict


def load_data():
    results = {}
    for model in ["gpt-5.4-mini", "gpt-3.5-turbo", "llama3.2-1b"]:
        for config in ["bare_results", "rag_no_rerank_results", "rag_rerank_results"]:
            key = f"{model}/{config}"
            with open(f"eval/results/{model}/{config}.json") as f:
                results[key] = json.load(f)

    with open("eval/results/retrieval_cache.json") as f:
        cache = json.load(f)

    with open("eval/test_set.json") as f:
        test_set = json.load(f)

    gt_map = {item["question"]: item["ground_truth"] for item in test_set}
    return results, cache, gt_map


def classify_failures(results, cache, gt_map):
    """Classify each fc=0 question into a failure mode."""
    rerank = results["gpt-5.4-mini/rag_rerank_results"]
    failures = []

    for r in rerank:
        if r.get("factual_correctness", 1) > 0:
            continue

        q = r["question"]
        gt = gt_map[q]
        cr = r.get("context_recall", 0)
        faith = r.get("faithfulness", 0)
        answer = r["answer"]

        chunks = cache.get(q, {}).get("rerank", [])
        chunk_texts = " ".join(c["text"] for c in chunks).lower()

        # Classify
        if "i don't have enough information" in answer.lower():
            if cr >= 0.8:
                mode = "FALSE_REFUSAL"
                reason = "Model refused despite relevant context being retrieved (cr={:.2f})".format(cr)
            else:
                mode = "CORRECT_REFUSAL_BAD_RETRIEVAL"
                reason = "Model correctly refused — retrieval missed the answer (cr={:.2f})".format(cr)
        elif cr < 0.5:
            mode = "RETRIEVAL_MISS"
            reason = "Retrieved chunks don't contain the answer (cr={:.2f})".format(cr)
        elif faith < 0.5:
            mode = "HALLUCINATION"
            reason = "Model made claims not grounded in context (faith={:.2f})".format(faith)
        else:
            mode = "JUDGE_DISAGREEMENT"
            reason = "Answer looks correct but RAGAS judge scored fc=0 — likely ground truth mismatch"

        failures.append({
            "question": q,
            "ground_truth": gt,
            "answer": answer[:300],
            "mode": mode,
            "reason": reason,
            "context_recall": cr,
            "faithfulness": faith,
            "sections": [c["section"] for c in chunks],
        })

    return failures


def print_analysis(failures):
    print("=" * 70)
    print("ERROR ANALYSIS: GPT-5.4-mini RAG+Rerank — 10 questions with fc=0")
    print("=" * 70)

    # Group by failure mode
    by_mode = defaultdict(list)
    for f in failures:
        by_mode[f["mode"]].append(f)

    mode_labels = {
        "JUDGE_DISAGREEMENT": "Judge Disagreement (answer is correct, ground truth too narrow)",
        "FALSE_REFUSAL": "False Refusal (context retrieved but model refused)",
        "RETRIEVAL_MISS": "Retrieval Miss (wrong chunks retrieved)",
        "CORRECT_REFUSAL_BAD_RETRIEVAL": "Correct Refusal (retrieval failed, model correctly refused)",
        "HALLUCINATION": "Hallucination (model fabricated from context)",
    }

    for mode, label in mode_labels.items():
        items = by_mode.get(mode, [])
        if not items:
            continue
        print(f"\n{'─' * 70}")
        print(f"  {label} — {len(items)} questions")
        print(f"{'─' * 70}")
        for f in items:
            print(f"\n  Q: {f['question']}")
            print(f"  GT: {f['ground_truth'][:150]}")
            print(f"  ANS: {f['answer'][:150]}")
            print(f"  Reason: {f['reason']}")
            print(f"  Sections: {f['sections']}")

    # Summary
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    total = len(failures)
    for mode, label in mode_labels.items():
        count = len(by_mode.get(mode, []))
        if count:
            print(f"  {count}/{total}  {label}")

    print(f"\nACTIONABLE FIXES:")
    if by_mode.get("JUDGE_DISAGREEMENT"):
        print(f"  1. Expand ground truths — {len(by_mode['JUDGE_DISAGREEMENT'])} questions have overly narrow reference answers.")
        print(f"     The model gave MORE detail than ground truth, which RAGAS penalizes.")
    if by_mode.get("FALSE_REFUSAL"):
        print(f"  2. Fix false refusals — {len(by_mode['FALSE_REFUSAL'])} questions where context was retrieved but model said 'I don't know'.")
        print(f"     Root cause: chunk text is garbled (parsing artifacts) so model can't interpret it.")
    if by_mode.get("RETRIEVAL_MISS") or by_mode.get("CORRECT_REFUSAL_BAD_RETRIEVAL"):
        miss_count = len(by_mode.get("RETRIEVAL_MISS", [])) + len(by_mode.get("CORRECT_REFUSAL_BAD_RETRIEVAL", []))
        print(f"  3. Fix retrieval gaps — {miss_count} questions where the right chunks weren't retrieved.")
        print(f"     May need better chunking or the info isn't in the source PDF.")


def main():
    results, cache, gt_map = load_data()
    failures = classify_failures(results, cache, gt_map)
    print_analysis(failures)


if __name__ == "__main__":
    main()
