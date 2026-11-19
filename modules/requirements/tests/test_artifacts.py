"""交付物测试：文件、清单、引用边与悬空引用校验。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ideaflow.artifacts import build_citations, deliver
from ideaflow.contracts import ContractError
from ideaflow.drafts import run_groups
from ideaflow.snapshot import build

CASE = Path(__file__).resolve().parents[1] / "cases" / "campus-books.md"


class DeliverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.snapshot = build(CASE.read_text(encoding="utf-8")).confirm("产品负责人")
        self.drafts = run_groups(self.snapshot)
        self.manifest = deliver(self.root, self.snapshot, self.drafts)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_writes_expected_files(self) -> None:
        expected = ["snapshot.v1.json", "drafts/DRAFT-A.json", "drafts/DRAFT-B.json",
                    "citations.json", "manifest.json"]
        for name in expected:
            self.assertTrue((self.root / name).exists(), name)
        self.assertEqual(sorted(self.manifest["files"]), sorted(expected[:-1]))

    def test_manifest_hashes_match_files(self) -> None:
        self.assertEqual(self.manifest["snapshot"]["hash"], self.snapshot.hash)
        for row in self.manifest["drafts"]:
            payload = json.loads((self.root / row["file"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["hash"], row["hash"])
            self.assertEqual(payload["draft_id"], row["draft_id"])

    def test_citations_cover_every_requirement(self) -> None:
        citations = json.loads((self.root / "citations.json").read_text(encoding="utf-8"))
        for group in ("A", "B"):
            cited = {edge["item"] for edge in citations["edges"]
                     if edge["draft"] == f"DRAFT-{group}"
                     and citations["sources"][edge["item"]]["type"] == "requirement"}
            self.assertEqual(cited, self.snapshot.ids("requirement"))
        self.assertEqual(citations["counts"]["drafts"], 2)

    def test_sources_point_back_to_input_lines(self) -> None:
        citations = json.loads((self.root / "citations.json").read_text(encoding="utf-8"))
        source = citations["sources"]["R-03"]
        self.assertEqual(source["start_line"], 5)
        self.assertEqual(source["section"], "校园二手书小程序")
        self.assertIn("在线付款", source["excerpt"])
        for edge in citations["edges"]:
            self.assertGreaterEqual(citations["sources"][edge["item"]]["start_line"], 1)

    def test_repeat_delivery_is_stable(self) -> None:
        again = deliver(self.root, self.snapshot, self.drafts)
        self.assertEqual(again["snapshot"]["hash"], self.manifest["snapshot"]["hash"])
        self.assertEqual(again["citations"]["hash"], self.manifest["citations"]["hash"])

    def test_dangling_citation_is_rejected(self) -> None:
        draft = self.drafts["A"]
        draft.claims[0]["constraint_ids"] = ["C-99"]
        with self.assertRaises(ContractError) as caught:
            build_citations(self.snapshot, self.drafts)
        self.assertEqual(caught.exception.code, "DANGLING_CITATION")

    def test_snapshot_mismatch_is_rejected(self) -> None:
        self.drafts["B"].snapshot_hash = "0" * 64
        with self.assertRaises(ContractError) as caught:
            build_citations(self.snapshot, self.drafts)
        self.assertEqual(caught.exception.code, "SNAPSHOT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
