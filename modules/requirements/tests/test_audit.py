"""自审查测试：覆盖、约束冲突、验收口径三类规则。"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from ideaflow.audit import audit_spec
from ideaflow.drafts import run_groups
from ideaflow.reviews import review_all
from ideaflow.snapshot import build
from ideaflow.spec import build_spec

CASE = Path(__file__).resolve().parents[1] / "cases" / "campus-books.md"


def types(audit: dict) -> list[str]:
    return [row["type"] for row in audit["issues"]]


class AuditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = build(CASE.read_text(encoding="utf-8")).confirm("产品负责人")
        cls.drafts = run_groups(cls.snapshot)
        cls.reviews = review_all(cls.snapshot, cls.drafts)
        cls.spec = build_spec(cls.snapshot, cls.drafts, cls.reviews)

    def clone(self):
        return copy.deepcopy(self.spec)

    def test_baseline_finds_overridden_requirement(self) -> None:
        audit = audit_spec(self.spec, self.snapshot)
        self.assertFalse(audit["passed"])
        self.assertEqual(audit["counts"]["blocking"], 1)
        self.assertEqual(audit["issues"][0]["type"], "REQUIREMENT_OVERRIDDEN")
        self.assertEqual(audit["issues"][0]["requirement_ids"], ["R-03"])
        self.assertEqual(audit["checks"]["coverage"]["with_decision"], 4)
        self.assertEqual(audit["checks"]["constraints"]["verified"], 3)
        self.assertEqual(audit["checks"]["acceptance"]["measurable"], 4)

    def test_clean_spec_passes(self) -> None:
        spec = self.clone()
        decision = next(row for row in spec.decisions if row["requirement_ids"] == ["R-03"])
        decision["satisfies"] = "full"
        decision["needs_human"] = False
        decision["choice"] = "线下当面成交并记录成交状态"
        audit = audit_spec(spec, self.snapshot)
        self.assertTrue(audit["passed"], audit["issues"])

    def test_missing_acceptance_is_blocking(self) -> None:
        spec = self.clone()
        spec.acceptance = [row for row in spec.acceptance if row["requirement_id"] != "R-02"]
        audit = audit_spec(spec, self.snapshot)
        self.assertIn("COVERAGE_MISSING_ACCEPTANCE", types(audit))
        self.assertFalse(audit["passed"])

    def test_missing_module_breaks_coverage_and_mapping(self) -> None:
        spec = self.clone()
        spec.modules = [row for row in spec.modules if row["id"] != "M-02"]
        spec.mapping = [row for row in spec.mapping if row["requirement_id"] != "R-02"]
        audit = audit_spec(spec, self.snapshot)
        self.assertIn("COVERAGE_MISSING_MODULE", types(audit))
        self.assertIn("MAPPING_INCONSISTENT", types(audit))

    def test_deadline_conflict_is_detected(self) -> None:
        spec = self.clone()
        spec.effort["commit_days"] = 20.0
        audit = audit_spec(spec, self.snapshot)
        self.assertIn("CONSTRAINT_CONFLICT", types(audit))
        conflict = next(row for row in audit["issues"]
                        if row["type"] == "CONSTRAINT_CONFLICT")
        self.assertEqual(conflict["severity"], "blocking")
        self.assertEqual(conflict["constraint_ids"], ["C-01"])

    def test_forbidden_content_is_detected(self) -> None:
        spec = self.clone()
        decision = next(row for row in spec.decisions if row["requirement_ids"] == ["R-03"])
        decision["choice"] = "站内支付 + 担保交易，覆盖下单与放款"
        decision["satisfies"] = "full"
        audit = audit_spec(spec, self.snapshot)
        self.assertIn("CONSTRAINT_VIOLATION", types(audit))

    def test_unmeasurable_acceptance_is_flagged(self) -> None:
        spec = self.clone()
        spec.acceptance[0]["criterion"] = "体验要尽快达到预期"
        audit = audit_spec(spec, self.snapshot)
        self.assertIn("ACCEPTANCE_NOT_MEASURABLE", types(audit))
        issue = next(row for row in audit["issues"]
                     if row["type"] == "ACCEPTANCE_NOT_MEASURABLE")
        self.assertEqual(issue["requirement_ids"], ["R-01"])

    def test_unverified_constraint_is_flagged(self) -> None:
        snapshot = copy.deepcopy(self.snapshot)
        snapshot.items.append({
            "id": "C-09", "type": "constraint", "text": "预算不超过两万元",
            "priority": "must", "acceptance": [],
            "source": {"section": "补充约束", "start_line": 0, "end_line": 0,
                       "excerpt": "预算不超过两万元"},
        })
        audit = audit_spec(self.spec, snapshot)
        self.assertIn("CONSTRAINT_UNVERIFIED", types(audit))

    def test_audit_is_repeatable(self) -> None:
        first = audit_spec(self.spec, self.snapshot)
        second = audit_spec(self.spec, self.snapshot)
        self.assertEqual(first["issues"], second["issues"])


if __name__ == "__main__":
    unittest.main()
