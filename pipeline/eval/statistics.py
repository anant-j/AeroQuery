import math
import random


BOOTSTRAP_SAMPLES = 2000


def _finite_values(values):
    return [
        float(value)
        for value in values
        if isinstance(value, (int, float)) and math.isfinite(value)
    ]


def _confidence_interval(values, seed):
    rng = random.Random(seed)
    sample_count = len(values)
    means = sorted(
        sum(rng.choices(values, k=sample_count)) / sample_count
        for _ in range(BOOTSTRAP_SAMPLES)
    )
    lower = means[int(0.025 * (BOOTSTRAP_SAMPLES - 1))]
    upper = means[int(0.975 * (BOOTSTRAP_SAMPLES - 1))]
    return round(lower, 4), round(upper, 4)


def metric_summary(values, seed=2026):
    """Return a mean and percentile-bootstrap interval for valid scores."""
    valid_values = _finite_values(values)
    if not valid_values:
        return {
            "n": 0,
            "mean": None,
            "standard_deviation": None,
            "ci95": None,
        }

    mean = sum(valid_values) / len(valid_values)
    standard_deviation = (
        math.sqrt(sum((value - mean) ** 2 for value in valid_values) / (len(valid_values) - 1))
        if len(valid_values) > 1
        else None
    )
    return {
        "n": len(valid_values),
        "mean": round(mean, 3),
        "standard_deviation": round(standard_deviation, 4) if standard_deviation is not None else None,
        "ci95": list(_confidence_interval(valid_values, seed)) if len(valid_values) > 1 else None,
    }


def paired_metric_summary(left_results, right_results, metric, seed=2026):
    """Summarize right-minus-left scores paired by question."""
    left_by_question = {result["question"]: result for result in left_results}
    right_by_question = {result["question"]: result for result in right_results}
    shared_questions = sorted(left_by_question.keys() & right_by_question.keys())
    differences = [
        right_by_question[question].get(metric) - left_by_question[question].get(metric)
        for question in shared_questions
        if isinstance(left_by_question[question].get(metric), (int, float))
        and math.isfinite(left_by_question[question][metric])
        and isinstance(right_by_question[question].get(metric), (int, float))
        and math.isfinite(right_by_question[question][metric])
    ]
    summary = metric_summary(differences, seed=seed)
    summary["mean_difference"] = summary.pop("mean")
    return summary
