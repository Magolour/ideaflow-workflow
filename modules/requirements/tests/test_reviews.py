"""评审与规格整合测试：隔离、评分、采纳理由与追溯映射。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ideaflow.contracts import ContractError
from ideaflow.drafts import run_groups
from ideaflow.reviews import review_all, review_context, review_draft
from ideaflow.snapshot import build
from ideaflow.spec import build_spec, load_spec, save_spec

CASE = Path(__file__).resolve().parents[1] / "cases" / "campus-books.md"


class ReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = build(CASE.read_text(encoding="utf-8")).confirm("产品负责人")
        cls.drafts = run_groups(cls.snapshot)
        cls.reviews = review_all(cls.snapshot, cls.drafts)
        cls.spec = build_spec(cls.snapshot, cls.drafts, cls.reviews)

    def test_review_context_holds_one_draft_only(self) -> None:
        context = review_context(self.snapshot, self.drafts["A"])
        self.assertEqual(context["draft"]["draft_id"], "DRAFT-A")
        payload = json.dumps(context, ensure_ascii=False)
        self.assertNotIn("DRAFT-B", payload)
        self.assertNotIn("体验优先", payload)
        self.assertIn("criteria", context)

    def test_scores_and_ranking(self) -> None:
        a = self.reviews["drafts"]["DRAFT-A"]
        b = self.reviews["drafts"]["DRAFT-B"]
        self.assertGreater(a["weighted"], b["weighted"])
        self.assertEqual(self.reviews["verdict"]["base_draft"], "DRAFT-A")
        self.assertEqual(b["scores"]["feasibility"], 1.0)
        self.assertEqual(b["scores"]["conflicts"], 2)
        self.assertEqual(len(a["criteria"]), 3)
        for row in a["criteria"]:
            self.assertTrue(row["reason"])
            self.assertIn("claim_ids", row)

    def test_disagreements_record_impact(self) -> None:
        a = self.reviews["drafts"]["DRAFT-A"]
        b = self.reviews["drafts"]["DRAFT-B"]
        self.assertEqual(len(a["disagreements"]), 1)
        self.assertEqual(a["disagreements"][0]["requirement_ids"], ["R-03"])
        self.assertEqual(a["disagreements"][0]["severity"], "blocking")
        self.assertEqual(len(b["disagreements"]), 2)
        self.assertTrue(all(row["severity"] == "blocking" for row in b["disagreements"]))

    def test_review_is_repeatable(self) -> None:
        again = review_draft(self.snapshot, self.drafts["A"])
        self.assertEqual(again["weighted"], self.reviews["drafts"]["DRAFT-A"]["weighted"])
        self.assertEqual(again["disagreements"],
                         self.reviews["drafts"]["DRAFT-A"]["disagreements"])

    def test_spec_uses_base_and_adopts_one_item(self) -> None:
        self.assertEqual(self.spec.base_draft, "DRAFT-A")
        self.assertEqual(len(self.spec.decisions), 4)
        origins = {row["requirement_ids"][0]: row["origin"] for row in self.spec.decisions}
        self.assertEqual(origins["R-02"], "adopted")
        self.assertEqual(origins["R-01"], "base")
        self.assertEqual(origins["R-04"], "base")

        payment = next(row for row in self.spec.decisions if row["requirement_ids"] == ["R-03"])
        self.assertEqual(payment["satisfies"], "declined")
        self.assertTrue(payment["needs_human"])

        adopted = next(row for row in self.spec.decisions if row["origin"] == "adopted")
        self.assertIn("DRAFT-B", adopted["draft_ids"])
        self.assertTrue(adopted["claim_ids"])
        self.assertIn("剩余预算", adopted["reason"])

    def test_not_adopted_keeps_reasons(self) -> None:
        reasons = {row["requirement_ids"][0]: row["reason"] for row in self.spec.not_adopted}
        self.assertIn("冲突", reasons["R-03"])
        self.assertIn("预算", reasons["R-04"])
        self.assertIn("预算", reasons["R-01"])
        self.assertEqual(len(self.spec.non_goals), len(self.spec.not_adopted))

    def test_modules_and_mapping_are_bidirectional(self) -> None:
        self.assertEqual(len(self.spec.modules), len(self.snapshot.requirements))
        for module in self.spec.modules:
            self.assertTrue(module["requirement_ids"])
            self.assertTrue(module["interfaces"])
            for interface in module["interfaces"]:
                for key in ("name", "caller", "input", "output"):
                    self.assertTrue(interface[key])
        mapped = {row["requirement_id"] for row in self.spec.mapping}
        self.assertEqual(mapped, self.snapshot.ids("requirement"))
        for row in self.spec.mapping:
            self.assertTrue(row["module_ids"])
            self.assertTrue(row["decision_ids"])

    def test_acceptance_covers_every_requirement(self) -> None:
        self.assertEqual({row["requirement_id"] for row in self.spec.acceptance},
                         self.snapshot.ids("requirement"))
        for row in self.spec.acceptance:
            self.assertTrue(row["criterion"])
            self.assertTrue(row["verify"])

    def test_open_items_include_blocking_payment(self) -> None:
        blocking = self.spec.blocking_open_items
        self.assertEqual(len(blocking), 1)
        self.assertEqual(blocking[0]["impact"], ["R-03"])
        self.assertTrue(blocking[0]["question"].startswith("首版是否必须支持"))
        self.assertEqual(len(self.spec.open_items), 3)
        self.assertEqual(len([row for row in self.spec.open_items if not row["blocking"]]), 2)

    def test_effort_stays_within_deadline(self) -> None:
        self.assertLessEqual(self.spec.effort["commit_days"], 14)

    def test_spec_hash_is_deterministic(self) -> None:
        again = build_spec(self.snapshot, self.drafts, self.reviews)
        self.assertEqual(again.hash, self.spec.hash)

    def test_spec_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            save_spec(self.spec, folder)
            loaded = load_spec(folder)
            self.assertEqual(loaded.hash, self.spec.hash)
            self.assertEqual(loaded.open_items, self.spec.open_items)
            self.assertTrue((Path(folder) / "spec.v1.json").exists())

    def test_two_drafts_required(self) -> None:
        partial = {"verdict": {"base_draft": "DRAFT-A"}}
        with self.assertRaises(ContractError) as caught:
            build_spec(self.snapshot, {"A": self.drafts["A"]}, partial)
        self.assertEqual(caught.exception.code, "NEED_TWO_DRAFTS")


if __name__ == "__main__":
    unittest.main()
