import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "contracts" / "v2"
sys.path.insert(0, str(ROOT / "modules" / "requirements"))
sys.path.insert(0, str(ROOT / "modules" / "orchestrator" / "python"))

IDEA = "我要做微信"
PROCESS_WORDS = ("问题定义", "关键路径", "全部范围", "结构边界", "剩余预算")


class NoRefAgent:
    """功能只引用主张，不引用需求编号。"""

    def __new__(cls):
        from ideaflow.agents import MockAgent

        class Agent(MockAgent):
            def respond(self, stage, role, context):
                if stage == "features":
                    return {"features": [
                        {"name": name, "description": f"{name}能力",
                         "source_refs": ["CLM-001"], "inferred": True,
                         "priority": "high"}
                        for name in ("聊天会话", "好友通讯录", "消息通知",
                                     "群聊", "朋友圈")]}
                return super().respond(stage, role, context)

        return Agent()


def load_schema(name: str) -> dict:
    return json.loads((V2 / name).read_text(encoding="utf-8"))


def inline(node, by_file):
    if isinstance(node, dict):
        if "$ref" in node:
            name, _, fragment = node["$ref"].partition("#")
            target = by_file[name]
            for part in [p for p in fragment.split("/") if p]:
                target = target[part]
            return inline(target, by_file)
        return {key: inline(value, by_file) for key, value in node.items()}
    if isinstance(node, list):
        return [inline(item, by_file) for item in node]
    return node


class FeatureSynthesisTest(unittest.TestCase):
    """一句话需求应先归纳成功能清单，再落模块与任务。"""

    @classmethod
    def setUpClass(cls):
        from ideaflow.agents import MockAgent
        from ideaflow.specflow import run

        cls.tmp = tempfile.TemporaryDirectory()
        cls.result = run(IDEA, out_dir=cls.tmp.name, agent=MockAgent(), discuss=True)
        cls.features_path = cls.result.out_dir / "features.json"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def features(self) -> dict:
        return json.loads(self.features_path.read_text(encoding="utf-8"))

    def test_features_file_exists_and_matches_contract(self):
        self.assertTrue(self.features_path.exists(), "缺少 features.json")
        by_file = {path.name: load_schema(path.name) for path in V2.glob("*.json")}
        validator = Draft202012Validator(inline(load_schema("features.schema.json"), by_file))
        errors = sorted(validator.iter_errors(self.features()), key=str)
        self.assertEqual([], [f"{list(e.absolute_path)} {e.message}" for e in errors])

    def test_features_are_functional(self):
        features = self.features()["features"]
        self.assertGreaterEqual(len(features), 5)
        self.assertLessEqual(len(features), 10)
        for feature in features:
            for word in PROCESS_WORDS:
                self.assertNotIn(word, feature["name"],
                                 f"功能名仍是过程约束：{feature['name']}")

    def test_features_have_sources(self):
        for feature in self.features()["features"]:
            self.assertTrue(feature["source_refs"], f"{feature['id']} 缺少来源")
            self.assertIsInstance(feature["inferred"], bool)

    def test_modules_come_from_features(self):
        names = {feature["name"] for feature in self.features()["features"]}
        modules = self.result.spec.to_json()["modules"]
        self.assertTrue(modules)
        for module in modules:
            self.assertIn(module["name"], names,
                          f"模块 {module['name']} 不在功能清单里")
        # 功能只引用部分需求时，未被引用的需求不应被当成“未覆盖”阻断。
        self.assertTrue(self.result.audits[-1]["passed"],
                        self.result.audits[-1]["issues"])
        self.assertEqual(self.result.spec.status, "draft")

    def test_invalid_feature_count_is_rejected(self):
        from ideaflow.agents import MockAgent
        from ideaflow.contracts import ContractError
        from ideaflow.specflow import run

        class TooFewAgent(MockAgent):
            def respond(self, stage, role, context):
                if stage == "features":
                    return {"features": [
                        {"name": "聊天会话", "description": "聊天",
                         "source_refs": ["R-01"], "inferred": True,
                         "priority": "high"}]}
                return super().respond(stage, role, context)

        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ContractError) as caught:
                run(IDEA, out_dir=folder, agent=TooFewAgent(), discuss=True)
        self.assertEqual(caught.exception.code, "FEATURE_COUNT")

    def test_more_than_twelve_modules_is_rejected(self):
        from ideaflow.contracts import ContractError
        from ideaflow.spec import build_spec

        features = {"features": [
            {"id": f"F-{index:02d}", "name": f"功能{index}", "description": "x",
             "source_refs": ["R-01"], "inferred": True, "priority": "high"}
            for index in range(1, 14)
        ]}
        with self.assertRaises(ContractError) as caught:
            build_spec(self.result.snapshot, self.result.race.drafts,
                       self.result.reviews, features=features)
        self.assertEqual(caught.exception.code, "TOO_MANY_MODULES")

    def test_duplicate_module_names_are_disambiguated(self):
        from ideaflow.spec import _unique_module_name

        used: set[str] = set()
        names = [_unique_module_name("聊天会话", used) for _ in range(3)]
        self.assertEqual(names, ["聊天会话", "聊天会话（2）", "聊天会话（3）"])

    def test_features_without_requirement_refs_still_export(self):
        """功能只引用主张时，规格仍能生成与导出，不被覆盖率误拦。"""
        from ideaflow.specflow import run

        with tempfile.TemporaryDirectory() as folder:
            result = run(IDEA, out_dir=folder, agent=NoRefAgent(), discuss=True)
            self.assertEqual(len(result.spec.modules), 5)
            payload = json.loads((result.out_dir / "spec.v1.json").read_text(encoding="utf-8"))
            payload.pop("artifacts", None)  # 导出层附加字段，不属于 spec 契约
            by_file = {path.name: load_schema(path.name) for path in V2.glob("*.json")}
            validator = Draft202012Validator(inline(load_schema("spec.schema.json"), by_file))
            errors = sorted(validator.iter_errors(payload), key=str)
            self.assertEqual([], [f"{list(e.absolute_path)} {e.message}" for e in errors])

    def test_unmapped_features_survive_tight_deadline(self):
        from ideaflow.specflow import run

        with tempfile.TemporaryDirectory() as folder:
            result = run(IDEA, constraints=["一周内完成"], out_dir=folder,
                         agent=NoRefAgent(), discuss=True)
            self.assertEqual(result.spec.status, "awaiting_human")
            self.assertEqual(result.spec.revisions, [])

    def test_plan_granularity(self):
        from plan_adapter import plan_from_spec

        plan = plan_from_spec(self.result.spec.to_json())
        self.assertGreaterEqual(len(plan["tasks"]), 5)
        self.assertLessEqual(len(plan["tasks"]), 20)
        for task in plan["tasks"]:
            self.assertLessEqual(task.get("estimate_days", 0), 3.0,
                                 f"{task['task_id']} 估时过长，需要拆分")


if __name__ == "__main__":
    unittest.main()
