import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "contracts" / "v2"
sys.path.insert(0, str(ROOT / "modules" / "requirements"))

from ideaflow.specflow import load_case, run  # noqa: E402


def load_schema(name: str) -> dict:
    return json.loads((V2 / name).read_text(encoding="utf-8"))


def inline(node, by_file):
    if isinstance(node, dict):
        if "$ref" in node:
            file_name, _, fragment = node["$ref"].partition("#")
            target = by_file[file_name]
            for part in [p for p in fragment.split("/") if p]:
                target = target[part]
            return inline(target, by_file)
        return {key: inline(value, by_file) for key, value in node.items()}
    if isinstance(node, list):
        return [inline(item, by_file) for item in node]
    return node


def validator_for(name: str) -> Draft202012Validator:
    by_file = {path.name: load_schema(path.name) for path in V2.glob("*.json")}
    return Draft202012Validator(inline(load_schema(name), by_file))


class GeneratedArtifactContractTest(unittest.TestCase):
    """需求模块真实产物必须过 v2 契约，防止代码与 schema 悄悄分叉。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.result = run(load_case("campus-books"), out_dir=cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_snapshot_matches_contract(self):
        errors = sorted(validator_for("snapshot.schema.json")
                        .iter_errors(self.result.snapshot.to_json()), key=str)
        self.assertEqual([], [f"{list(error.absolute_path)} {error.message}" for error in errors])

    def test_spec_matches_contract(self):
        errors = sorted(validator_for("spec.schema.json")
                        .iter_errors(self.result.spec.to_json()), key=str)
        self.assertEqual([], [f"{list(error.absolute_path)} {error.message}" for error in errors])

    def test_spec_modules_have_delivery_fields(self):
        for module in self.result.spec.to_json()["modules"]:
            self.assertTrue(module["deliverable"])
            self.assertTrue(module["acceptance_items"])
            self.assertIn("write_paths", module)


class DiscussionEnrichedContractTest(unittest.TestCase):
    """带讨论产物的快照同样要过契约，防止"讨论补充"字段游离在 schema 之外。"""

    @classmethod
    def setUpClass(cls):
        from ideaflow.agents import MockAgent

        cls.tmp = tempfile.TemporaryDirectory()
        cls.result = run(load_case("campus-books"), out_dir=cls.tmp.name,
                         agent=MockAgent(), discuss=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_snapshot_with_discussion_matches_contract(self):
        errors = sorted(validator_for("snapshot.schema.json")
                        .iter_errors(self.result.snapshot.to_json()), key=str)
        self.assertEqual([], [f"{list(error.absolute_path)} {error.message}" for error in errors])

    def test_discussion_items_are_traceable(self):
        items = [item for item in self.result.snapshot.to_json()["items"]
                 if item["source"]["section"] == "讨论补充"]
        self.assertTrue(items, "讨论应当向快照补充条目")
        for item in items:
            self.assertIn(item["type"], {"requirement", "constraint", "open"})
            self.assertTrue(item["text"].strip())
        self.assertTrue(any(item["source"].get("actor") for item in items),
                        "讨论条目应记录提出者")


if __name__ == "__main__":
    unittest.main()
