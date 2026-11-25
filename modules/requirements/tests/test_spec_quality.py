"""规格质量：功能到需求映射、审计分母、验收条件与估时。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ideaflow.audit import audit_spec
from ideaflow.drafts import run_groups
from ideaflow.features import claim_requirements
from ideaflow.reviews import review_all
from ideaflow.snapshot import build
from ideaflow.spec import build_spec
from ideaflow.spec_doc import export_spec

IDEA = (
    "做一个志愿平台。活动发布。扫码签到。志愿时长。组队报名。"
    "活动审核。报名管理。签到统计。时长认定。团队协作。"
    "两周内上线，只有两名开发。"
)
NAMES = ["活动发布", "扫码签到", "志愿时长", "组队报名", "活动审核",
         "报名管理", "签到统计", "时长认定", "团队协作"]


class VolunteerBoard:
    claims = [{"id": f"CLM-{index:03d}", "text": name, "role": "ba"}
              for index, name in enumerate(NAMES, 1)]
    options: list[dict] = []


def make_spec():
    snapshot = build(IDEA).confirm("测试负责人")
    drafts = run_groups(snapshot)
    for draft in drafts.values():
        for index, decision in enumerate(draft.decisions, 1):
            decision["cost_days"] = round(1.0 + index * 0.5, 1)
        draft.packages = [{
            "name": f"实现 {decision['requirement_ids'][0]}",
            "optimistic": round(decision["cost_days"] * 0.8, 1),
            "likely": decision["cost_days"],
            "pessimistic": round(decision["cost_days"] * 1.6, 1),
        } for decision in draft.decisions]
        draft.packages.append({"name": "联调、验收与演示准备",
                               "optimistic": 2.0, "likely": 3.0, "pessimistic": 5.0})
    features = {"features": [{
        "id": f"F-{index:02d}", "name": name, "description": f"{name}能力",
        "source_refs": [f"CLM-{index:03d}"], "inferred": index % 3 == 0,
        "priority": "high",
    } for index, name in enumerate(NAMES, 1)]}
    claim_refs = claim_requirements(snapshot, VolunteerBoard())
    spec = build_spec(snapshot, drafts, review_all(snapshot, drafts),
                      features=features, claim_refs=claim_refs)
    return snapshot, spec


class FeatureSpecQualityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot, cls.spec = make_spec()

    def test_modules_resolve_claims_to_requirements(self) -> None:
        for module in self.spec.modules:
            self.assertTrue(module["requirement_ids"], module["name"])
            self.assertTrue(any(ref.startswith("R-") for ref in module["requirement_ids"]))
        self.assertFalse([row for row in self.spec.mapping if not row["requirement_id"]])

    def test_acceptance_has_owner_and_specific_criterion(self) -> None:
        criteria = [row["criterion"] for row in self.spec.acceptance]
        self.assertEqual(len(criteria), len(self.spec.modules))
        self.assertTrue(all(row["requirement_id"] for row in self.spec.acceptance))
        self.assertEqual(len(criteria), len(set(criteria)))
        for name in NAMES:
            self.assertIn(name, " ".join(criteria))

    def test_audit_counts_features_not_zero(self) -> None:
        audit = audit_spec(self.spec, self.snapshot)
        coverage = audit["checks"]["coverage"]
        self.assertEqual(coverage["requirements"], len(self.spec.modules))
        self.assertEqual(coverage["with_module"], coverage["requirements"])
        self.assertEqual(coverage["with_decision"], coverage["requirements"])
        self.assertEqual(coverage["with_acceptance"], coverage["requirements"])

    def test_estimates_come_from_work_packages(self) -> None:
        values = [module["estimate_days"] for module in self.spec.modules]
        self.assertGreater(len(set(values)), 1, values)

    def test_open_items_are_merged(self) -> None:
        questions = [row["question"] for row in self.spec.open_items]
        self.assertLessEqual(len(questions), 2, questions)
        self.assertFalse(any("待确认对应需求" in question for question in questions))

    def test_export_keeps_spec_pure(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            export_spec(self.spec, self.snapshot, folder)
            pure = json.loads((Path(folder) / "spec.v1.json").read_text(encoding="utf-8"))
            bundle = json.loads((Path(folder) / "spec.bundle.json").read_text(encoding="utf-8"))
        self.assertNotIn("artifacts", pure)
        self.assertIn("artifacts", bundle)
        self.assertEqual(bundle["artifacts"]["json"], "spec.v1.json")

    def test_unmapped_feature_falls_back_to_feature_id(self) -> None:
        snapshot = build("做一个志愿平台。活动发布。扫码签到。").confirm("测试负责人")
        drafts = run_groups(snapshot)
        features = {"features": [
            {"id": f"F-{index:02d}", "name": f"功能{index}", "description": "无",
             "source_refs": ["CLM-999"], "inferred": True, "priority": "high"}
            for index in range(1, 4)
        ]}
        spec = build_spec(snapshot, drafts, review_all(snapshot, drafts), features=features)
        self.assertTrue(all(module["requirement_ids"][0] == f"F-{index:02d}"
                            for index, module in enumerate(spec.modules, 1)))
        audit = audit_spec(spec, snapshot)
        self.assertEqual(audit["checks"]["coverage"]["requirements"], 3)
        self.assertEqual(audit["checks"]["coverage"]["with_module"], 3)

    def test_missing_module_or_acceptance_blocks(self) -> None:
        import copy

        missing_module = copy.deepcopy(self.spec)
        missing_module.modules = missing_module.modules[1:]
        audit = audit_spec(missing_module, self.snapshot)
        self.assertIn("COVERAGE_MISSING_MODULE",
                      [row["type"] for row in audit["issues"]])

        missing_acceptance = copy.deepcopy(self.spec)
        requirement_id = missing_acceptance.modules[0]["requirement_ids"][0]
        missing_acceptance.acceptance = [
            row for row in missing_acceptance.acceptance
            if row["requirement_id"] != requirement_id]
        audit = audit_spec(missing_acceptance, self.snapshot)
        self.assertIn("COVERAGE_MISSING_ACCEPTANCE",
                      [row["type"] for row in audit["issues"]])


if __name__ == "__main__":
    unittest.main()
