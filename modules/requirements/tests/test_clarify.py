"""人工澄清：问题先列出，答复进入黑板，未答保持假设。"""

from __future__ import annotations

import tempfile
import unittest

from ideaflow.agents import MockAgent
from ideaflow.pipeline import clarify_questions, run


class ClarifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.questions = clarify_questions("做一个校园二手书交易小程序", agent=MockAgent())

    def test_questions_are_blocking_and_identifiable(self) -> None:
        self.assertTrue(self.questions)
        for row in self.questions:
            self.assertTrue(row["blocking"])
            self.assertRegex(row["id"], r"^Q-\d{3}$")
            self.assertTrue(row["text"].strip())

    def test_answers_are_recorded_as_human_decisions(self) -> None:
        first = self.questions[0]
        with tempfile.TemporaryDirectory() as folder:
            result = run("做一个校园二手书交易小程序", out_dir=folder,
                         agent=MockAgent(), answers={first["id"]: "由实验室管理员验收"})
            question = next(row for row in result.board.open_questions
                            if row["id"] == first["id"])
            self.assertEqual(question["resolution"], "由实验室管理员验收")
            self.assertEqual(question["owner"], "human")
            decision = next(row for row in result.board.decisions
                            if row["scope"] == f"澄清 {first['id']}")
            self.assertEqual(decision["choice"], "由实验室管理员验收")

    def test_unanswered_questions_stay_assumptions(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = run("做一个校园二手书交易小程序", out_dir=folder, agent=MockAgent())
            question = result.board.open_questions[0]
            self.assertEqual(question["resolution"], "未知，按假设继续")
            self.assertEqual(question["owner"], "human:assumed")


if __name__ == "__main__":
    unittest.main()
