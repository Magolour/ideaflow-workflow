"""取舍记录测试：案例里两组方案必须有实际分歧与可核对的代价。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ideaflow.contracts import ContractError
from ideaflow.demo import run_case


class CaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "case"
        cls.result = run_case(out_dir=cls.root, confirm="案例演示")
        cls.tradeoffs = cls.result.tradeoffs
        cls.rows = {row["requirement_id"]: row for row in cls.tradeoffs["items"]}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_case_has_four_tradeoffs(self) -> None:
        self.assertEqual(self.tradeoffs["counts"]["total"], 4)
        self.assertEqual([row["id"] for row in self.tradeoffs["items"]],
                         ["TRD-01", "TRD-02", "TRD-03", "TRD-04"])
        self.assertEqual(self.tradeoffs["counts"]["requirement_vs_schedule"], 1)
        self.assertEqual(self.tradeoffs["counts"]["experience_vs_schedule"], 3)

    def test_payment_is_a_real_conflict(self) -> None:
        row = self.rows["R-03"]
        self.assertEqual(row["tension"], "requirement_vs_schedule")
        self.assertEqual(row["a"]["satisfies"], "declined")
        self.assertEqual(row["b"]["satisfies"], "full")
        self.assertGreater(row["delta_days"], 0)
        self.assertEqual(self.tradeoffs["headline"], row["id"])

    def test_search_shows_cost_difference(self) -> None:
        row = self.rows["R-02"]
        self.assertEqual(row["tension"], "experience_vs_schedule")
        self.assertEqual(row["a"]["satisfies"], "full")
        self.assertEqual(row["b"]["satisfies"], "full")
        self.assertGreater(row["delta_days"], 0)

    def test_project_level_deadline_comparison(self) -> None:
        project = self.tradeoffs["project"]
        self.assertEqual(project["deadline_days"], 14)
        self.assertTrue(project["a_within_deadline"])
        self.assertFalse(project["b_within_deadline"])
        self.assertLess(project["a_days"], project["b_days"])

    def test_every_row_keeps_source(self) -> None:
        for row in self.tradeoffs["items"]:
            self.assertGreaterEqual(row["source"]["start_line"], 1)
            self.assertTrue(row["summary"])

    def test_tradeoffs_are_delivered(self) -> None:
        path = self.root / "tradeoffs.json"
        self.assertTrue(path.exists())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["headline"], self.tradeoffs["headline"])
        self.assertIn("tradeoffs.json", self.result.manifest["files"])
        extras = {row["file"] for row in self.result.manifest["extras"]}
        self.assertIn("tradeoffs.json", extras)

    def test_repeat_case_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            again = run_case(out_dir=Path(other) / "case", confirm="案例演示")
            self.assertEqual(json.dumps(again.tradeoffs, ensure_ascii=False, sort_keys=True),
                             json.dumps(self.tradeoffs, ensure_ascii=False, sort_keys=True))

    def test_missing_case_is_reported(self) -> None:
        with self.assertRaises(ContractError) as caught:
            run_case(case_file="cases/not-exist.md")
        self.assertEqual(caught.exception.code, "CASE_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
