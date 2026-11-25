import json
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from test_api_gaps import ROOT, get_json, post_json, running_server, wait_for_progress
from spec_gate import _digest, attach_local_review

sys.path.insert(0, str(ROOT / "modules" / "requirements"))
from ideaflow.server import make_server


class SpecGateHTTPTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.spec = self.example("spec.normal.json")

    def example(self, name):
        return json.loads((ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8"))

    def rejected(self, base, payload, status, error):
        with self.assertRaises(HTTPError) as raised:
            post_json(f"{base}/api/start", payload)
        with raised.exception as response:
            self.assertEqual(response.code, status)
            body = json.load(response)
        self.assertEqual(body["error"], error)
        progress = get_json(f"{base}/api/progress")
        self.assertEqual((progress["status"], progress["total"], progress["plan_id"]),
                         ("created", 0, None))
        self.assertFalse((self.out / "plan.json").exists())
        return body["details"]

    def test_external_spec_requires_current_review_and_leaves_no_plan(self):
        stale = deepcopy(self.spec)
        stale["review"]["spec_ref"]["version"] += 1
        different_snapshot = deepcopy(self.spec)
        different_snapshot["review"]["snapshot_ref"]["hash"] = "b" * 64
        edited = deepcopy(self.spec)
        edited["modules"][0]["name"] = "修改后的模块"
        different_audit = deepcopy(self.spec)
        different_audit["review"]["audit"]["checks"] = {}
        wrong_status = {**self.spec, "status": "draft"}
        cases = (
            (self.example("spec.draft.json"), "SPEC_APPROVAL_REQUIRED"),
            (stale, "SPEC_AUDIT_MISMATCH"),
            (different_snapshot, "SPEC_AUDIT_MISMATCH"),
            (edited, "SPEC_AUDIT_MISMATCH"),
            (different_audit, "SPEC_AUDIT_MISMATCH"),
            (wrong_status, "SPEC_STATE_MISMATCH"),
        )
        with running_server(self.out) as (base, _), patch("entry.plan_from_spec") as plan:
            for spec, error in cases:
                with self.subTest(error=error):
                    self.rejected(base, spec, 409, error)
            plan.assert_not_called()

    def test_blocked_spec_without_question_reports_audit(self):
        audit = {**self.spec["review"]["audit"], "passed": False,
                 "counts": {"blocking": 1, "major": 0, "minor": 0, "total": 1},
                 "issues": [{"id": "AUD-01", "severity": "blocking"}]}
        audit["hash"] = _digest({key: audit[key] for key in ("spec_ref", "issues", "checks")})
        blocked = attach_local_review(self.spec, audit, None)
        with running_server(self.out) as (base, _):
            details = self.rejected(base, blocked, 422, "SPEC_BLOCKED_NO_QUESTION")
        self.assertEqual(details["audit"]["issues"], audit["issues"])

    def test_blocked_requirement_answer_is_reaudited_before_planning(self):
        raw = (ROOT / "modules" / "requirements" / "cases" / "campus-books-tight.md"
               ).read_text(encoding="utf-8")
        with running_server(self.out) as (base, _):
            details = self.rejected(base, {"requirement": raw}, 409, "SPEC_HUMAN_REQUIRED")
            question = details["question"]
            self.assertEqual(question["spec_ref"], details["audit"]["spec_ref"])
            self.assertTrue(question["question"])
            run_dir = self.out / "requirements" / details["run_id"]
            version = question["spec_ref"]["version"]
            original = run_dir / f"spec.v{version}.json"
            old_bytes = original.read_bytes()
            awaiting = json.loads(old_bytes)
            self.rejected(base, awaiting, 409, "SPEC_HUMAN_REQUIRED")
            command = {"run_id": run_dir.name, "spec_ref": question["spec_ref"],
                       "question_id": question["id"], "actor": "reviewer",
                       "option": "A", "answer": "同意按列出的新工期完成"}
            with make_server("127.0.0.1", 0, run_dir.parent, quiet=True) as requirements:
                worker = threading.Thread(target=requirements.serve_forever, daemon=True)
                worker.start()
                url = f"http://127.0.0.1:{requirements.server_address[1]}/api/spec/answer"
                try:
                    stale = {**command, "spec_ref": {**command["spec_ref"], "hash": "f" * 64}}
                    for invalid, status, code in (
                            (stale, 409, "STALE_SPEC_REF"),
                            ({**command, "option": "C"}, 422, "MANUAL_REVISION_REQUIRED")):
                        with self.assertRaises(HTTPError) as raised:
                            post_json(url, invalid)
                        with raised.exception as response:
                            self.assertEqual(response.code, status)
                            self.assertEqual(json.load(response)["error"]["code"], code)
                    self.assertEqual(original.read_bytes(), old_bytes)
                    self.assertFalse((run_dir / f"answer.v{version}.json").exists())
                    reviewed = post_json(url, command)
                    with self.assertRaises(HTTPError) as repeated:
                        post_json(url, command)
                    with repeated.exception as response:
                        self.assertEqual(response.code, 409)
                        self.assertEqual(json.load(response)["error"]["code"], "QUESTION_ALREADY_ANSWERED")
                finally:
                    requirements.shutdown()
                    worker.join()

            spec = reviewed["spec"]
            self.assertEqual(spec["version"], version + 1)
            self.assertEqual(spec["snapshot_ref"]["version"], awaiting["snapshot_ref"]["version"] + 1)
            self.assertEqual(spec["review"]["status"], "approved")
            self.assertTrue(reviewed["audit"]["passed"])
            self.assertEqual(original.read_bytes(), old_bytes)
            self.assertTrue((run_dir / f"answer.v{version}.json").exists())
            approved = post_json(f"{base}/api/start", spec)["progress"]
            self.assertEqual(approved["waiting_on"]["gate_type"], "plan_approval")
            self.assertEqual(approved["running"], 0)
            plan = json.loads((self.out / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["spec_ref"], spec["review"]["spec_ref"])
            post_json(f"{base}/api/events", {
                "type": "human_op", "action": "approve", "plan_id": approved["plan_id"],
                "plan_version": approved["plan_version"], "operator": "test",
            })
            for _ in range(approved["total"]):
                progress = wait_for_progress(base, lambda state: state["running"] > 0)
                task_id = next(row["task_id"] for row in progress["tasks"] if row["status"] == "running")
                post_json(f"{base}/api/events", {"type": "ai_report", "result": "done", "task_id": task_id})
                wait_for_progress(base, lambda state: any(
                    row["task_id"] == task_id and row["status"] == "done" for row in state["tasks"]))
            wait_for_progress(base, lambda state: state["status"] == "delivery_pending")
            post_json(f"{base}/api/events", {"type": "human_op", "action": "approve", "operator": "test"})
            completed = wait_for_progress(base, lambda state: state["status"] == "completed")
            self.assertEqual(completed["delivery"]["status"], "confirmed")
            self.assertEqual([row["target_type"] for row in completed["decisions"]], ["plan", "delivery"])

    def test_device_booking_review_from_requirements_is_used_in_plan(self):
        raw = (ROOT / "modules" / "requirements" / "cases" / "device-booking.md"
               ).read_text(encoding="utf-8")
        with running_server(self.out) as (base, _):
            progress = post_json(f"{base}/api/start", {"requirement": raw})["progress"]
        spec = json.loads((self.out / "spec.json").read_text(encoding="utf-8"))
        plan = json.loads((self.out / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["status"], "confirmed")
        self.assertEqual(spec["review"]["status"], "approved")
        self.assertEqual(spec["review"]["audit"]["counts"]["blocking"], 0)
        self.assertEqual(plan["spec_ref"], spec["review"]["spec_ref"])
        self.assertEqual(progress["waiting_on"]["gate_type"], "plan_approval")
        self.assertEqual(progress["running"], 0)
