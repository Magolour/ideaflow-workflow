import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "contracts" / "v2"
EXAMPLES = ROOT / "contracts" / "examples"

FILES = {
    "enums": "enums.schema.json",
    "idea": "idea.schema.json",
    "snapshot": "snapshot.schema.json",
    "draft": "draft.schema.json",
    "spec": "spec.schema.json",
    "plan": "plan.schema.json",
    "event": "event.schema.json",
    "decision": "decision.schema.json",
    "artifact": "artifact.schema.json",
    "delivery": "delivery.schema.json",
    "features": "features.schema.json",
    "evidence": "evidence.schema.json",
    "summary": "summary.schema.json",
}

NORMAL = {
    "idea": "idea.normal.json",
    "snapshot": "snapshot.normal.json",
    "draft": "draft.normal.json",
    "spec": "spec.normal.json",
    "plan": "plan.normal.json",
    "event": "event.normal.json",
    "decision": "decision.normal.json",
    "artifact": "artifact.normal.json",
    "delivery": "delivery.normal.json",
    "features": "features.normal.json",
    "evidence": "evidence.normal.json",
    "summary": "summary.normal.json",
}

# 每份异常样例要触发的具体规则，避免 schema 松动后样例"因别的原因"误过
EXPECTED_FAILURE = {
    "idea": "non-empty",
    "snapshot": "does not match",
    "draft": "minimum",
    "spec": "non-empty",
    "plan": "is not one of",
    "event": "minimum",
    "decision": "reason",
    "artifact": "path",
    "delivery": "decided_by",
    "features": "source_refs",
    "evidence": "required",
    "summary": "minimum",
}

INVALID = {
    "idea": "idea.invalid.json",
    "snapshot": "snapshot.invalid.json",
    "draft": "draft.invalid.json",
    "spec": "spec.invalid.json",
    "plan": "plan.invalid.json",
    "event": "event.invalid.json",
    "decision": "decision.invalid.json",
    "artifact": "artifact.invalid.json",
    "delivery": "delivery.invalid.json",
    "features": "features.invalid.json",
    "evidence": "evidence.invalid.json",
    "summary": "summary.invalid.json",
}


