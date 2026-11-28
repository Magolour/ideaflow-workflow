"""讨论数据传递：规格载荷里的角色、阶段、事件与来源。"""

from __future__ import annotations

import tempfile
import unittest

from ideaflow.agents import MockAgent
from ideaflow.specflow import run


class DiscussionPayloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.result = run("做一个校园二手书交易小程序", out_dir=cls.tmp.name,
                         agent=MockAgent(), discuss=True)
        cls.disc = cls.result.payload["discussion"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_roles_and_stages_are_exposed(self) -> None:
        names = [row["name"] for row in self.disc["roles"]]
        self.assertEqual(names, ["需求分析", "用户视角", "技术专家",
                                 "创新顾问", "风险评估", "质疑者"])
        self.assertTrue(all(row["done"] for row in self.disc["stages"]))
        self.assertTrue(all(row["participated"] for row in self.disc["roles"]))

    def test_events_have_traceable_fields(self) -> None:
        events = self.disc["events"]
        self.assertTrue(events)
        seqs = [row["seq"] for row in events]
        self.assertEqual(seqs, sorted(seqs))
        for row in events:
            for key in ("seq", "ts", "actor", "action", "object_type",
                        "object_id", "stage", "round"):
                self.assertIn(key, row)
        self.assertTrue(any(row["stage"] == "diverge" for row in events))
        self.assertTrue(any(row["stage"] == "sign" for row in events))

    def test_claims_objections_and_decisions_are_linked(self) -> None:
        claims = {row["id"] for row in self.disc["claims"]}
        self.assertTrue(claims)
        for row in self.disc["objections"]:
            self.assertIn(row["target"], claims)
        self.assertTrue(self.disc["key_opinions"])
        self.assertIn(self.disc["consensus"]["state"],
                      ("consensus", "pending_verification", "no_consensus"))

    def test_without_discussion_payload_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = run("做一个登录页", out_dir=folder, agent=MockAgent())
            self.assertIsNone(result.payload["discussion"])


if __name__ == "__main__":
    unittest.main()
