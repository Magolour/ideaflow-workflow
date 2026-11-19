"""讨论闭环与收敛引擎的回归测试。

测试关注流程约束而不是文案质量：编号、隔离、契约校验、章节完整性、
终止条件与共识判定，这些都不能靠提示词保证。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from ideaflow.agents import MockAgent
from ideaflow.blackboard import Blackboard
from ideaflow.contracts import ContractError, validate_role_output, validate_stances
from ideaflow.convergence import (consensus_from, evaluate_termination,
                                  integrity_issues, key_opinions,
                                  needs_counterexample)
from ideaflow.pipeline import run
from ideaflow.roles import DISCUSSION_ROLES

IDEA = "做一个校园二手书交易小程序，学生可以发布和搜索教材"


class RecordingAgent(MockAgent):
    """记录每次调用上下文，用于验证独立发散是否真的隔离。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def respond(self, stage, role, context):
        self.calls.append({"stage": stage, "role": role.id, "context": deepcopy(context)})
        return super().respond(stage, role, context)


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_emits_all_artifacts(self) -> None:
        result = run(IDEA, out_dir=self.out)
        self.assertTrue(result.proposal_path.exists())
        self.assertTrue((result.out_dir / "blackboard.json").exists())
        self.assertTrue((result.out_dir / "events.jsonl").exists())
        snapshot = json.loads((result.out_dir / "blackboard.json").read_text(encoding="utf-8"))
        json.dumps(snapshot, ensure_ascii=False)  # 必须可序列化
        self.assertEqual(len(snapshot["options"]), 3)

    def test_ids_are_unique_and_sequential(self) -> None:
        result = run(IDEA, out_dir=self.out)
        claims = [row["id"] for row in result.board.claims]
        self.assertEqual(claims, [f"CLM-{index:03d}" for index in range(1, len(claims) + 1)])
        self.assertEqual(len(claims), len(set(claims)))

    def test_diverge_is_isolated(self) -> None:
        agent = RecordingAgent()
        run(IDEA, out_dir=self.out, agent=agent)
        diverge = [call for call in agent.calls if call["stage"] == "diverge"]
        self.assertEqual(len(diverge), len(DISCUSSION_ROLES))
        for call in diverge:
            self.assertEqual(call["context"]["claims"], [])
            self.assertNotIn("objections", call["context"])
        contexts = [id(call["context"]) for call in diverge]
        self.assertEqual(len(contexts), len(set(contexts)))

    def test_proposal_has_eleven_chapters(self) -> None:
        result = run(IDEA, out_dir=self.out)
        text = result.proposal_path.read_text(encoding="utf-8")
        for index in range(1, 12):
            self.assertIn(f"## {index} ", text, f"缺少第 {index} 章")
        self.assertIn("### 11.7 收敛判定", text)
        self.assertIn("### 11.9 伪共识检查", text)
        self.assertIn("附录 C 关键意见处理表", text)

    def test_responses_close_blocking_objections(self) -> None:
        result = run(IDEA, out_dir=self.out)
        self.assertEqual(result.board.unresolved_blocking(), [])
        self.assertTrue(all(row.get("response") for row in result.board.objections))
        self.assertIn(result.board.consensus_state(),
                      ("conditional", "pending_verification", "consensus"))

    def test_key_opinions_have_dispositions(self) -> None:
        result = run(IDEA, out_dir=self.out)
        rows = key_opinions(result.board)
        self.assertEqual(len(rows), len(result.board.claims))
        self.assertTrue(all(row["status"] for row in rows))
        self.assertTrue(any(row["status"] == "adopted" for row in rows))

    def test_events_are_sequential(self) -> None:
        result = run(IDEA, out_dir=self.out)
        seqs = [row["seq"] for row in result.events.events]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
        lines = (result.out_dir / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), len(seqs))

    def test_same_second_runs_do_not_overwrite(self) -> None:
        first = run(IDEA, out_dir=self.out)
        second = run(IDEA, out_dir=self.out)
        self.assertNotEqual(first.out_dir, second.out_dir)


class ConvergenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.board = run(IDEA, out_dir=Path(self.tmp.name)).board

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_value_objection_requires_human_decision(self) -> None:
        row = self.board.objections[0]
        row["status"] = "open"
        self.board.divergence = {row["id"]: "value"}
        result = evaluate_termination(self.board, max_rounds=5)
        self.assertEqual(result["reason"], "human_decision")
        self.assertFalse(result["stop"] is False)
        state = consensus_from(self.board, result)
        self.assertEqual(state["state"], "no_consensus")

    def test_blocking_objection_hits_round_limit(self) -> None:
        row = self.board.objections[0]
        row["status"] = "open"
        row["severity"] = "blocking"
        self.board.divergence = {row["id"]: "tradeoff"}
        self.board.round = 3
        result = evaluate_termination(self.board, max_rounds=2)
        self.assertEqual(result["reason"], "max_rounds")

    def test_two_rounds_without_new_issues_stops_discussion(self) -> None:
        row = self.board.objections[0]
        row["status"] = "open"
        row["severity"] = "blocking"
        self.board.divergence = {row["id"]: "tradeoff"}
        self.board.round = 1
        result = evaluate_termination(self.board, max_rounds=5, rounds_without_new=2)
        self.assertEqual(result["reason"], "no_new_issues")
        self.assertEqual(consensus_from(self.board, result)["state"], "no_consensus")

    def test_all_agree_triggers_counterexample(self) -> None:
        for role in self.board.stances:
            for option_id in self.board.stances[role]:
                self.board.stances[role][option_id] = "agree"
        self.assertTrue(needs_counterexample(self.board))

    def test_integrity_issue_blocks_cited_claim(self) -> None:
        cited = next(row["based_on"][0] for row in self.board.decisions
                     if row.get("based_on"))
        self.board.reviews = [{"role": "ba", "claim": cited, "verdict": "omitted",
                               "note": "决策改写了原意", "round": 2}]
        issues = integrity_issues(self.board)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["claim"], cited)


class ContractTest(unittest.TestCase):
    def test_claim_requires_reason(self) -> None:
        payload = {"role": "ba", "claims": [{"text": "观点", "reason": "",
                                             "evidence": "fact", "assumes": []}]}
        with self.assertRaises(ContractError) as caught:
            validate_role_output(payload, role_id="ba", stage="diverge")
        self.assertEqual(caught.exception.code, "INVALID_FIELD")

    def test_unknown_evidence_type_rejected(self) -> None:
        payload = {"role": "ba", "claims": [{"text": "观点", "reason": "理由",
                                             "evidence": "听说", "assumes": []}]}
        with self.assertRaises(ContractError) as caught:
            validate_role_output(payload, role_id="ba", stage="diverge")
        self.assertEqual(caught.exception.code, "INVALID_ENUM")

    def test_objection_must_reference_existing_claim(self) -> None:
        payload = {"role": "skeptic", "claims": [], "assumptions": [],
                   "objections": [{"target": "CLM-999", "text": "反例",
                                   "consequence": "后果", "severity": "blocking"}]}
        with self.assertRaises(ContractError) as caught:
            validate_role_output(payload, role_id="skeptic", stage="critique",
                                 known_claims={"CLM-001"})
        self.assertEqual(caught.exception.code, "UNKNOWN_CLAIM")

    def test_response_must_reference_open_objection(self) -> None:
        payload = {"role": "ba", "responses": [
            {"objection": "OBJ-404", "position": "accept",
             "argument": "接受", "evidence": "rule"}]}
        with self.assertRaises(ContractError) as caught:
            validate_role_output(payload, role_id="ba", stage="respond",
                                 known_objections={"OBJ-001"})
        self.assertEqual(caught.exception.code, "UNKNOWN_OBJECTION")

    def test_conditional_stance_requires_condition(self) -> None:
        payload = {"role": "ba", "stances": [
            {"option": "OPT-A", "stance": "conditional", "reason": "还行", "condition": ""}]}
        with self.assertRaises(ContractError) as caught:
            validate_stances(payload, role_id="ba", known_options={"OPT-A"})
        self.assertEqual(caught.exception.code, "MISSING_CONDITION")

    def test_unreviewed_stance_is_not_support(self) -> None:
        board = Blackboard("测试想法")
        board.add_option(key="A", name="方案 A", scope=["范围"], cost_days="1",
                         risk="低", tradeoffs="无", based_on=[])
        board.record_stance("user", "OPT-A", "unreviewed")
        self.assertEqual(board.stances["user"]["OPT-A"], "unreviewed")


if __name__ == "__main__":
    unittest.main()
