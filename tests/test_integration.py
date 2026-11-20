import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "contracts" / "v2"
sys.path.insert(0, str(ROOT / "modules" / "orchestrator" / "python"))

from agent import RealtimeAgent  # noqa: E402
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


def two_module_spec() -> dict:
    spec = json.loads((ROOT / "contracts" / "examples" / "spec.normal.json")
                      .read_text(encoding="utf-8"))
    first = spec["modules"][0]
    second = json.loads(json.dumps(first))
    second.update({
        "id": "M-02",
        "name": "预约接口",
        "responsibility": "对外提供预约创建与查询",
        "requirement_ids": ["R-02"],
        "depends_on": ["M-01"],
        "suggested_role": "ai",
    })
    spec["modules"] = [first, second]
    return spec


class SpecToPlanToRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_depends_on_maps_module_ids_to_task_ids(self):
        plan = plan_from_spec(two_module_spec())
        self.assertEqual([task["task_id"] for task in plan["tasks"]], ["T1", "T2"])
        self.assertEqual(plan["tasks"][1]["depends_on"], ["T1"])
        self.assertEqual(plan["tasks"][1]["design_refs"], ["M-02"])

    def test_unknown_module_dependency_rejected(self):
        spec = two_module_spec()
        spec["modules"][1]["depends_on"] = ["M-99"]
        with self.assertRaises(ValueError):
            plan_from_spec(spec)

    def test_plan_from_real_spec_shape_matches_schema(self):
        plan = plan_from_spec(two_module_spec(), plan_id="PLAN-001",
                              created_at="2026-11-20T11:00:00+08:00")
        files = {"enums.schema.json": load_schema("enums.schema.json"),
                 "plan.schema.json": load_schema("plan.schema.json")}
        validator = Draft202012Validator(inline(files["plan.schema.json"], files))
        self.assertEqual([], sorted(validator.iter_errors(plan), key=str))

    def test_plan_is_consumable_by_orchestrator(self):
        spec = two_module_spec()
        plan = plan_from_spec(spec, plan_id="PLAN-001",
                              created_at="2026-11-20T11:00:00+08:00")
        agent = RealtimeAgent(out_dir=self.out, mock=True,
                              upload_path=self.out / "uploaded.json")
        state = agent.start(plan, json.dumps(plan, ensure_ascii=False).encode("utf-8"))

        self.assertEqual(state["plan_id"], "PLAN-001")
        self.assertEqual(state["spec_version"], f"v{spec['version']}")
        self.assertEqual(state["total"], 2)
        self.assertEqual(len(state["execution_order"]), 2)
        self.assertEqual(agent.tasks["T2"]["deliverable"], spec["modules"][1]["deliverable"])
        self.assertEqual(agent.tasks["T2"]["acceptance_items"],
                         spec["modules"][1]["acceptance_items"])


if __name__ == "__main__":
    unittest.main()
