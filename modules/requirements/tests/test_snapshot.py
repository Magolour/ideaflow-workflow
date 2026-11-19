"""需求快照测试：分类、编号、来源行号、版本与哈希。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ideaflow.contracts import ContractError
from ideaflow.snapshot import Snapshot, build, load, save

RAW = """# 校园二手书小程序

学生可以发布二手教材。
学生可以按书名搜索教材。
买家可以在线付款并线下自提。

约束：两周内出演示版，只有两名开发。
首版不做在线支付。

是否需要信用评价？
"""


class ParseTest(unittest.TestCase):
    def test_classifies_three_kinds(self) -> None:
        snapshot = build(RAW)
        self.assertEqual([item["text"] for item in snapshot.requirements],
                         ["学生可以发布二手教材", "学生可以按书名搜索教材",
                          "买家可以在线付款并线下自提"])
        self.assertEqual([item["text"] for item in snapshot.constraints],
                         ["两周内出演示版", "只有两名开发", "首版不做在线支付"])
        self.assertEqual([item["text"] for item in snapshot.questions], ["是否需要信用评价？"])

    def test_ids_are_sequential_per_type(self) -> None:
        snapshot = build(RAW)
        self.assertEqual([item["id"] for item in snapshot.requirements],
                         ["R-01", "R-02", "R-03"])
        self.assertEqual([item["id"] for item in snapshot.constraints], ["C-01", "C-02", "C-03"])
        self.assertEqual([item["id"] for item in snapshot.questions], ["Q-01"])
        self.assertEqual(snapshot.counts(), {"requirement": 3, "constraint": 3, "open": 1})

    def test_source_keeps_line_and_section(self) -> None:
        snapshot = build(RAW)
        publish = snapshot.requirements[0]
        self.assertEqual(publish["source"]["start_line"], 3)
        self.assertEqual(publish["source"]["end_line"], 3)
        self.assertEqual(publish["source"]["section"], "校园二手书小程序")
        self.assertIn("发布二手教材", publish["source"]["excerpt"])

    def test_explicit_prefix_wins(self) -> None:
        snapshot = build("需求：支持导出 Excel\n约束：预算不超过两万元\n问题：谁来做验收？")
        self.assertEqual(snapshot.requirements[0]["text"], "支持导出 Excel")
        self.assertEqual(snapshot.constraints[0]["text"], "预算不超过两万元")
        self.assertEqual(snapshot.questions[0]["text"], "谁来做验收？")

    def test_extra_constraints_and_questions(self) -> None:
        snapshot = build("做一个排班工具", constraints=["仅限内部使用"],
                         questions=["是否需要移动端？"])
        texts = {(item["type"], item["text"]) for item in snapshot.items}
        self.assertIn(("constraint", "仅限内部使用"), texts)
        self.assertIn(("open", "是否需要移动端？"), texts)

    def test_empty_input_is_rejected(self) -> None:
        with self.assertRaises(ContractError) as caught:
            build("   \n\n")
        self.assertEqual(caught.exception.code, "EMPTY_INPUT")

    def test_duplicate_lines_are_merged(self) -> None:
        snapshot = build("学生可以发布教材。学生可以发布教材。")
        self.assertEqual(len(snapshot.requirements), 1)


class VersionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = build(RAW)

    def test_hash_is_stable_for_same_content(self) -> None:
        self.assertEqual(build(RAW).hash, self.snapshot.hash)

    def test_edit_bumps_version_and_keeps_history(self) -> None:
        revised = self.snapshot.revise(
            [{"op": "update", "id": "C-01", "text": "三周内出演示版"}],
            reason="与团队确认实际工期", actor="pm")
        self.assertEqual(revised.version, 2)
        self.assertEqual(revised.parent_version, 1)
        self.assertEqual(revised.status, "draft")
        self.assertNotEqual(revised.hash, self.snapshot.hash)
        self.assertEqual(revised.requirements[0]["text"], "学生可以发布二手教材")
        self.assertEqual(revised.history[-1]["reason"], "与团队确认实际工期")
        self.assertEqual(revised.diff(self.snapshot)[0]["field"], "text")

    def test_no_change_is_rejected(self) -> None:
        with self.assertRaises(ContractError) as caught:
            self.snapshot.revise([{"op": "update", "id": "R-01",
                                   "text": "学生可以发布二手教材"}], reason="无变化")
        self.assertEqual(caught.exception.code, "NO_CHANGE")

    def test_removed_id_is_not_reused(self) -> None:
        revised = self.snapshot.revise([{"op": "remove", "id": "Q-01"}],
                                       reason="该问题已澄清")
        revised = revised.revise([{"op": "add", "type": "open", "text": "是否需要站内消息？"}],
                                 reason="补充新问题")
        self.assertIn("Q-01", revised.retired_ids)
        self.assertEqual(revised.questions[0]["id"], "Q-02")

    def test_confirm_changes_status_only(self) -> None:
        confirmed = self.snapshot.confirm("产品负责人")
        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.confirmed_by, "产品负责人")
        self.assertEqual(confirmed.version, 1)
        self.assertEqual(confirmed.hash, self.snapshot.hash)

        with self.assertRaises(ContractError):
            confirmed.confirm("另一个人")

    def test_revise_after_confirm_returns_to_draft(self) -> None:
        confirmed = self.snapshot.confirm("产品负责人")
        revised = confirmed.revise([{"op": "add", "type": "requirement",
                                     "text": "支持按价格排序"}], reason="补充要求")
        self.assertEqual(revised.status, "draft")
        self.assertIsNone(revised.confirmed_by)
        self.assertEqual(revised.version, 2)

    def test_unknown_reference_is_rejected(self) -> None:
        with self.assertRaises(ContractError) as caught:
            self.snapshot.ensure_known(["R-01", "R-99"])
        self.assertEqual(caught.exception.code, "UNKNOWN_REQUIREMENT")


class PersistenceTest(unittest.TestCase):
    def test_save_keeps_old_versions(self) -> None:
        snapshot = build(RAW)
        revised = snapshot.revise([{"op": "add", "type": "requirement",
                                    "text": "支持按价格排序"}], reason="补充要求")
        with tempfile.TemporaryDirectory() as folder:
            save(snapshot, folder)
            save(revised, folder)
            self.assertTrue((Path(folder) / "snapshot.v1.json").exists())
            self.assertTrue((Path(folder) / "snapshot.v2.json").exists())

            latest = load(folder)
            self.assertEqual(latest.version, 2)
            self.assertEqual(latest.hash, revised.hash)

            old = Snapshot.from_json(json.loads(
                (Path(folder) / "snapshot.v1.json").read_text(encoding="utf-8")))
            self.assertEqual(old.hash, snapshot.hash)

    def test_tampered_hash_is_detected(self) -> None:
        payload = build(RAW).to_json()
        payload["items"][0]["text"] = "被改过的内容"
        with self.assertRaises(ContractError) as caught:
            Snapshot.from_json(payload)
        self.assertEqual(caught.exception.code, "HASH_MISMATCH")


if __name__ == "__main__":
    unittest.main()
