"""Inspect low-scoring CAR evaluation answers; classifications are hypotheses."""

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path


def classify_failures(results, cache):
    failures, skipped = [], 0
    for row in results:
        factual = row.get("factual_correctness")
        if not isinstance(factual, (int, float)) or not math.isfinite(factual):
            skipped += 1
            continue
        if factual > 0:
            continue
        recall, faith = row.get("context_recall"), row.get("faithfulness")
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (recall, faith)):
            mode = "INSUFFICIENT_METRICS"
        elif "i don't have enough information" in row["answer"].lower():
            mode = "POSSIBLE_FALSE_REFUSAL" if recall >= 0.8 else "POSSIBLE_RETRIEVAL_MISS"
        elif recall < 0.5:
            mode = "POSSIBLE_RETRIEVAL_MISS"
        elif faith < 0.5:
            mode = "POSSIBLE_UNGROUNDED_ANSWER"
        else:
            mode = "REVIEW_ANSWER_REFERENCE_AND_JUDGE"
        chunks = cache.get(row["question"], {}).get("rag_rerank", [])
        failures.append({"question": row["question"], "mode": mode, "answer": row["answer"],
                         "ground_truth": row["ground_truth"], "source_url": row["source_url"],
                         "sections": [chunk["section"] for chunk in chunks]})
    return failures, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="CAR run directory containing manifest.json")
    parser.add_argument("--model", default="gpt-5.4-mini")
    args = parser.parse_args()
    directory = Path(args.run)
    slug = re.sub(r"[^a-zA-Z0-9._-]", "-", args.model)
    results = json.loads((directory / slug / "rag_rerank_results.json").read_text())
    cache = json.loads((directory / "retrieval_cache.json").read_text())
    failures, skipped = classify_failures(results, cache)
    print(f"{args.model}: {len(failures)} zero factual-correctness scores; {skipped} missing scores")
    print("Categories are review hypotheses, not confirmed causes.")
    for mode, count in sorted(Counter(item["mode"] for item in failures).items()):
        print(f"  {mode}: {count}")
    for item in failures:
        print(f"\n{item['mode']}: {item['question']}\n  Source: {item['source_url']}")
        print(f"  Retrieved sections: {', '.join(item['sections'])}")
        print(f"  Reference: {item['ground_truth']}\n  Answer: {item['answer']}")


if __name__ == "__main__":
    main()
