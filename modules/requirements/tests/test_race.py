"""赛马执行测试：组级失败隔离、尝试记录与单独重试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ideaflow.contracts import ContractError
from ideaflow.drafts import run_groups
from ideaflow.race import (load_race, retry_failed, run_race, save_race,
                           unchanged_groups)
from ideaflow.snapshot import build, load, save

CASE = Path(__file__).resolve().parents[1] / "cases" / "campus-books.md"


class RaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.snapshot = build(CASE.read_text(encoding="utf-8")).confirm("产品负责人")
        save(self.snapshot, self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_failure_is_isolated_to_one_group(self) -> None:
        race = run_race(self.snapshot, fail_groups={"B": 1}, max_attempts=1)
        self.assertEqual(race.groups["A"].state, "done")
        self.assertEqual(race.groups["B"].state, "failed")
        self.assertEqual(race.groups["B"].error["code"], "SIMULATED_FAILURE")
        self.assertIn("A", race.drafts)
        self.assertNotIn("B", race.drafts)

        save_race(race, self.root)
        self.assertTrue((self.root / "drafts" / "DRAFT-A.json").exists())
        self.assertFalse((self.root / "drafts" / "DRAFT-B.json").exists())
        self.assertTrue((self.root / "race.json").exists())

    def test_retry_runs_only_the_failed_group(self) -> None:
        race = run_race(self.snapshot, fail_groups={"B": 1}, max_attempts=1)
        save_race(race, self.root)
        before = load_race(self.root)
        a_hash = before.groups["A"].draft_hash

        after = retry_failed(self.root)

        self.assertEqual(after.groups["B"].state, "done")
        self.assertEqual(after.groups["A"].draft_hash, a_hash)
        self.assertEqual(after.groups["A"].attempts, 1)
        self.assertEqual(after.groups["B"].attempts, 2)
        self.assertEqual(unchanged_groups(before, after), {"A": True})
        self.assertTrue((self.root / "drafts" / "DRAFT-B.json").exists())

        b_records = [row for row in after.attempts if row["group"] == "B"]
        self.assertEqual([row["state"] for row in b_records], ["failed", "done"])

    def test_auto_retry_when_attempts_allowed(self) -> None:
        race = run_race(self.snapshot, fail_groups={"B": 1}, max_attempts=2)
        self.assertEqual(race.groups["B"].state, "done")
        self.assertEqual(race.groups["B"].attempts, 2)
        self.assertEqual(race.groups["A"].attempts, 1)
        b_records = [row for row in race.attempts if row["group"] == "B"]
        self.assertEqual(len(b_records), 2)

    def test_all_groups_can_fail_without_raising(self) -> None:
        race = run_race(self.snapshot, fail_groups={"A": 1, "B": 1}, max_attempts=1)
        self.assertEqual(race.failed_groups, ["A", "B"])
        self.assertEqual(race.drafts, {})
        self.assertEqual(len(race.attempts), 2)

    def test_retry_without_failures_is_a_noop(self) -> None:
        race = run_race(self.snapshot, max_attempts=1)
        save_race(race, self.root)
        count = len(race.attempts)
        after = retry_failed(self.root)
        self.assertEqual(len(after.attempts), count)
        self.assertEqual(after.failed_groups, [])

    def test_race_roundtrip(self) -> None:
        race = run_race(self.snapshot, max_attempts=1)
        save_race(race, self.root)
        loaded = load_race(self.root)
        self.assertEqual(loaded.groups["A"].state, "done")
        self.assertEqual(loaded.groups["A"].draft_hash, race.groups["A"].draft_hash)
        self.assertEqual(len(loaded.attempts), len(race.attempts))

    def test_unconfirmed_snapshot_is_rejected(self) -> None:
        draft_snapshot = build(CASE.read_text(encoding="utf-8"))
        with self.assertRaises(ContractError) as caught:
            run_race(draft_snapshot)
        self.assertEqual(caught.exception.code, "SNAPSHOT_NOT_CONFIRMED")

    def test_retry_keeps_group_hashes_for_unchanged_groups(self) -> None:
        full = run_groups(self.snapshot)
        race = run_race(self.snapshot, fail_groups={"B": 1}, max_attempts=1)
        save_race(race, self.root)
        after = retry_failed(self.root)
        self.assertEqual(after.drafts["A"].hash, full["A"].hash)
        self.assertEqual(after.drafts["B"].hash, full["B"].hash)

    def test_retry_reloads_snapshot(self) -> None:
        race = run_race(self.snapshot, fail_groups={"B": 1}, max_attempts=1)
        save_race(race, self.root)
        retry_failed(self.root)
        self.assertEqual(load(self.root).hash, self.snapshot.hash)


if __name__ == "__main__":
    unittest.main()
