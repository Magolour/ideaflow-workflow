"""讨论阶段角色级失败隔离：单角色失败不终止整条运行。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ideaflow.agents import MockAgent
from ideaflow.contracts import ContractError
from ideaflow.pipeline import run


class FailingRoleAgent(MockAgent):
    def __init__(self, stage: str, role_id: str) -> None:
        self.stage = stage
        self.role_id = role_id

    def respond(self, stage, role, context):
        if stage == self.stage and role.id == self.role_id:
            raise ContractError("LLM_BAD_OUTPUT", "模拟该角色失败", stage)
        return super().respond(stage, role, context)


class DuplicateCritiqueAgent(MockAgent):
    """每轮都提出同一条异议：第二轮去重后为空，不应报错。"""

    def respond(self, stage, role, context):
        if stage == "critique" and role.id == "ba":
            claims = context.get("claims") or []
            objections = []
            if claims:
                objections.append({"target": claims[0]["id"], "text": "重复异议",
                                   "consequence": "重复", "severity": "major"})
            return {"role": "ba", "claims": [], "assumptions": [],
                    "questions": [], "objections": objections}
        return super().respond(stage, role, context)


class CaptureReviewAgent(MockAgent):
    def __init__(self) -> None:
        self.contexts: list[tuple[str, dict]] = []

    def respond(self, stage, role, context):
        if stage == "review":
            self.contexts.append((role.id, context))
        return super().respond(stage, role, context)


class ForeignReviewAgent(MockAgent):
    """复核时引用了别人的论点：应被过滤，而不是中止整条 run。"""

    def respond(self, stage, role, context):
        if stage == "review" and role.id == "user":
            return {"role": "user", "claims": [], "assumptions": [], "objections": [],
                    "questions": [],
                    "reviews": [{"claim": "CLM-001", "verdict": "preserved", "note": "x"}]}
        return super().respond(stage, role, context)


class IsolationTest(unittest.TestCase):
    def run_with(self, stage: str, role_id: str) -> dict:
        with tempfile.TemporaryDirectory() as folder:
            result = run("做一个校园二手书交易小程序，支持发布与搜索",
                         out_dir=folder, agent=FailingRoleAgent(stage, role_id))
            return result.report

    def test_diverge_failure_keeps_run_alive(self) -> None:
        report = self.run_with("diverge", "ba")
        warnings = report["warnings"]
        self.assertGreaterEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["stage"], "diverge")
        self.assertEqual(warnings[0]["role"], "ba")
        self.assertGreater(report["counts"]["claims"], 0)

    def test_clarify_failure_keeps_run_alive(self) -> None:
        report = self.run_with("clarify", "ba")
        self.assertEqual(report["warnings"][0]["stage"], "clarify")
        self.assertEqual(report["counts"]["rounds"], 1)

    def test_duplicate_critique_does_not_fail(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = run("做一个校园二手书交易小程序，支持发布与搜索",
                         out_dir=folder, agent=DuplicateCritiqueAgent(),
                         max_rounds=2, threshold=0.99)
            self.assertTrue((result.out_dir / "proposal.md").exists())
            self.assertEqual(result.report["counts"]["rounds"], 2)
            self.assertFalse([row for row in result.report["warnings"]
                              if row["stage"] == "critique"])

    def test_review_context_contains_claim_text(self) -> None:
        agent = CaptureReviewAgent()
        with tempfile.TemporaryDirectory() as folder:
            run("做一个校园二手书交易小程序，支持发布与搜索",
                out_dir=folder, agent=agent)
        self.assertTrue(agent.contexts)
        for _, context in agent.contexts:
            for claim in context["claims"]:
                self.assertTrue(claim["text"])
                self.assertIn("evidence", claim)

    def test_foreign_review_is_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = run("做一个校园二手书交易小程序，支持发布与搜索",
                         out_dir=folder, agent=ForeignReviewAgent())
            self.assertTrue((result.out_dir / "proposal.md").exists())


if __name__ == "__main__":
    unittest.main()
