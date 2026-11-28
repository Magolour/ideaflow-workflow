"""规格追溯与质量：决策来源、接口输入输出、缺失估算。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ideaflow.agents import MockAgent
from ideaflow.specflow import run


class SpecTraceabilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.result = run("我要做微信", out_dir=cls.tmp.name,
                         agent=MockAgent(), discuss=True)
        cls.spec = json.loads((cls.result.out_dir / "spec.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_every_decision_has_reason_and_source(self) -> None:
        for decision in self.spec["decisions"]:
            self.assertTrue(decision.get("reason", "").strip(), decision["id"])
            sources = (decision.get("requirement_ids") or decision.get("claim_ids")
                       or decision.get("draft_ids"))
            self.assertTrue(sources, f"{decision['id']} 没有来源")

    def test_every_module_interface_has_input_and_output(self) -> None:
        for module in self.spec["modules"]:
            self.assertTrue(module["interfaces"], module["id"])
            for row in module["interfaces"]:
                self.assertTrue(str(row.get("input") or "").strip())
                self.assertTrue(str(row.get("output") or "").strip())

    def test_acceptance_is_measurable_and_linked(self) -> None:
        self.assertTrue(self.spec["acceptance"])
        for row in self.spec["acceptance"]:
            self.assertTrue(row["criterion"].strip())
            self.assertTrue(row["verify"].strip())

    def test_missing_estimates_are_recorded(self) -> None:
        missing = [row for row in self.spec["modules"]
                   if row.get("estimate_days") is None]
        for module in missing:
            covered = any(module["name"] in row["question"]
                          or module["id"] in (row.get("impact") or [])
                          for row in self.spec["open_items"])
            self.assertTrue(covered, f"{module['id']} 缺少估算且没有待确认")

    def test_markdown_and_json_share_version_and_hash(self) -> None:
        folder = self.result.out_dir
        machine = json.loads((folder / "spec.v1.json").read_text(encoding="utf-8"))
        markdown = (folder / "spec.v1.md").read_text(encoding="utf-8")
        self.assertIn(f"版本 {machine['version']}", markdown)
        self.assertIn(machine["hash"][:16], markdown)
        self.assertEqual(self.spec["hash"], machine["hash"])


if __name__ == "__main__":
    unittest.main()
