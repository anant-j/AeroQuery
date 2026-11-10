import unittest

from eval.statistics import metric_summary, paired_metric_summary


class MetricSummaryTests(unittest.TestCase):
    def test_excludes_missing_and_non_finite_scores(self):
        summary = metric_summary([0.2, None, 0.8, float("nan")])

        self.assertEqual(summary["n"], 2)
        self.assertEqual(summary["mean"], 0.5)
        self.assertEqual(summary["ci95"], [0.2, 0.8])

    def test_empty_scores_have_no_mean_or_interval(self):
        self.assertEqual(
            metric_summary([None]),
            {"n": 0, "mean": None, "standard_deviation": None, "ci95": None},
        )

    def test_paired_summary_uses_only_shared_valid_questions(self):
        left = [
            {"question": "first", "score": 0.2},
            {"question": "second", "score": None},
            {"question": "unpaired", "score": 1.0},
        ]
        right = [
            {"question": "first", "score": 0.7},
            {"question": "second", "score": 0.9},
        ]

        summary = paired_metric_summary(left, right, "score")

        self.assertEqual(summary["n"], 1)
        self.assertEqual(summary["mean_difference"], 0.5)
        self.assertIsNone(summary["ci95"])


if __name__ == "__main__":
    unittest.main()
