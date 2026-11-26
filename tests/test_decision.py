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


def decision_validator() -> Draft202012Validator:
    by_file = {name: load_schema(name) for name in ("decision.schema.json", "enums.schema.json")}
    return Draft202012Validator(inline(load_schema("decision.schema.json"), by_file))


TASKS = {
    "tasks": [
        {"task_id": "T1", "name": "确认需求边界", "suggested_role": "human"},
        {"task_id": "T2", "name": "整理验收清单", "suggested_role": "human"},
    ]
}


class DecisionRecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.out = Path(self.tmp.name)
        self.agent = RealtimeAgent(out_dir=self.out, mock=True,
                                   upload_path=self.out / "uploaded.json")
        self.agent.start(TASKS, json.dumps(TASKS).encode("utf-8"))
        self.agent.process({"type": "human_op", "action": "approve"})
        self.assertEqual(self.agent.waiting_on["object_id"], "T1")

    def tearDown(self):
        self.tmp.cleanup()

    def decisions(self):
        path = self.out / "decisions.jsonl"
        if not path.exists():
            return []
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        return [row for row in records if row["target_type"] == "task"]

    def test_approve_records_valid_decision(self):
        self.agent._human_op({"action": "approve", "operator": "tester"})

        records = self.decisions()
        self.assertEqual(len(records), 1)
        record = records[0]
        decision_validator().validate(record)
        self.assertRegex(record["decision_id"], r"^DEC-[A-Za-z0-9_-]+$")
        self.assertEqual(record["target_type"], "task")
        self.assertEqual(record["target_id"], "T1")
        self.assertEqual(record["action"], "approve")
        self.assertEqual(record["actor"], "human:tester")

    def test_approve_emits_decision_event(self):
        self.agent._human_op({"action": "approve", "operator": "tester"})

        actions = [event["action"] for event in self.agent.log]
        self.assertIn("decision_recorded", actions)

    def test_reject_without_reason_is_rejected(self):
        with self.assertRaises(ValueError):
            self.agent._human_op({"action": "reject", "operator": "tester"})
        self.assertEqual(self.decisions(), [])
        self.assertEqual(self.agent.waiting_on["object_id"], "T1")

    def test_reject_records_reason_and_blocks_downstream(self):
        self.agent._human_op({"action": "reject", "operator": "tester",
                              "reason": "需求边界不清楚，先退回"})

        records = self.decisions()
        self.assertEqual(len(records), 1)
        record = records[0]
        decision_validator().validate(record)
        self.assertEqual(record["action"], "reject")
        self.assertEqual(record["reason"], "需求边界不清楚，先退回")
        self.assertEqual(self.agent.tasks["T1"]["status"], "blocked")
        self.assertEqual(self.agent.tasks["T2"]["status"], "blocked")

    def test_skip_records_decision(self):
        self.agent._human_op({"action": "skip", "operator": "tester",
                              "reason": "本轮不验收"})

        record = self.decisions()[0]
        decision_validator().validate(record)
        self.assertEqual(record["action"], "skip")
        self.assertEqual(self.agent.tasks["T1"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
