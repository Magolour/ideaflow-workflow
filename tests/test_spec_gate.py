import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from test_api_gaps import ROOT, get_json, post_json, running_server
from spec_gate import _digest, attach_local_review


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

    def test_actual_blocked_requirement_returns_question_and_leaves_start_available(self):
        raw = (ROOT / "modules" / "requirements" / "cases" / "campus-books-tight.md"
               ).read_text(encoding="utf-8")
        with running_server(self.out) as (base, _):
            details = self.rejected(base, {"requirement": raw}, 409, "SPEC_HUMAN_REQUIRED")
            question = details["question"]
            self.assertEqual(question["spec_ref"], details["audit"]["spec_ref"])
            self.assertTrue(question["question"])
            run_dir = self.out / "requirements" / details["run_id"]
            spec = json.loads((run_dir / "spec.json").read_text(encoding="utf-8"))
            awaiting = attach_local_review(spec, details["audit"], run_dir.name)
            self.rejected(base, awaiting, 409, "SPEC_HUMAN_REQUIRED")

            # The approved contract fixture stands in for the producer's reviewed response.
            # The producer's answer endpoint is exercised by its owning module.
            approved = post_json(f"{base}/api/start", self.spec)["progress"]
            self.assertEqual(approved["waiting_on"]["gate_type"], "plan_approval")
            self.assertEqual(approved["running"], 0)
            plan = json.loads((self.out / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["spec_ref"], self.spec["review"]["spec_ref"])

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
