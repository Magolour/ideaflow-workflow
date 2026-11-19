"""A/B 草案测试：覆盖率、隔离、取舍与契约校验。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from ideaflow.constraints import deadline_days, forbidden_subjects, headcount
from ideaflow.contracts import ContractError
from ideaflow.drafts import build_draft, run_groups, validate_draft
from ideaflow.snapshot import build

CASE = Path(__file__).resolve().parents[1] / "cases" / "campus-books.md"


class GroupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = build(CASE.read_text(encoding="utf-8")).confirm("产品负责人")
        cls.drafts = run_groups(cls.snapshot)

    def test_each_group_covers_every_requirement(self) -> None:
        required = self.snapshot.ids("requirement")
        for draft in self.drafts.values():
            covered = {decision["requirement_ids"][0] for decision in draft.decisions}
            self.assertEqual(covered, required)

    def test_groups_take_opposite_positions_on_payment(self) -> None:
        payment = "R-03"
        a = self.drafts["A"].decision_for(payment)
        b = self.drafts["B"].decision_for(payment)
        self.assertEqual(a["satisfies"], "declined")
        self.assertEqual(b["satisfies"], "full")
        self.assertNotEqual(a["choice"], b["choice"])
        self.assertIn("线下", a["choice"])
        self.assertIn("支付", b["choice"])

    def test_estimates_differ_and_a_fits_the_deadline(self) -> None:
        a, b = self.drafts["A"], self.drafts["B"]
        self.assertLess(a.commit_days, b.commit_days)
        self.assertLessEqual(a.commit_days, 14)
        self.assertGreater(b.commit_days, 14)

    def test_b_reports_constraint_conflicts(self) -> None:
        conflicts = {check["constraint_id"]
                     for check in self.drafts["B"].constraints_checked
                     if not check["complies"]}
        self.assertIn("C-01", conflicts)  # 超出两周
        self.assertIn("C-03", conflicts)  # 与“不做在线支付”冲突
        self.assertFalse([check for check in self.drafts["A"].constraints_checked
                          if not check["complies"]])

    def test_claims_cite_requirements_and_constraints(self) -> None:
        for draft in self.drafts.values():
            self.assertTrue(draft.claims)
            for claim in draft.claims:
                for item_id in claim.get("requirement_ids", []) + claim.get("constraint_ids", []):
                    self.assertIn(item_id, self.snapshot.ids())
            self.assertEqual(draft.claims[2]["constraint_ids"], ["C-01"])

    def test_hash_is_stable_and_group_specific(self) -> None:
        again = run_groups(self.snapshot)
        self.assertEqual(again["A"].hash, self.drafts["A"].hash)
        self.assertNotEqual(self.drafts["A"].hash, self.drafts["B"].hash)

    def test_contexts_are_isolated(self) -> None:
        captured: dict[str, dict] = {}

        def capture(group: str, context: dict) -> None:
            captured[group] = context

        run_groups(self.snapshot, on_context=capture)
        self.assertEqual(set(captured), {"A", "B"})
        self.assertNotEqual(captured["A"]["objective"], captured["B"]["objective"])
        self.assertEqual(set(captured["A"]), {"group", "objective", "posture", "rules", "snapshot"})
        self.assertNotIn("DRAFT-B", json.dumps(captured["A"], ensure_ascii=False))
        self.assertNotIn("DRAFT-A", json.dumps(captured["B"], ensure_ascii=False))

        # 改副本不影响快照，也不影响下一次生成
        captured["A"]["snapshot"]["items"][0]["text"] = "被篡改的条目"
        self.assertEqual(self.snapshot.requirements[0]["text"], "学生可以发布二手教材")
        self.assertNotEqual(run_groups(self.snapshot)["A"].decisions[0]["scope"], "被篡改的条目")

    def test_draft_does_not_leak_other_group(self) -> None:
        payload = json.dumps(self.drafts["A"].to_json(), ensure_ascii=False)
        self.assertNotIn("DRAFT-B", payload)
        self.assertNotIn("体验优先", payload)

    def test_unconfirmed_snapshot_is_rejected(self) -> None:
        draft_snapshot = build(CASE.read_text(encoding="utf-8"))
        with self.assertRaises(ContractError) as caught:
            run_groups(draft_snapshot)
        self.assertEqual(caught.exception.code, "SNAPSHOT_NOT_CONFIRMED")

    def test_unknown_reference_is_rejected(self) -> None:
        draft = build_draft(self.snapshot, "A")
        draft.decisions[0]["requirement_ids"] = ["R-01", "R-99"]
        with self.assertRaises(ContractError) as caught:
            validate_draft(draft, self.snapshot)
        self.assertEqual(caught.exception.code, "UNKNOWN_REQUIREMENT")

    def test_missing_requirement_is_rejected(self) -> None:
        draft = build_draft(self.snapshot, "A")
        draft.decisions = [row for row in draft.decisions if row["requirement_ids"] != ["R-01"]]
        with self.assertRaises(ContractError) as caught:
            validate_draft(draft, self.snapshot)
        self.assertEqual(caught.exception.code, "DRAFT_COVERAGE")


class ConstraintHelperTest(unittest.TestCase):
    def test_deadline(self) -> None:
        self.assertEqual(deadline_days("两周内出演示版"), 14)
        self.assertEqual(deadline_days("10 天内上线"), 10)
        self.assertIsNone(deadline_days("尽快上线"))

    def test_headcount(self) -> None:
        self.assertEqual(headcount("只有两名开发"), 2)
        self.assertEqual(headcount("3 人团队"), 3)
        self.assertIsNone(headcount("团队规模待定"))

    def test_forbidden_subjects(self) -> None:
        self.assertEqual(forbidden_subjects("首版不做在线支付"), ["在线支付"])
        self.assertIn("第三方登录", forbidden_subjects("不引入第三方登录"))


if __name__ == "__main__":
    unittest.main()
