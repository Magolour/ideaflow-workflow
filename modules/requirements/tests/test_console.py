"""控制台数据接口：运行列表与单次运行聚合。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ideaflow.server import load_run, scan_runs


class ConsoleDataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        run = self.base / "20260101-000000-spec-demo"
        (run / "discussion" / "d1").mkdir(parents=True)
        (run / "spec.json").write_text(json.dumps({
            "spec_id": "SPEC-001", "version": 1, "status": "awaiting_human",
            "modules": [{"id": "M-01", "name": "登录"}],
        }), encoding="utf-8")
        (run / "snapshot.json").write_text(json.dumps({
            "source": {"raw_text": "做一个登录页"}, "items": [],
        }), encoding="utf-8")
        (run / "features.json").write_text(json.dumps({
            "features": [{"id": "F-01", "name": "登录"}],
        }), encoding="utf-8")
        (run / "discussion" / "d1" / "blackboard.json").write_text(json.dumps({
            "claims": [{"id": "CLM-001", "role": "ba", "text": "先做登录"}],
            "objections": [],
        }), encoding="utf-8")
        (run / "discussion" / "d1" / "events.jsonl").write_text(
            json.dumps({"seq": 1, "actor": "system", "action": "run_created"}) + "\n",
            encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_scan_runs_reads_summary(self) -> None:
        rows = scan_runs(self.base)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["idea"], "做一个登录页")
        self.assertEqual(rows[0]["features"], 1)
        self.assertEqual(rows[0]["modules"], 1)
        self.assertEqual(rows[0]["status"], "awaiting_human")

    def test_load_run_aggregates_files(self) -> None:
        run_id = scan_runs(self.base)[0]["id"]
        payload = load_run(self.base, run_id)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["spec"]["spec_id"], "SPEC-001")
        self.assertEqual(payload["features"]["features"][0]["name"], "登录")
        self.assertEqual(payload["board"]["claims"][0]["id"], "CLM-001")
        self.assertEqual(payload["events"][0]["seq"], 1)

    def test_unknown_or_unsafe_run_is_rejected(self) -> None:
        self.assertIsNone(load_run(self.base, "missing"))
        self.assertIsNone(load_run(self.base, "../etc"))


if __name__ == "__main__":
    unittest.main()
