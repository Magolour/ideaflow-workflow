"""修订测试：问题定位、范围裁剪、复核结果与记录持久化。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ideaflow.audit import audit_spec
from ideaflow.contracts import ContractError
from ideaflow.drafts import run_groups
from ideaflow.reviews import review_all
from ideaflow.revisions import apply_revision, save_revisions, scope_revision
from ideaflow.snapshot import build
from ideaflow.spec import build_spec, load_spec, save_spec

TIGHT = Path(__file__).resolve().parents[1] / "cases" / "campus-books-tight.md"
NORMAL = Path(__file__).resolve().parents[1] / "cases" / "campus-books.md"


class RevisionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = build(TIGHT.read_text(encoding="utf-8")).confirm("产品负责人")
        cls.drafts = run_groups(cls.snapshot)
        cls.spec = build_spec(cls.snapshot, cls.drafts,
                              review_all(cls.snapshot, cls.drafts))
        cls.audit = audit_spec(cls.spec, cls.snapshot)
        cls.revised, cls.record, cls.operations = scope_revision(
            cls.spec, cls.snapshot, cls.audit)
        cls.new_audit = audit_spec(cls.revised, cls.snapshot)

    def test_v1_has_deadline_and_requirement_issues(self) -> None:
        types = {row["type"] for row in self.audit["issues"]}
        self.assertIn("CONSTRAINT_CONFLICT", types)
        self.assertIn("REQUIREMENT_OVERRIDDEN", types)
        conflict = next(row for row in self.audit["issues"]
                        if row["type"] == "CONSTRAINT_CONFLICT")
        self.assertEqual(conflict["constraint_ids"], ["C-01"])

    def test_scope_revision_fits_deadline(self) -> None:
        self.assertEqual(self.revised.version, 2)
        self.assertEqual(self.spec.version, 1)
        self.assertLessEqual(self.revised.effort["commit_days"], 9)
        self.assertLess(self.revised.effort["commit_days"],
                        self.spec.effort["commit_days"])
        self.assertNotEqual(self.revised.hash, self.spec.hash)

    def test_revision_record_has_before_after_and_recheck(self) -> None:
        record = self.record
        self.assertEqual(record["id"], "REV-001")
        self.assertEqual(record["issue_types"], ["CONSTRAINT_CONFLICT"])
        self.assertIn("期限 9 天", record["reason"])
        self.assertEqual(record["before"]["version"], 1)
        self.assertEqual(record["after"]["version"], 2)
        self.assertEqual(len(record["changes"]), 2)
        self.assertEqual(record["recheck"]["resolved"], ["AUD-02"])
        self.assertFalse(record["recheck"]["passed"])
        self.assertEqual([row["type"] for row in record["recheck"]["remaining"]],
                         ["REQUIREMENT_OVERRIDDEN"])

    def test_recheck_clears_conflict_but_keeps_requirement(self) -> None:
        types = [row["type"] for row in self.new_audit["issues"]]
        self.assertNotIn("CONSTRAINT_CONFLICT", types)
        self.assertIn("REQUIREMENT_OVERRIDDEN", types)
        self.assertEqual(self.new_audit["counts"]["blocking"], 1)

    def test_scope_cut_changes_choices(self) -> None:
        requirements = [op["requirement_id"] for op in self.operations]
        self.assertEqual(requirements, ["R-04", "R-01"])
        self.assertIn("收藏夹页面", self.revised.decisions[3]["choice"])
        self.assertIn("去掉图片与草稿", self.revised.decisions[0]["choice"])

    def test_revisions_are_kept_on_spec(self) -> None:
        self.assertEqual(len(self.revised.revisions), 1)
        self.assertEqual(self.revised.revisions[0]["id"], "REV-001")

    def test_save_and_load_keeps_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            save_spec(self.revised, folder)
            save_revisions(self.revised, folder)
            self.assertTrue((Path(folder) / "revisions.json").exists())
            self.assertTrue((Path(folder) / "revision.v2.json").exists())
            loaded = load_spec(folder)
            self.assertEqual(loaded.hash, self.revised.hash)
            self.assertEqual(loaded.revisions[0]["recheck"]["resolved"], ["AUD-02"])

    def test_no_fix_when_no_conflict(self) -> None:
        snapshot = build(NORMAL.read_text(encoding="utf-8")).confirm("产品负责人")
        drafts = run_groups(snapshot)
        spec = build_spec(snapshot, drafts, review_all(snapshot, drafts))
        audit = audit_spec(spec, snapshot)
        with self.assertRaises(ContractError) as caught:
            scope_revision(spec, snapshot, audit)
        self.assertEqual(caught.exception.code, "NO_AUTO_FIX")

    def test_unknown_requirement_is_rejected(self) -> None:
        with self.assertRaises(ContractError) as caught:
            apply_revision(self.spec, self.snapshot, self.audit,
                           [{"op": "update_decision", "requirement_id": "R-99",
                             "cost_days": 1.0}], reason="测试")
        self.assertEqual(caught.exception.code, "UNKNOWN_REQUIREMENT")

    def test_no_change_is_rejected(self) -> None:
        decision = self.spec.decisions[0]
        with self.assertRaises(ContractError) as caught:
            apply_revision(self.spec, self.snapshot, self.audit,
                           [{"op": "update_decision",
                             "requirement_id": decision["requirement_ids"][0],
                             "choice": decision["choice"],
                             "cost_days": decision["cost_days"]}],
                           reason="没有实际变化")
        self.assertEqual(caught.exception.code, "NO_CHANGE")


if __name__ == "__main__":
    unittest.main()
