import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

from metrics import coverage, invalid_dependencies, summarize  # noqa: E402

CASE = {
    "case": "demo",
    "must_have": [
        {"id": "M1", "text": "发布", "keywords": ["发布"], "severity": "severe"},
        {"id": "M2", "text": "搜索", "keywords": ["搜索"], "severity": "severe"},
        {"id": "C1", "text": "不做支付", "keywords": ["不做支付"], "severity": "normal"},
    ],
}


class CoverageTest(unittest.TestCase):
    def test_all_covered(self):
        result = coverage(["学生可以发布教材", "支持搜索", "首版不做支付"], CASE["must_have"])
        self.assertEqual(result["rate"], 1.0)
        self.assertEqual(result["missing_ids"], [])

    def test_severe_missing_listed(self):
        result = coverage(["学生可以发布教材", "首版不做支付"], CASE["must_have"])
        self.assertEqual(result["missing_ids"], ["M2"])
        self.assertEqual(result["severe_missing_ids"], ["M2"])

    def test_keywords_must_all_match(self):
        item = [{"id": "X", "text": "两周两名", "keywords": ["两周", "两名"]}]
        self.assertEqual(coverage(["两周内完成"], item)["missing_ids"], ["X"])


class DependencyTest(unittest.TestCase):
    def test_clean_chain(self):
        tasks = [{"task_id": "T1"}, {"task_id": "T2", "depends_on": ["T1"]}]
        self.assertEqual(invalid_dependencies(tasks), [])

    def test_unknown_reference(self):
        tasks = [{"task_id": "T1", "depends_on": ["T9"]}]
        self.assertIn("依赖不存在的任务 T9", invalid_dependencies(tasks)[0])

    def test_self_dependency(self):
        tasks = [{"task_id": "T1", "depends_on": ["T1"]}]
        self.assertIn("依赖自身", invalid_dependencies(tasks)[0])

    def test_cycle_detected(self):
        tasks = [{"task_id": "T1", "depends_on": ["T2"]},
                 {"task_id": "T2", "depends_on": ["T1"]}]
        problems = invalid_dependencies(tasks)
        self.assertTrue(any("循环依赖" in problem for problem in problems))


class SummarizeTest(unittest.TestCase):
    def test_report_shape(self):
        row = summarize(CASE, ["发布", "搜索"], [{"task_id": "T1"}],
                        duration_s=1.2345, tokens=120, human_interventions=2,
                        mode="multi")
        self.assertEqual(row["requirements"]["coverage_rate"], 0.667)
        self.assertEqual(row["run"]["tokens"], 120)
        self.assertEqual(row["plan"]["tasks"], 1)
        self.assertEqual(row["plan"]["invalid_dependencies"], [])


if __name__ == "__main__":
    unittest.main()
