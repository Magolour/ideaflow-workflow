"""草案阶段接入真实 Agent：注入、覆盖校验与流程透传。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ideaflow.contracts import ContractError
from ideaflow.drafts import build_draft, run_groups
from ideaflow.snapshot import build
from ideaflow.specflow import run

CASE = Path(__file__).resolve().parents[1] / "cases" / "campus-books.md"


class FakeDraftAgent:
    name = "fake"

    def __init__(self, *, skip: str | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.bound: Path | None = None
        self.skip = skip

    def bind_run(self, path: Path) -> None:
        self.bound = Path(path)

    def respond(self, stage: str, role, context: dict) -> dict:
        self.calls.append((stage, role.id))
        if stage != "draft":
            raise AssertionError(f"unexpected stage {stage}")
        items = context["snapshot"]["items"]
        requirements = [item for item in items if item["type"] == "requirement"]
        decisions = []
        for item in requirements:
            if item["id"] == self.skip:
                continue
            decisions.append({
                "requirement_ids": [item["id"]],
                "choice": f"模型方案：{item['text']}",
                "reason": "模型判断这是最小实现",
                "cost_days": 2.0,
                "satisfies": "full",
                "risk": "低",
            })
        return {
            "decisions": decisions,
            "claims": [{"text": "模型给出的主张", "requirement_ids": [requirements[0]["id"]],
                        "evidence": "rule"}],
            "assumptions": [{"text": "学生愿意使用", "verify_by": "访谈 5 人"}],
            "risks": ["模型识别的风险"],
        }


class DraftAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = build(CASE.read_text(encoding="utf-8")).confirm("产品负责人")

    def test_builds_draft_from_agent_output(self) -> None:
        agent = FakeDraftAgent()
        draft = build_draft(self.snapshot, "A", agent=agent)
        self.assertEqual(agent.calls[0][0], "draft")
        self.assertEqual(draft.decisions[0]["id"], "A-DEC-01")
        self.assertTrue(draft.decisions[0]["scope"])
        self.assertEqual(draft.claims[0]["id"], "A-01")
        self.assertGreater(draft.commit_days, 0)

    def test_missing_requirement_is_rejected(self) -> None:
        agent = FakeDraftAgent(skip="R-03")
        with self.assertRaises(ContractError) as caught:
            build_draft(self.snapshot, "A", agent=agent)
        self.assertEqual(caught.exception.code, "DRAFT_COVERAGE")

    def test_both_groups_use_agent(self) -> None:
        agent = FakeDraftAgent()
        drafts = run_groups(self.snapshot, agent=agent)
        self.assertEqual({row[1] for row in agent.calls}, {"group-A", "group-B"})
        self.assertEqual(set(drafts), {"A", "B"})

    def test_specflow_passes_agent_and_binds_run(self) -> None:
        agent = FakeDraftAgent()
        with tempfile.TemporaryDirectory() as folder:
            result = run(CASE.read_text(encoding="utf-8"), out_dir=folder, agent=agent)
            self.assertTrue(result.out_dir.exists())
            self.assertEqual(len(agent.calls), 2)
            self.assertEqual(agent.bound, result.out_dir)
            self.assertTrue((result.out_dir / "spec.v1.json").exists())


if __name__ == "__main__":
    unittest.main()
