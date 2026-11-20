import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "contracts" / "v2"
sys.path.insert(0, str(ROOT / "modules" / "orchestrator" / "python"))

from plan_adapter import plan_from_spec  # noqa: E402


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


class PlanAdapterTest(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads(
            (ROOT / "contracts" / "examples" / "spec.normal.json").read_text(encoding="utf-8")
        )

    def test_plan_from_spec(self):
        plan = plan_from_spec(self.spec, created_at="2026-11-20T11:00:00+08:00")
        self.assertEqual(plan["spec_ref"]["spec_id"], self.spec["spec_id"])
        self.assertEqual(plan["tasks"][0]["task_id"], "T1")
        self.assertEqual(plan["tasks"][0]["design_refs"], ["M-01"])
        self.assertIn("R-01", plan["tasks"][0]["requirement_refs"])
        self.assertEqual(plan["status"], "draft")

    def test_plan_matches_schema(self):
        plan = plan_from_spec(self.spec, created_at="2026-11-20T11:00:00+08:00")
        files = {
            "enums.schema.json": load_schema("enums.schema.json"),
            "plan.schema.json": load_schema("plan.schema.json"),
        }
        validator = Draft202012Validator(inline(files["plan.schema.json"], files))
        self.assertEqual([], sorted(validator.iter_errors(plan), key=str))

    def test_empty_modules_rejected(self):
        with self.assertRaises(ValueError):
            plan_from_spec({"spec_id": "SPEC-1", "version": 1, "modules": []})

    def test_human_role_requires_review(self):
        spec = json.loads(json.dumps(self.spec))
        spec["modules"][0]["suggested_role"] = "human"
        plan = plan_from_spec(spec)
        self.assertTrue(plan["tasks"][0]["review_required"])


if __name__ == "__main__":
    unittest.main()
