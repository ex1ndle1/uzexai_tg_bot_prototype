"""Snapshot test for the metrics that were read before writing the suite.

``baseline_metrics.json`` was produced from the raw CSV registry with
``tests_independent/build_baseline.py``.  The test fails if the registry, the
documented formulas or the reference computation drift.

It also checks internal consistency, so a grep of the JSON is not required.
"""

from __future__ import annotations

import json
import math
import os
import unittest

from tests_independent import reference as ref

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, "baseline_metrics.json")


class BaselineMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with open(BASELINE, encoding="utf-8") as fh:
            cls.baseline = json.load(fh)["metrics"]
        cls.actual = ref.compute_metrics()

    def assert_nested(self, expected, actual, path: str = "metrics") -> None:
        if isinstance(expected, dict):
            self.assertIsInstance(actual, dict, path)
            self.assertEqual(
                set(expected), set(actual), f"key set changed at {path}"
            )
            for key in expected:
                self.assert_nested(expected[key], actual[key], f"{path}.{key}")
        elif isinstance(expected, float):
            self.assertIsInstance(actual, (int, float), path)
            self.assertTrue(
                math.isclose(float(actual), expected, rel_tol=1e-9, abs_tol=0.01),
                f"{path}: expected {expected!r}, got {actual!r}",
            )
        else:
            self.assertEqual(actual, expected, path)

    def test_recomputed_metrics_match_frozen_baseline(self) -> None:
        self.assert_nested(self.baseline, self.actual)

    def test_metrics_are_internally_consistent(self) -> None:
        dataset = self.actual["dataset"]
        total = dataset["deals_total"]
        self.assertEqual(sum(dataset["deals_by_year"].values()), total)
        self.assertEqual(sum(self.actual["statuses"].values()), total)
        self.assertEqual(sum(self.actual["contract_types"].values()), total)
        self.assertEqual(
            self.actual["statuses"]["Аннулирован(Не оплачен)"],
            self.actual["aggregates"]["annulled_deals"],
        )
        self.assertEqual(
            self.actual["statuses"]["Исполнен"],
            self.actual["aggregates"]["fully_delivered_deals"],
        )
        self.assertEqual(
            self.actual["statuses"]["Исполнен(Частично)"],
            self.actual["aggregates"]["partially_delivered_deals"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
