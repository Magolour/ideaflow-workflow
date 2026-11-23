"""真实模型 Agent 测试：解析、重试、审计与结构化校验。"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ideaflow.agents import LLMAgent, STAGE_HINTS
from ideaflow.contracts import ContractError
from ideaflow.roles import DISCUSSION_ROLES

ROLE = DISCUSSION_ROLES[0]


class ScriptedTransport:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def __call__(self, messages, temperature, json_mode):
        self.calls.append({"messages": messages, "temperature": temperature,
                           "json_mode": json_mode})
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return reply, {"prompt_tokens": 10, "completion_tokens": 5}


def agent_with(replies: list[str], **kwargs) -> tuple[LLMAgent, ScriptedTransport]:
    transport = ScriptedTransport(replies)
    agent = LLMAgent(api_key="test", model="test-model", transport=transport, **kwargs)
    return agent, transport


DIVERGE = json.dumps({
    "role": "ba",
    "claims": [{"text": "范围应收窄", "reason": "输入只给了目标", "evidence": "fact",
                "assumes": []}],
    "assumptions": [{"text": "用户集中", "verify_by": "访谈 5 人"}],
    "objections": [],
    "questions": ["谁验收？"],
}, ensure_ascii=False)


class LLMAgentTest(unittest.TestCase):
    def test_returns_parsed_output(self) -> None:
        agent, transport = agent_with([DIVERGE])
        data = agent.respond("diverge", ROLE, {"idea": "二手书", "constraints": []})
        self.assertEqual(data["role"], "ba")
        self.assertEqual(len(data["claims"]), 1)
        self.assertEqual(transport.calls[0]["temperature"], ROLE.temperature)
        self.assertTrue(transport.calls[0]["json_mode"])

    def test_strips_code_fence(self) -> None:
        agent, _ = agent_with(["```json\n" + DIVERGE + "\n```"])
        data = agent.respond("diverge", ROLE, {"idea": "二手书", "constraints": []})
        self.assertEqual(data["claims"][0]["text"], "范围应收窄")

    def test_retries_after_invalid_json(self) -> None:
        agent, transport = agent_with(["不是 JSON", DIVERGE])
        data = agent.respond("diverge", ROLE, {"idea": "二手书", "constraints": []})
        self.assertEqual(data["claims"][0]["text"], "范围应收窄")
        self.assertEqual(len(transport.calls), 2)
        repair = transport.calls[1]["messages"][-1]["content"]
        self.assertIn("输出不合格", repair)
        self.assertIn("必须符合这个形状", repair)

    @unittest.skip("待修复：clarify 阶段模型把 claims 写成字符串，会导致整个运行中止")
    def test_clarify_tolerates_string_claims(self) -> None:
        """真实模型常把 claims 写成字符串数组；该阶段应规范化或忽略，而不是中止。"""
        payload = json.dumps({
            "role": "ba",
            "claims": ["学生需要能发布教材"],
            "questions": ["首版是否支持在线支付？"],
        }, ensure_ascii=False)
        agent, _ = agent_with([payload])
        data = agent.respond("clarify", ROLE, {"idea": "二手书", "constraints": []})
        self.assertTrue(all(isinstance(row, dict) for row in data["claims"]))
        self.assertEqual(len(data["questions"]), 1)

    def test_fails_after_retries(self) -> None:
        agent, transport = agent_with(["不是 JSON"], retries=1)
        with self.assertRaises(ContractError) as caught:
            agent.respond("diverge", ROLE, {"idea": "二手书", "constraints": []})
        self.assertEqual(caught.exception.code, "LLM_BAD_OUTPUT")
        self.assertEqual(len(transport.calls), 2)

    def test_clarify_tolerates_string_claims(self) -> None:
        reply = json.dumps({
            "role": "ba",
            "claims": ["学生需要能发布教材"],
            "assumptions": ["用户是学生"],
            "objections": [],
            "questions": ["由谁验收？"],
        }, ensure_ascii=False)
        agent, _ = agent_with([reply])
        data = agent.respond("clarify", ROLE, {"idea": "二手书", "constraints": []})
        self.assertEqual(data["claims"], [])
        self.assertEqual(data["assumptions"], [])
        self.assertEqual(data["questions"], ["由谁验收？"])

    def test_clarify_hint_does_not_ask_for_claims(self) -> None:
        self.assertNotIn('"claims"', STAGE_HINTS["clarify"])
        self.assertIn("questions", STAGE_HINTS["clarify"])

    def test_empty_responses_trigger_retry(self) -> None:
        empty = json.dumps({"role": "ba", "responses": []})
        valid = json.dumps({"role": "ba", "responses": [
            {"objection": "OBJ-001", "position": "accept",
             "argument": "接受并补齐", "evidence": "rule"}]}, ensure_ascii=False)
        agent, transport = agent_with([empty, valid])
        data = agent.respond("respond", ROLE, {"objections": [{"id": "OBJ-001"}]})
        self.assertEqual(len(data["responses"]), 1)
        self.assertEqual(len(transport.calls), 2)
        self.assertIn("EMPTY_RESPONSES", transport.calls[1]["messages"][-1]["content"])

    def test_empty_reviews_trigger_retry(self) -> None:
        empty = json.dumps({"role": "ba", "reviews": []})
        valid = json.dumps({"role": "ba", "reviews": [
            {"claim": "CLM-001", "verdict": "preserved", "note": "未失真"}]},
            ensure_ascii=False)
        agent, transport = agent_with([empty, valid])
        data = agent.respond("review", ROLE, {"claims": [{"id": "CLM-001"}]})
        self.assertEqual(len(data["reviews"]), 1)
        self.assertEqual(len(transport.calls), 2)

    def test_critique_drops_unknown_targets(self) -> None:
        bad = json.dumps({
            "role": "ba",
            "objections": [{"target": "CLM-999", "text": "异议",
                            "consequence": "后果", "severity": "major"}],
        }, ensure_ascii=False)
        good = json.dumps({
            "role": "ba",
            "objections": [{"target": "CLM-001", "text": "异议",
                            "consequence": "后果", "severity": "major"}],
        }, ensure_ascii=False)
        agent, transport = agent_with([bad, good])
        data = agent.respond("critique", ROLE,
                             {"claims": [{"id": "CLM-001", "role": "ba"}]})
        self.assertEqual([row["target"] for row in data["objections"]], ["CLM-001"])
        self.assertIn("EMPTY_CRITIQUE", transport.calls[1]["messages"][-1]["content"])

    def test_review_drops_foreign_claims(self) -> None:
        bad = json.dumps({
            "role": "ba",
            "reviews": [{"claim": "CLM-999", "verdict": "preserved", "note": "x"}],
        }, ensure_ascii=False)
        good = json.dumps({
            "role": "ba",
            "reviews": [{"claim": "CLM-001", "verdict": "preserved", "note": "未失真"}],
        }, ensure_ascii=False)
        agent, transport = agent_with([bad, good])
        data = agent.respond("review", ROLE, {"claims": [{"id": "CLM-001"}]})
        self.assertEqual([row["claim"] for row in data["reviews"]], ["CLM-001"])
        self.assertIn("EMPTY_REVIEWS", transport.calls[1]["messages"][-1]["content"])

    def test_synthesize_is_validated(self) -> None:
        bad = json.dumps({"options": [{"key": "A", "name": "唯一方案", "scope": ["x"],
                                      "cost_days": "1 天", "risk": "低",
                                      "tradeoffs": "无", "based_on": []}],
                          "decisions": [], "not_adopted": [], "non_goals": [],
                          "narrative": {key: "" for key in
                                        ("background", "users", "value", "tech",
                                         "ops", "metrics")},
                          "work_packages": [{"name": "包", "deliverable": "物",
                                             "optimistic": 1, "likely": 2,
                                             "pessimistic": 3}],
                          "open_questions": []}, ensure_ascii=False)
        agent, _ = agent_with([bad], retries=0)
        with self.assertRaises(ContractError):
            agent.respond("synthesize", ROLE,
                          {"idea": "x", "claims": [{"id": "CLM-001"}]})

    def test_draft_normalizes_cost_and_satisfies(self) -> None:
        reply = json.dumps({
            "decisions": [{
                "requirement_ids": ["R-01"],
                "choice": "最小实现",
                "reason": "先跑通",
                "cost_days": "2 天",
                "satisfies": "完全满足",
            }],
        }, ensure_ascii=False)
        agent, _ = agent_with([reply], retries=0)
        context = {"snapshot": {"items": [
            {"id": "R-01", "type": "requirement", "text": "发布教材"}]}}
        data = agent.respond("draft", ROLE, context)
        self.assertEqual(data["decisions"][0]["cost_days"], 2.0)
        self.assertEqual(data["decisions"][0]["satisfies"], "full")

    def test_draft_missing_requirement_is_retried(self) -> None:
        reply = json.dumps({"decisions": [{
            "requirement_ids": ["R-01"], "choice": "x", "cost_days": 1,
        }]}, ensure_ascii=False)
        agent, transport = agent_with([reply], retries=1)
        context = {"snapshot": {"items": [
            {"id": "R-01", "type": "requirement", "text": "发布"},
            {"id": "R-02", "type": "requirement", "text": "搜索"}]}}
        with self.assertRaises(ContractError) as caught:
            agent.respond("draft", ROLE, context)
        self.assertEqual(caught.exception.code, "LLM_BAD_OUTPUT")
        self.assertIn("DRAFT_COVERAGE", caught.exception.message)
        self.assertEqual(len(transport.calls), 2)

    def test_draft_requires_decisions(self) -> None:
        agent, _ = agent_with([json.dumps({"decisions": []})])
        with self.assertRaises(ContractError) as caught:
            agent.respond("draft", ROLE, {"snapshot": {"items": []}})
        self.assertEqual(caught.exception.code, "LLM_BAD_OUTPUT")
        self.assertIn("EMPTY_DRAFT", caught.exception.message)

    def test_audit_log_written(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            agent, _ = agent_with([DIVERGE], audit_path=Path(folder) / "ai_calls.jsonl")
            agent.respond("diverge", ROLE, {"idea": "二手书", "constraints": []})
            lines = (Path(folder) / "ai_calls.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertEqual(row["status"], "ok")
            self.assertEqual(row["stage"], "diverge")
            self.assertEqual(row["prompt_tokens"], 10)

    def test_bind_run_moves_audit_path(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            agent, _ = agent_with([DIVERGE])
            agent.bind_run(Path(folder) / "run")
            agent.respond("diverge", ROLE, {"idea": "二手书", "constraints": []})
            self.assertTrue((Path(folder) / "run" / "ai_calls.jsonl").exists())

    def test_payload_supports_provider_options(self) -> None:
        captured = {}

        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout):
            captured.update(json.loads(request.data.decode("utf-8")))
            body = {"choices": [{"message": {"content": DIVERGE}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            return FakeResponse(json.dumps(body).encode("utf-8"))

        env = {"AI_JSON_MODE": "1", "AI_EXTRA_BODY": '{"enable_thinking": false}'}
        with patch.dict(os.environ, env), patch("urllib.request.urlopen", fake_urlopen):
            agent = LLMAgent(api_key="k", model="qwen-plus")
            agent.respond("diverge", ROLE, {"idea": "二手书", "constraints": []})
        self.assertEqual(captured["response_format"], {"type": "json_object"})
        self.assertFalse(captured["enable_thinking"])
        self.assertEqual(captured["model"], "qwen-plus")

    def test_json_mode_can_be_disabled(self) -> None:
        captured = {}

        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout):
            captured.update(json.loads(request.data.decode("utf-8")))
            body = {"choices": [{"message": {"content": DIVERGE}}]}
            return FakeResponse(json.dumps(body).encode("utf-8"))

        with patch.dict(os.environ, {"AI_JSON_MODE": "0"}), \
                patch("urllib.request.urlopen", fake_urlopen):
            agent = LLMAgent(api_key="k", model="qwen-plus")
            agent.respond("diverge", ROLE, {"idea": "二手书", "constraints": []})
        self.assertNotIn("response_format", captured)

    def test_missing_credentials(self) -> None:
        with self.assertRaises(ContractError) as caught:
            LLMAgent(api_key="", model="m")
        self.assertEqual(caught.exception.code, "LLM_NOT_CONFIGURED")


if __name__ == "__main__":
    unittest.main()
