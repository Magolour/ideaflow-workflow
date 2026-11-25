"""需求快照测试：分类、编号、来源行号、版本与哈希。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ideaflow.contracts import ContractError
from ideaflow.snapshot import Snapshot, _discussion_items, build, load, save

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


class DiscussionMappingTest(unittest.TestCase):
    """讨论结论进快照的规则：去重、封顶、可追溯。"""

    class Board:
        def __init__(self, assumptions, questions=(), objections=(), non_goals=(),
                     options=()):
            self.assumptions = list(assumptions)
            self.open_questions = list(questions)
            self.objections = list(objections)
            self.non_goals = list(non_goals)
            self.options = list(options)

        def unresolved_blocking(self):
            return [row for row in self.objections if row.get("severity") == "blocking"]

    def test_duplicate_assumptions_collapse(self):
        board = self.Board([
            {"text": "用户集中在校内", "role": "ba", "round": 1},
            {"text": "用户集中在校内。", "role": "risk", "round": 2},
            {"text": "用户 集中 在校内", "role": "tech", "round": 2},
        ])
        items = _discussion_items(board)
        self.assertEqual(len(items), 1)

    def test_items_do_not_repeat_raw_text(self):
        board = self.Board([{"text": "两周内出演示版", "role": "ba", "round": 1}])
        items = _discussion_items(board, existing=["两周内出演示版"])
        self.assertEqual(items, [])

    def test_limit_keeps_must_first(self):
        board = self.Board(
            [{"text": f"假设 {index}", "role": "ba", "round": 1} for index in range(10)],
            questions=[{"text": "必须回答的问题", "blocking": True, "source": "ba"}],
        )
        items = _discussion_items(board, limit=3)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["priority"], "must")

    def test_limit_never_drops_must(self):
        board = self.Board(
            [],
            questions=[{"text": f"阻断 {index}", "blocking": True, "source": "ba"}
                       for index in range(5)],
        )
        items = _discussion_items(board, limit=2)
        self.assertEqual(len(items), 5)
        self.assertTrue(all(row["priority"] == "must" for row in items))

    def test_source_carries_actor_and_round(self):
        board = self.Board([{"text": "用户集中在校内", "role": "risk", "round": 2}])
        source = _discussion_items(board)[0]["source"]
        self.assertEqual(source["actor"], "risk")
        self.assertEqual(source["round"], 2)

    def test_option_scopes_become_requirements_before_optional_assumptions(self):
        """一句话需求也要能拆：方案范围不能被推断假设挤出可选名额。"""
        board = self.Board(
            [{"text": f"推断假设 {index}", "role": "ba", "round": 1}
             for index in range(5)],
            options=[{"id": "OPT-A", "role": "synthesizer",
                      "scope": ["聊天会话", "好友通讯录", "消息通知"]}],
        )
        items = _discussion_items(board, limit=8)
        scopes = [row["text"] for row in items if row["type"] == "requirement"]
        self.assertEqual(scopes, ["范围：聊天会话", "范围：好友通讯录", "范围：消息通知"])
        self.assertTrue(all(row["priority"] == "should"
                            for row in items if row["type"] == "requirement"))

    def test_option_scope_dedupes_with_existing_text(self):
        board = self.Board(
            [], options=[{"id": "OPT-A", "scope": ["两周内出演示版"]}])
        items = _discussion_items(board, existing=["两周内出演示版"])
        self.assertEqual(items, [])
