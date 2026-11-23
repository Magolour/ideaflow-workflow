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


if __name__ == "__main__":
    unittest.main()