def load(name: str) -> dict:
    return json.loads((V2 / name).read_text(encoding="utf-8"))


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = {key: load(path) for key, path in FILES.items()}
        cls.by_file = {path: cls.schemas[key] for key, path in FILES.items()}
        cls.by_file["spec-review.schema.json"] = load("spec-review.schema.json")

    def inline(self, node):
        """把跨文件 $ref 展开，避免测试依赖网络或 resolver 版本。"""
        if isinstance(node, dict):
            if "$ref" in node:
                target = self._resolve(node["$ref"])
                return self.inline(target)
            return {key: self.inline(value) for key, value in node.items()}
        if isinstance(node, list):
            return [self.inline(item) for item in node]
        return node

    def _resolve(self, ref: str):
        file_name, _, fragment = ref.partition("#")
        target = self.by_file[file_name]
        for part in [p for p in fragment.split("/") if p]:
            target = target[part]
        return target

    def test_schemas_are_valid(self):
        for key, schema in self.schemas.items():
            with self.subTest(schema=key):
                Draft202012Validator.check_schema(schema)

    def test_openapi_is_parseable(self):
        """文件存在不算契约，能被解析、有 paths 才算。"""
        try:
            import yaml
        except ImportError:  # pragma: no cover
            self.skipTest("未安装 pyyaml")
        api = yaml.safe_load((V2 / "openapi.yaml").read_text(encoding="utf-8"))
        self.assertIn("openapi", api)
        self.assertIn("/api/start", api["paths"])
        self.assertIn("/api/progress", api["paths"])

    def test_normal_examples_pass(self):
        for key, filename in NORMAL.items():
            with self.subTest(example=filename):
                data = json.loads((EXAMPLES / filename).read_text(encoding="utf-8"))
                errors = sorted(self.validator(key).iter_errors(data), key=str)
                self.assertEqual([], errors)

    def test_every_contract_has_both_examples(self):
        documented = [key for key in FILES if key != "enums"]
        for key in documented:
            with self.subTest(contract=key):
                self.assertIn(key, NORMAL)
                self.assertIn(key, INVALID)

    def test_invalid_examples_fail(self):
        for key, filename in INVALID.items():
            with self.subTest(example=filename):
                data = json.loads((EXAMPLES / filename).read_text(encoding="utf-8"))
                errors = [error.message for error in self.validator(key).iter_errors(data)]
                self.assertTrue(errors, "异常样例必须被拒绝")
                expected = EXPECTED_FAILURE[key]
                self.assertTrue(any(expected in message for message in errors),
                                f"应因 {expected} 失败，实际：{errors}")

    def test_openapi_declares_new_paths_and_fields(self):
        """v2.1 新增的接口与字段必须出现在契约里，不能只在文档里说。"""
        try:
            import yaml
        except ImportError:  # pragma: no cover
            self.skipTest("未安装 pyyaml")
        api = yaml.safe_load((V2 / "openapi.yaml").read_text(encoding="utf-8"))
        for path in ("/api/spec/item", "/api/graph/edit", "/api/replay", "/api/summary"):
            self.assertIn(path, api["paths"])
        schemas = api["components"]["schemas"]
        progress = schemas["Progress"]["properties"]
        for field in ("edges", "mode", "phase", "plan_review", "spec"):
            self.assertIn(field, progress, f"Progress 缺字段 {field}")
        task = schemas["TaskState"]["properties"]
        for field in ("kind", "started_at", "finished_at", "evidence"):
            self.assertIn(field, task, f"TaskState 缺字段 {field}")

    def test_enums_cover_kind_mode_and_unverified(self):
        defs = self.schemas["enums"]["$defs"]
        self.assertIn("unverified", defs["acceptanceResult"]["enum"])
        self.assertEqual(["code", "test", "review", "doc"], defs["taskKind"]["enum"])
        self.assertEqual(["mock", "live"], defs["runMode"]["enum"])

    def test_plan_accepts_kind_and_test_task_id(self):
        """测试任务用 T1T 这类后缀编号，且 kind 必须是四种类型之一。"""
        plan = json.loads((EXAMPLES / "plan.normal.json").read_text(encoding="utf-8"))
        plan["tasks"] = plan["tasks"][:1]
        plan["tasks"][0].update({"task_id": "T1T", "kind": "test"})
        plan["tasks"][0].pop("depends_on", None)
        errors = [error.message for error in self.validator("plan").iter_errors(plan)]
        self.assertEqual([], errors)

        plan["tasks"][0]["kind"] = "build"
        errors = [error.message for error in self.validator("plan").iter_errors(plan)]
        self.assertTrue(any("is not one of" in message for message in errors),
                        f"非法 kind 应被拒绝，实际：{errors}")

    def test_endpoint_examples_match_components(self):
        """新接口的联调样例必须对得上 openapi 的组件 schema。"""
        try:
            import yaml
        except ImportError:  # pragma: no cover
            self.skipTest("未安装 pyyaml")
        api = yaml.safe_load((V2 / "openapi.yaml").read_text(encoding="utf-8"))
        by_file = {path.name: load(path.name) for path in V2.glob("*.json")}
        by_file.update(api["components"]["schemas"])
        cases = {
            "spec_item.request.json": "SpecItemPatch",
            "graph_edit.request.json": "GraphEditRequest",
            "graph_edit.invalid.json": "GraphEditRequest",
            "graph_edit.result.json": "GraphEditResult",
            "replay.result.json": "ReplayResult",
        }
        def resolve(node):
            if isinstance(node, dict):
                if "$ref" in node:
                    ref = node["$ref"]
                    if ref.startswith("#/components/schemas/"):
                        return resolve(api["components"]["schemas"][ref.rsplit("/", 1)[-1]])
                    file_name, _, fragment = ref.partition("#")
                    target = by_file[file_name]
                    for part in [p for p in fragment.split("/") if p]:
                        target = target[part]
                    return resolve(target)
                return {key: resolve(value) for key, value in node.items()}
            if isinstance(node, list):
                return [resolve(item) for item in node]
            return node

        for filename, name in cases.items():
            with self.subTest(example=filename):
                data = json.loads((EXAMPLES / filename).read_text(encoding="utf-8"))
                validator = Draft202012Validator(resolve(by_file[name]))
                errors = [error.message for error in validator.iter_errors(data)]
                if "invalid" in filename:
                    self.assertTrue(errors, f"{filename} 必须被拒绝")
                else:
                    self.assertEqual([], errors, f"{filename}: {errors}")

    def test_new_paths_declare_status_codes(self):
        """四个新接口的状态码必须写清，防止实现时随手改。"""
        try:
            import yaml
        except ImportError:  # pragma: no cover
            self.skipTest("未安装 pyyaml")
        api = yaml.safe_load((V2 / "openapi.yaml").read_text(encoding="utf-8"))
        expected = {
            "/api/spec/item": {"200", "400", "404", "409", "422"},
            "/api/graph/edit": {"200", "400", "409", "422"},
            "/api/replay": {"200", "400"},
            "/api/summary": {"200", "409"},
        }
        for path, codes in expected.items():
            with self.subTest(path=path):
                item = api["paths"][path]
                method = next(iter(item))
                declared = set(item[method]["responses"])
                self.assertTrue(codes <= declared, f"{path} 缺 {sorted(codes - declared)}")

    def validator(self, key: str):
        return Draft202012Validator(self.inline(self.schemas[key]))


if __name__ == "__main__":
    unittest.main()
