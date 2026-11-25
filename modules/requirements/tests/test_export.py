"""规格导出测试：同版 JSON / Markdown、互相引用与篡改检测。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ideaflow.contracts import ContractError
from ideaflow.drafts import run_groups
from ideaflow.reviews import review_all
from ideaflow.snapshot import build
from ideaflow.spec import build_spec
from ideaflow.spec_doc import export_spec, render_spec_markdown, verify_export

CASE = Path(__file__).resolve().parents[1] / "cases" / "campus-books.md"


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = build(CASE.read_text(encoding="utf-8")).confirm("产品负责人")
        cls.drafts = run_groups(cls.snapshot)
        cls.spec = build_spec(cls.snapshot, cls.drafts,
                              review_all(cls.snapshot, cls.drafts))

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.result = export_spec(self.spec, self.snapshot, self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_writes_versioned_and_latest_files(self) -> None:
        for name in ("spec.v1.json", "spec.v1.md", "spec.json", "spec.md",
                     "spec.bundle.json"):
            self.assertTrue((self.root / name).exists(), name)

    def test_same_content_hash_in_both_formats(self) -> None:
        self.assertEqual(self.result["content_hash"], self.spec.hash)
        markdown = (self.root / "spec.v1.md").read_text(encoding="utf-8")
        self.assertIn(self.spec.hash, markdown)
        payload = json.loads((self.root / "spec.v1.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["hash"], self.spec.hash)
        self.assertNotIn("artifacts", payload, "spec 文件必须保持纯规格，不带导出元数据")
        bundle = json.loads((self.root / "spec.bundle.json").read_text(encoding="utf-8"))
        self.assertEqual(bundle["artifacts"]["markdown"], "spec.v1.md")
        self.assertEqual(bundle["artifacts"]["json"], "spec.v1.json")
        self.assertEqual(bundle["artifacts"]["content_hash"], self.spec.hash)

    def test_verify_passes_and_checks_are_explicit(self) -> None:
        check = verify_export(self.root, 1)
        self.assertTrue(check["ok"], check["checks"])
        self.assertTrue(all(check["checks"].values()))
        self.assertEqual(check["content_hash"], self.spec.hash)

    def test_markdown_contains_required_sections(self) -> None:
        markdown = render_spec_markdown(self.spec, self.snapshot)
        for heading in ("## 2 需求追溯", "## 3 设计决策", "## 4 模块与接口",
                        "## 5 验收条件", "## 6 明确不做", "## 7 待决问题", "## 8 估算"):
            self.assertIn(heading, markdown)
        self.assertIn("| R-03 |", markdown)
        self.assertIn("不满足", markdown)
        self.assertIn("publishBook", markdown)

    def test_export_is_deterministic(self) -> None:
        first = (self.root / "spec.v1.md").read_text(encoding="utf-8")
        export_spec(self.spec, self.snapshot, self.root)
        second = (self.root / "spec.v1.md").read_text(encoding="utf-8")
        self.assertEqual(first, second)

    def test_tampered_markdown_fails_verification(self) -> None:
        (self.root / "spec.v1.md").write_text("被替换的内容\n", encoding="utf-8")
        check = verify_export(self.root, 1)
        self.assertFalse(check["ok"])
        self.assertFalse(check["checks"]["hash_in_markdown"])

    def test_tampered_json_fails_verification(self) -> None:
        payload = json.loads((self.root / "spec.v1.json").read_text(encoding="utf-8"))
        payload["decisions"][0]["choice"] = "被改过的选择"
        (self.root / "spec.v1.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        check = verify_export(self.root, 1)
        self.assertFalse(check["ok"])
        self.assertFalse(check["checks"]["json_integrity"])

    def test_missing_version_is_reported(self) -> None:
        with self.assertRaises(ContractError) as caught:
            verify_export(self.root, 9)
        self.assertEqual(caught.exception.code, "EXPORT_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
