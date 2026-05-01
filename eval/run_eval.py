import os
import json
from openai import OpenAI
from dotenv import load_dotenv
from generation.rag import ask

load_dotenv()

LLM_MODEL = "gpt-3.5-turbo"  # Change this to run eval with a different model
JUDGE_MODEL = "gpt-5.4-mini"  # Always use a strong model for judging


def load_test_set():
    with open("eval/test_set.json") as f:
        return json.load(f)


def ask_bare_llm(question, client):
    """Ask the LLM directly without any retrieval — baseline comparison."""
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": "You are an aviation regulation expert. Answer the question based on your knowledge of Canadian aviation regulations."},
            {"role": "user", "content": question},
        ],
        temperature=0,
    )
    return response.choices[0].message.content


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


def run_eval():
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    test_set = load_test_set()

    rag_results = []
    bare_results = []

    print(f"Running eval on {len(test_set)} questions...\n")

    for i, item in enumerate(test_set):
        q = item["question"]
        gt = item["ground_truth"]
        print(f"[{i+1}/{len(test_set)}] {q[:60]}...")

        # RAG pipeline
        rag = ask(q, model=LLM_MODEL)
        rag_contexts = [c["text"] for c in rag["chunks"]]
        rag_faith = judge_faithfulness(rag["answer"], rag_contexts, client)
        rag_correct = judge_correctness(rag["answer"], gt, client)

        rag_results.append({
            "question": q,
            "ground_truth": gt,
            "answer": rag["answer"],
            "faithfulness": rag_faith["score"],
            "correctness": rag_correct["score"],
            "tokens": rag["tokens"],
        })

        # Bare LLM (no retrieval)
        bare_answer = ask_bare_llm(q, client)
        bare_correct = judge_correctness(bare_answer, gt, client)

        bare_results.append({
            "question": q,
            "ground_truth": gt,
            "answer": bare_answer,
            "correctness": bare_correct["score"],
        })

    # Aggregate scores
    rag_faith_avg = sum(r["faithfulness"] for r in rag_results) / len(rag_results)
    rag_correct_avg = sum(r["correctness"] for r in rag_results) / len(rag_results)
    bare_correct_avg = sum(r["correctness"] for r in bare_results) / len(bare_results)

    print(f"\n{'='*60}")
    print(f"EVAL RESULTS ({len(test_set)} questions)")
    print(f"{'='*60}")
    print(f"\n{'Metric':<25} {'RAG':>10} {'Bare LLM':>10}")
    print(f"{'-'*45}")
    print(f"{'Correctness':<25} {rag_correct_avg:>10.2f} {bare_correct_avg:>10.2f}")
    print(f"{'Faithfulness':<25} {rag_faith_avg:>10.2f} {'N/A':>10}")
    print()

    # Save detailed results per model
    model_slug = LLM_MODEL.replace("/", "-")
    results_dir = f"eval/results/{model_slug}"
    os.makedirs(results_dir, exist_ok=True)
    with open(f"{results_dir}/rag_results.json", "w") as f:
        json.dump(rag_results, f, indent=2)
    with open(f"{results_dir}/bare_results.json", "w") as f:
        json.dump(bare_results, f, indent=2)

    summary = {
        "num_questions": len(test_set),
        "model": LLM_MODEL,
        "rag_correctness": round(rag_correct_avg, 3),
        "rag_faithfulness": round(rag_faith_avg, 3),
        "bare_correctness": round(bare_correct_avg, 3),
    }
    with open(f"{results_dir}/summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"Detailed results saved to {results_dir}/")


if __name__ == "__main__":
    run_eval()
