"""升级测试：人工问题的内容、选项、状态与幂等性。"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from ideaflow.audit import audit_spec
from ideaflow.contracts import ContractError
from ideaflow.drafts import run_groups
from ideaflow.escalate import build_question, escalate, save_question
from ideaflow.reviews import review_all
from ideaflow.revisions import scope_revision
from ideaflow.snapshot import build
from ideaflow.spec import build_spec, load_spec, save_spec
from ideaflow.spec_doc import export_spec, verify_export

TIGHT = Path(__file__).resolve().parents[1] / "cases" / "campus-books-tight.md"


class EscalateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = build(TIGHT.read_text(encoding="utf-8")).confirm("产品负责人")
        cls.drafts = run_groups(cls.snapshot)
        cls.spec = build_spec(cls.snapshot, cls.drafts,
                              review_all(cls.snapshot, cls.drafts))
        cls.revised, _, _ = scope_revision(
            cls.spec, cls.snapshot, audit_spec(cls.spec, cls.snapshot))
        cls.audit = audit_spec(cls.revised, cls.snapshot)
        cls.escalated, cls.question = escalate(cls.revised, cls.snapshot, cls.audit)

    def test_question_is_specific(self) -> None:
        question = self.question
        self.assertEqual(question["id"], "HUMAN-Q-01")
        self.assertTrue(question["question"].startswith("首版是否必须支持"))
        self.assertIn("R-03", question["question"])
        self.assertGreaterEqual(len(question["background"]), 2)
        self.assertEqual([row["key"] for row in question["options"]], ["A", "B", "C"])
        self.assertEqual(question["recommended"], "B")
        self.assertTrue(question["if_undecided"])

    def test_options_carry_cost_and_consequence(self) -> None:
        options = {row["key"]: row for row in self.question["options"]}
        self.assertGreater(options["A"]["cost_days"], options["B"]["cost_days"])
        self.assertIn("C-03", options["A"]["text"])
        for row in options.values():
            self.assertTrue(row["consequence"])

    def test_affected_items_are_listed(self) -> None:
        affected = self.question["affected"]
        self.assertEqual(affected["requirements"], ["R-03"])
        self.assertEqual(affected["constraints"], ["C-03"])
        self.assertTrue(affected["issues"])

    def test_status_and_hash(self) -> None:
        self.assertEqual(self.escalated.status, "awaiting_human")
        self.assertEqual(self.escalated.version, self.revised.version)
        self.assertEqual(self.escalated.hash, self.revised.hash)
        self.assertEqual(self.escalated.escalation["id"], "HUMAN-Q-01")

    def test_escalation_survives_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            save_spec(self.escalated, folder)
            loaded = load_spec(folder)
            self.assertEqual(loaded.status, "awaiting_human")
            self.assertEqual(loaded.escalation["question"], self.question["question"])
            self.assertEqual(loaded.hash, self.escalated.hash)

    def test_idempotent(self) -> None:
        again, question = escalate(self.escalated, self.snapshot, self.audit)
        self.assertEqual(question["created_at"], self.question["created_at"])
        self.assertIs(again, self.escalated)

    def test_export_marks_pending_question(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = export_spec(self.escalated, self.snapshot, folder)
            markdown = (Path(folder) / result["markdown"]).read_text(encoding="utf-8")
            self.assertIn("## 9 需人工决策", markdown)
            self.assertIn("HUMAN-Q-01", markdown)
            self.assertIn("确认前不能作为开发依据", markdown)
            self.assertTrue(verify_export(folder, self.escalated.version)["ok"])

    def test_no_question_when_audit_passes(self) -> None:
        spec = copy.deepcopy(self.revised)
        decision = next(row for row in spec.decisions
                        if row["requirement_ids"] == ["R-03"])
        decision["satisfies"] = "full"
        decision["needs_human"] = False
        decision["choice"] = "线下当面成交并记录成交状态"
        audit = audit_spec(spec, self.snapshot)
        self.assertTrue(audit["passed"], audit["issues"])
        self.assertIsNone(build_question(spec, self.snapshot, audit))
        with self.assertRaises(ContractError) as caught:
            escalate(spec, self.snapshot, audit)
        self.assertEqual(caught.exception.code, "NO_ESCALATION")

    def test_schedule_conflict_question(self) -> None:
        spec = copy.deepcopy(self.revised)
        decision = next(row for row in spec.decisions
                        if row["requirement_ids"] == ["R-03"])
        decision["satisfies"] = "full"
        decision["choice"] = "线下当面成交并记录成交状态"
        spec.effort["commit_days"] = 20.0
        audit = audit_spec(spec, self.snapshot)
        question = build_question(spec, self.snapshot, audit)
        self.assertIsNotNone(question)
        self.assertIn("期限", question["question"])
        self.assertEqual(len(question["options"]), 3)

    def test_save_question_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = save_question(self.question, folder)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["id"], "HUMAN-Q-01")


if __name__ == "__main__":
    unittest.main()
