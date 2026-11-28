"""T-17：共识矩阵数据结构。"""

from __future__ import annotations

import tempfile
import unittest

from ideaflow.agents import MockAgent
from ideaflow.pipeline import run
from ideaflow.server import _consensus_matrix, _result_payload


class ConsensusMatrixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.result = run("做一个校园二手书交易小程序", out_dir=cls.tmp.name,
                         agent=MockAgent())
        cls.payload = _result_payload(cls.result)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_matrix_has_roles_options_and_cells(self) -> None:
        matrix = self.payload["consensus_matrix"]
        self.assertTrue(matrix["options"])
        self.assertEqual(len(matrix["roles"]), 6)
        self.assertEqual(len(matrix["cells"]), len(matrix["options"]) * 6)
        row = matrix["cells"][0]
        for key in ("role", "role_name", "option", "stance", "reason", "condition"):
            self.assertIn(key, row)

    def test_missing_reason_is_empty_not_fabricated(self) -> None:
        matrix = self.payload["consensus_matrix"]
        for cell in matrix["cells"]:
            self.assertIsInstance(cell["reason"], str)
            self.assertIsInstance(cell["condition"], str)

    def test_existing_job_fields_are_unchanged(self) -> None:
        for key in ("run_id", "idea", "counts", "consensus", "options",
                    "objections", "questions", "markdown"):
            self.assertIn(key, self.payload)

    def test_support_is_preserved(self) -> None:
        matrix = self.payload["consensus_matrix"]
        self.assertEqual(matrix["support"], self.payload["option_support"])


if __name__ == "__main__":
    unittest.main()
