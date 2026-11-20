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
}


def load(name: str) -> dict:
    return json.loads((V2 / name).read_text(encoding="utf-8"))


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = {key: load(path) for key, path in FILES.items()}
        cls.by_file = {path: cls.schemas[key] for key, path in FILES.items()}

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

    def test_openapi_exists(self):
        self.assertTrue((V2 / "openapi.yaml").is_file())

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
                self.assertTrue(list(self.validator(key).iter_errors(data)))

    def validator(self, key: str):
        return Draft202012Validator(self.inline(self.schemas[key]))


if __name__ == "__main__":
    unittest.main()
