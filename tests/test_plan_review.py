import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "orchestrator" / "python"))

from plan_adapter import plan_from_spec  # noqa: E402

SPEC = {
    "spec_id": "SPEC-REVIEW",
    "version": 1,
    "modules": [
        {"id": "M-01", "name": "图书列表", "responsibility": "对外提供列表",
         "requirement_ids": ["R-01"], "decision_ids": [], "interfaces": ["GET /api/books"],
         "estimate_days": 2, "deliverable": "列表接口", "acceptance_items": ["可分页"],
         "depends_on": [], "write_paths": ["api/"], "suggested_role": "ai"},
        {"id": "M-02", "name": "预约接口", "responsibility": "提供预约",
         "requirement_ids": ["R-02"], "decision_ids": [], "interfaces": ["POST /api/bookings"],
         "estimate_days": 2, "deliverable": "预约接口", "acceptance_items": ["可创建"],
         "depends_on": ["M-01"], "write_paths": ["api/"], "suggested_role": "ai"},
    ],
}


def make_plan() -> dict:
    return json.loads(json.dumps(plan_from_spec(SPEC, plan_id="PLAN-REVIEW")))


def codes(review: dict) -> set[str]:
    return {row["code"] for row in review["issues"]}


class PlanReviewTest(unittest.TestCase):
    """计划评审：模块覆盖、任务粒度、依赖合法性。"""

    def test_missing_module_coverage_is_reported(self):
        from plan_review import review_plan

        plan = make_plan()
        plan["tasks"] = [task for task in plan["tasks"] if task["task_id"] != "T2"]
        review = review_plan(SPEC, plan)
        self.assertIn("MODULE_WITHOUT_TASK", codes(review))
        missing = [row for row in review["issues"] if row["code"] == "MODULE_WITHOUT_TASK"]
        self.assertEqual(missing[0]["module_id"], "M-02")

    def test_task_granularity_is_flagged(self):
        from plan_review import review_plan

        plan = make_plan()
        plan["tasks"][0]["estimate_days"] = 10
        review = review_plan(SPEC, plan)
        self.assertIn("TASK_TOO_LARGE", codes(review))

    def test_dangling_dependency_is_flagged(self):
        from plan_review import review_plan

        plan = make_plan()
        plan["tasks"][1]["depends_on"] = ["T9"]
        review = review_plan(SPEC, plan)
        self.assertIn("DEPENDENCY_INVALID", codes(review))

    def test_cycle_is_flagged(self):
        from plan_review import review_plan

        plan = make_plan()
        plan["tasks"][0]["depends_on"] = ["T2"]
        plan["tasks"][1]["depends_on"] = ["T1"]
        review = review_plan(SPEC, plan)
        self.assertIn("DEPENDENCY_CYCLE", codes(review))

    def test_clean_plan_has_no_issues(self):
        from plan_review import review_plan

        review = review_plan(SPEC, make_plan())
        self.assertEqual(review["issues"], [])
        self.assertEqual(review["plan_id"], "PLAN-REVIEW")
        self.assertEqual(review["spec_ref"]["spec_id"], "SPEC-REVIEW")


if __name__ == "__main__":
    unittest.main()
