"""可视化模块（成员5）的回归断言。

两类检查：
  1. 页面锚点：计划号/规格版本徽标、按 layers 并排的依赖图、任务详情（来源/产物）、
     决策记录、交付确认按钮 —— 契约 v2 里成员5 的五项都要在页面上有落点；
  2. 夹具契约：modules/visualization/progress_fixture.py 返回的 Progress 必须过
     contracts/v2/openapi.yaml 的 Progress、decision.schema.json、delivery.schema.json，
     这样等后端透传字段时，前端渲染的正是契约里的结构。

运行：python -m unittest tests.test_visualization -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "contracts" / "v2"
PAGE = ROOT / "modules" / "orchestrator" / "web" / "index.html"
sys.path.insert(0, str(ROOT / "modules" / "visualization"))


def load_schema(name: str) -> dict:
    return json.loads((V2 / name).read_text(encoding="utf-8"))


def inline(node, by_file):
    """把跨文件的 $ref 就地展开，便于用一个 Draft202012Validator 校验。"""
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            if ref.startswith("#/components/schemas/"):
                return inline(by_file[ref.rsplit("/", 1)[-1]], by_file)
            file_name, _, fragment = ref.partition("#")
            target = by_file[file_name]
            for part in [p for p in fragment.split("/") if p]:
                target = target[part]
            return inline(target, by_file)
        return {key: inline(value, by_file) for key, value in node.items()}
    if isinstance(node, list):
        return [inline(item, by_file) for item in node]
    return node


class PageAnchorTest(unittest.TestCase):
    """验收要点必须在页面上有对应锚点，避免「文档写了、页面没有」。"""

    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")

    def test_page_exists_and_is_self_contained(self):
        self.assertGreater(len(self.html), 20000, "工作台应是自包含的单文件页面")
        for external in ("<script src=", "<link rel=\"stylesheet\""):
            self.assertNotIn(external, self.html, "页面不应依赖外部构建产物")

    def test_plan_and_spec_badges(self):
        self.assertIn('id="bPlan"', self.html)
        self.assertIn('id="bSpec"', self.html)
        self.assertIn("plan_id", self.html)
        self.assertIn("spec_version", self.html)

    def test_graph_rendered_by_layers(self):
        self.assertIn('id="graph"', self.html)
        self.assertIn("state.layers", self.html)
        self.assertIn("第 ", self.html)  # 层头「第 N 层 · 并行 k」
        self.assertIn("并行 ", self.html)

    def test_task_detail_shows_source_and_artifacts(self):
        for field in ("deliverable", "acceptance_items", "requirement_refs",
                      "design_refs", "artifact_ids", "depends_on"):
            self.assertIn(field, self.html, f"任务详情缺少 {field}")

    def test_decisions_and_delivery(self):
        self.assertIn('data-tab="decisions"', self.html)
        self.assertIn("state.decisions", self.html)
        self.assertIn('id="btnDeliverOk', self.html)
        self.assertIn("delivery_pending", self.html)

    def test_human_gate_shows_reason_and_actions(self):
        self.assertIn("waiting_on", self.html)
        self.assertIn("放行", self.html)
        self.assertIn("驳回", self.html)
        self.assertIn("已等待", self.html)


class FixtureContractTest(unittest.TestCase):
    """夹具的 Progress 必须逐字段过契约，前端才不是照着「想象中的字段」写的。"""

    @classmethod
    def setUpClass(cls):
        try:
            import yaml
        except ImportError:  # pragma: no cover
            raise unittest.SkipTest("未安装 pyyaml")
        import progress_fixture as fixture

        cls.progress = fixture.Fixture().progress()
        openapi = yaml.safe_load((V2 / "openapi.yaml").read_text(encoding="utf-8"))
        schemas = {path.name: load_schema(path.name) for path in V2.glob("*.json")}
        for name in ("Progress", "TaskState", "Edge"):
            schemas[name] = openapi["components"]["schemas"][name]
        cls.schemas = schemas
        cls.by_file = schemas

    def validate(self, schema: dict, payload) -> list[str]:
        validator = Draft202012Validator(inline(schema, self.by_file))
        return [f"{list(error.absolute_path)} {error.message}"
                for error in sorted(validator.iter_errors(payload), key=str)]

    def test_progress_matches_contract(self):
        errors = self.validate(self.schemas["Progress"], self.progress)
        self.assertEqual([], errors)

    def test_tasks_carry_provenance_and_artifacts(self):
        for task in self.progress["tasks"]:
            errors = self.validate(self.schemas["TaskState"], task)
            self.assertEqual([], errors, task["task_id"])
        self.assertTrue(all(t["requirement_refs"] for t in self.progress["tasks"]))
        self.assertTrue(all(t["design_refs"] for t in self.progress["tasks"]))
        self.assertTrue(any(t.get("artifact_ids") for t in self.progress["tasks"]))

    def test_edges_pass_contract(self):
        for edge in self.progress["edges"]:
            self.assertEqual([], self.validate(self.schemas["Edge"], edge))

    def test_decisions_pass_contract(self):
        self.assertGreaterEqual(len(self.progress["decisions"]), 2)
        for row in self.progress["decisions"]:
            self.assertEqual([], self.validate(load_schema("decision.schema.json"), row))

    def test_delivery_pending_has_draft_delivery(self):
        self.assertEqual("delivery_pending", self.progress["status"])
        delivery = self.progress["delivery"]
        self.assertEqual("draft", delivery["status"])
        self.assertEqual([], self.validate(load_schema("delivery.schema.json"), delivery))
        self.assertEqual(len(self.progress["tasks"]), len(delivery["items"]))
        self.assertEqual("delivery_confirm", self.progress["waiting_on"]["gate_type"])

    def test_confirming_delivery_completes_instance(self):
        import progress_fixture as fixture

        fx = fixture.Fixture()
        fx.human_op({"type": "human_op", "action": "approve", "operator": "tester"})
        payload = fx.progress()
        self.assertEqual("completed", payload["status"])
        self.assertEqual("confirmed", payload["delivery"]["status"])
        self.assertEqual("human:tester", payload["delivery"]["decided_by"])
        self.assertIsNone(payload["waiting_on"])
        self.assertEqual([], self.validate(load_schema("delivery.schema.json"), payload["delivery"]))
        self.assertIn("delivery", [row["target_type"] for row in payload["decisions"]])

    def test_reject_requires_reason(self):
        import progress_fixture as fixture

        fx = fixture.Fixture()
        with self.assertRaises(ValueError):
            fx.human_op({"type": "human_op", "action": "reject", "operator": "tester"})


if __name__ == "__main__":
    unittest.main()
