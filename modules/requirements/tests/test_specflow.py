"""规格流程编排测试：从案例到修订、人工问题与导出。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ideaflow.agents import MockAgent
from ideaflow.contracts import ContractError
from ideaflow.specflow import load_case, run


class RecordingAgent(MockAgent):
    """记录收到的阶段，用来验证规格流程是否真的走了讨论。"""

    def __init__(self) -> None:
        self.stages: list[str] = []

    def respond(self, stage, role, context):
        self.stages.append(stage)
        return super().respond(stage, role, context)

TIGHT = "campus-books-tight"


class SpecFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_tight_case_revises_then_escalates(self) -> None:
        result = run(load_case(TIGHT), out_dir=self.root, case_name=TIGHT)
        data = result.payload
        self.assertEqual(data["flow"], "spec")
        self.assertEqual(data["status"], "awaiting_human")
        self.assertEqual([row["spec_version"] for row in data["audits"]], [1, 2])
        self.assertEqual(len(data["revisions"]), 1)
        self.assertEqual(data["revisions"][0]["id"], "REV-001")
        self.assertEqual(data["revisions"][0]["recheck"]["resolved"], ["AUD-02"])
        self.assertEqual(data["question"]["id"], "HUMAN-Q-01")
        self.assertIn("需人工决策", data["markdown"])
        self.assertIn("HUMAN-Q-01", data["markdown"])

    def test_artifacts_are_written(self) -> None:
        result = run(load_case(TIGHT), out_dir=self.root, case_name=TIGHT)
        for name in ("snapshot.v1.json", "race.json", "review.json",
                     "spec.v2.json", "spec.v2.md", "audit.v1.json", "audit.v2.json",
                     "revisions.json", "human_question.json", "manifest.json"):
            self.assertTrue((result.out_dir / name).exists(), name)
        manifest = json.loads((result.out_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("human_question.json", manifest["files"])
        self.assertIn("spec.v2.md", manifest["files"])

    def test_payload_has_snapshot_drafts_and_tradeoffs(self) -> None:
        data = run(load_case(TIGHT), out_dir=self.root, case_name=TIGHT).payload
        self.assertEqual(data["snapshot"]["counts"],
                         {"requirement": 4, "constraint": 3, "open": 0})
        self.assertEqual(len(data["snapshot"]["items"]), 7)
        self.assertEqual([row["draft_id"] for row in data["drafts"]],
                         ["DRAFT-A", "DRAFT-B"])
        self.assertEqual(data["tradeoffs"]["counts"]["total"], 4)
        self.assertEqual(data["tradeoffs"]["headline"], "TRD-03")
        self.assertEqual(len(data["spec"]["modules"]), 4)

    def test_normal_case_escalates_without_revision(self) -> None:
        data = run(load_case("campus-books"), out_dir=self.root,
                   case_name="campus-books").payload
        self.assertEqual(len(data["audits"]), 1)
        self.assertEqual(data["revisions"], [])
        self.assertIsNotNone(data["question"])
        self.assertEqual(data["status"], "awaiting_human")

    def test_free_idea_can_pass_audit(self) -> None:
        data = run("做一个团队值班排班工具，支持换班和请假。",
                   out_dir=self.root).payload
        self.assertEqual(data["status"], "draft")
        self.assertIsNone(data["question"])
        self.assertTrue(data["audits"][0]["passed"])
        self.assertTrue(data["html"])

    def test_runs_get_unique_directories(self) -> None:
        first = run(load_case(TIGHT), out_dir=self.root, case_name=TIGHT)
        second = run(load_case(TIGHT), out_dir=self.root, case_name=TIGHT)
        self.assertNotEqual(first.run_id, second.run_id)

    @unittest.skip("待实现：讨论阶段接进规格流程（discuss=True 时先跑六角色讨论）")
    def test_discussion_stages_run_before_spec(self) -> None:
        """规格流程必须真的经过讨论，而不是直接切原文。"""
        agent = RecordingAgent()
        run(load_case("campus-books"), out_dir=self.root,
            case_name="campus-books", agent=agent, discuss=True)
        stages = set(agent.stages)
        for stage in ("clarify", "diverge", "critique", "synthesize"):
            self.assertIn(stage, stages, f"讨论阶段缺少 {stage}")

    def test_unknown_case_is_reported(self) -> None:
        with self.assertRaises(ContractError) as caught:
            load_case("not-exist")
        self.assertEqual(caught.exception.code, "CASE_NOT_FOUND")
        with self.assertRaises(ContractError) as caught:
            load_case("../secret")
        self.assertEqual(caught.exception.code, "INVALID_CASE")


if __name__ == "__main__":
    unittest.main()
