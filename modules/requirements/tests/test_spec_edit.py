"""T-07：单条需求文本编辑。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ideaflow.agents import MockAgent
from ideaflow.contracts import ContractError
from ideaflow.spec_edit import edit_spec_item
from ideaflow.specflow import run


class SpecEditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.result = run("做一个校园二手书交易小程序", out_dir=cls.tmp.name,
                         agent=MockAgent(), discuss=True)
        cls.item = cls.result.payload["snapshot"]["items"][0]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_edit_creates_new_versions_and_keeps_old(self) -> None:
        result = edit_spec_item(
            self.result.out_dir, item_id=self.item["id"],
            text="学生可以发布二手教材并上传三张照片",
            actor="成员2", expected_version=self.result.spec.version,
        )
        self.assertEqual(result["version"], self.result.spec.version + 1)
        self.assertNotEqual(result["hash"], self.result.spec.hash)
        self.assertTrue((self.result.out_dir / "spec.v1.json").exists())
        self.assertTrue((self.result.out_dir / f"spec.v{result['version']}.json").exists())
        latest = json.loads((self.result.out_dir / "spec.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["version"], result["version"])
        snapshot = json.loads((self.result.out_dir / "snapshot.json").read_text(encoding="utf-8"))
        row = next(x for x in snapshot["items"] if x["id"] == self.item["id"])
        self.assertIn("三张照片", row["text"])

    def test_stale_version_is_rejected(self) -> None:
        with self.assertRaises(ContractError) as ctx:
            edit_spec_item(self.result.out_dir, item_id=self.item["id"],
                           text="新文本", actor="成员2",
                           expected_version=self.result.spec.version + 99)
        self.assertEqual(ctx.exception.code, "STALE_VERSION")

    def test_non_requirement_is_not_editable(self) -> None:
        snapshot = json.loads((self.result.out_dir / "snapshot.json").read_text(encoding="utf-8"))
        constraint = next((x for x in snapshot["items"] if x["type"] == "constraint"), None)
        if constraint is None:
            self.skipTest("本次快照没有约束条目")
        with self.assertRaises(ContractError) as ctx:
            edit_spec_item(self.result.out_dir, item_id=constraint["id"],
                           text="新文本", actor="成员2")
        self.assertEqual(ctx.exception.code, "NOT_EDITABLE")

    def test_unknown_item_is_rejected(self) -> None:
        with self.assertRaises(ContractError) as ctx:
            edit_spec_item(self.result.out_dir, item_id="R-999", text="新文本",
                           actor="成员2")
        self.assertEqual(ctx.exception.code, "UNKNOWN_ITEM")


if __name__ == "__main__":
    unittest.main()
