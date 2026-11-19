"""赛马执行：组级失败隔离、尝试记录与单独重试。

一次赛马不是一个事务：某组失败只影响它自己，另一组的结果照常保留。
每次尝试都写入 `attempts`，重试只挑 `failed` 的组，已完成的组不会被重跑，
因此它们的产物哈希在重试前后保持不变——这正是可以对外证明的一点。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .contracts import ContractError
from .drafts import Draft, build_draft
from .snapshot import Snapshot, load as load_snapshot
from .storage import now_iso, read_json, write_json

GROUP_STATES = ("pending", "running", "done", "failed")
STATE_LABEL = {"pending": "待执行", "running": "执行中", "done": "成功", "failed": "失败"}


@dataclass
class GroupState:
    group: str
    state: str = "pending"
    attempts: int = 0
    draft_hash: str | None = None
    error: dict[str, str] | None = None
    duration_ms: int = 0
    started_at: str | None = None
    finished_at: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "state": self.state,
            "state_label": STATE_LABEL[self.state],
            "attempts": self.attempts,
            "draft_hash": self.draft_hash,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class Race:
    snapshot_id: str
    snapshot_version: int
    snapshot_hash: str
    groups: dict[str, GroupState]
    attempts: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    drafts: dict[str, Draft] = field(default_factory=dict)

    @property
    def done_groups(self) -> list[str]:
        return [group for group, state in self.groups.items() if state.state == "done"]

    @property
    def failed_groups(self) -> list[str]:
        return [group for group, state in self.groups.items() if state.state == "failed"]

    def to_json(self) -> dict[str, Any]:
        return {
            "snapshot_ref": {"snapshot_id": self.snapshot_id,
                             "version": self.snapshot_version,
                             "hash": self.snapshot_hash},
            "created_at": self.created_at,
            "groups": {group: state.to_json() for group, state in self.groups.items()},
            "attempts": self.attempts,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "Race":
        try:
            groups = {group: GroupState(group=group,
                                        state=row.get("state", "pending"),
                                        attempts=int(row.get("attempts", 0)),
                                        draft_hash=row.get("draft_hash"),
                                        error=row.get("error"),
                                        duration_ms=int(row.get("duration_ms", 0)),
                                        started_at=row.get("started_at"),
                                        finished_at=row.get("finished_at"))
                      for group, row in payload["groups"].items()}
            return cls(snapshot_id=payload["snapshot_ref"]["snapshot_id"],
                       snapshot_version=int(payload["snapshot_ref"]["version"]),
                       snapshot_hash=payload["snapshot_ref"]["hash"],
                       groups=groups,
                       attempts=list(payload.get("attempts", [])),
                       created_at=payload.get("created_at", now_iso()))
        except KeyError as error:
            raise ContractError("INVALID_RACE", f"赛马记录缺少字段 {error}", "race") from None


def _record_attempt(race: Race, group: str, attempt: int, state: GroupState,
                    started: float, outcome: dict[str, Any]) -> None:
    race.attempts.append({
        "group": group,
        "attempt": attempt,
        "state": outcome["state"],
        "at": state.started_at,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "hash": outcome.get("hash"),
        "error": outcome.get("error"),
    })


def _attempt(race: Race, snapshot: Snapshot, group: str, attempt: int,
             *, simulate_failure: bool = False) -> None:
    state = race.groups[group]
    state.state = "running"
    state.attempts = attempt
    state.started_at = now_iso()
    started = time.perf_counter()

    try:
        if simulate_failure:
            raise ContractError("SIMULATED_FAILURE",
                                f"{group} 组第 {attempt} 次尝试被模拟为失败", "group")
        draft = build_draft(snapshot, group, attempt=attempt)
    except ContractError as error:
        state.state = "failed"
        state.error = {"code": error.code, "message": error.message, "path": error.path}
        outcome = {"state": "failed", "error": state.error}
    except Exception as error:  # noqa: BLE001 - 任何异常都归到组级失败，不中断其他组
        state.state = "failed"
        state.error = {"code": "INTERNAL_ERROR",
                       "message": f"{type(error).__name__}: {error}", "path": ""}
        outcome = {"state": "failed", "error": state.error}
    else:
        state.state = "done"
        state.error = None
        state.draft_hash = draft.hash
        race.drafts[group] = draft
        outcome = {"state": "done", "hash": draft.hash}

    state.duration_ms = int((time.perf_counter() - started) * 1000)
    state.finished_at = now_iso()
    _record_attempt(race, group, attempt, state, started, outcome)


def run_race(snapshot: Snapshot, *, groups: tuple[str, ...] = ("A", "B"),
             fail_groups: dict[str, int] | None = None, max_attempts: int = 1,
             on_attempt: Callable[[Race, str, int], None] | None = None) -> Race:
    """跑一次赛马。`fail_groups={"B": 1}` 表示 B 组首次尝试注入一次失败。"""
    if snapshot.status != "confirmed":
        raise ContractError("SNAPSHOT_NOT_CONFIRMED", "开始赛马前必须先确认快照", "status")
    if max_attempts < 1:
        raise ContractError("INVALID_FIELD", "max_attempts 至少为 1", "max_attempts")

    remaining = {group: int((fail_groups or {}).get(group, 0)) for group in groups}
    race = Race(snapshot_id=snapshot.snapshot_id, snapshot_version=snapshot.version,
                snapshot_hash=snapshot.hash,
                groups={group: GroupState(group=group) for group in groups})

    for group in groups:
        for attempt in range(1, max_attempts + 1):
            simulate = remaining[group] > 0
            if simulate:
                remaining[group] -= 1
            _attempt(race, snapshot, group, attempt, simulate_failure=simulate)
            if on_attempt is not None:
                on_attempt(race, group, attempt)
            if race.groups[group].state == "done":
                break
    return race


def save_race(race: Race, directory: str | Path) -> Path:
    root = Path(directory)
    for draft in race.drafts.values():
        write_json(root / "drafts" / f"{draft.draft_id}.json", draft.to_json())
    return write_json(root / "race.json", race.to_json())


def load_race(directory: str | Path) -> Race:
    return Race.from_json(read_json(Path(directory) / "race.json"))


def load_drafts(race: Race, directory: str | Path) -> None:
    """把已成功组的草案读回内存，重试时不需要重跑它们。"""
    root = Path(directory)
    for group, state in race.groups.items():
        if state.state != "done":
            continue
        path = root / "drafts" / f"DRAFT-{group}.json"
        if path.exists():
            race.drafts[group] = Draft.from_json(read_json(path))


def retry_failed(directory: str | Path, *, max_attempts: int = 1) -> Race:
    """只重跑失败的组；已完成的组不重跑，产物哈希保持不变。"""
    root = Path(directory)
    race = load_race(root)
    snapshot = load_snapshot(root)
    load_drafts(race, root)

    for group in race.failed_groups:
        start_attempt = race.groups[group].attempts + 1
        for attempt in range(start_attempt, start_attempt + max_attempts):
            _attempt(race, snapshot, group, attempt)
            if race.groups[group].state == "done":
                break
    save_race(race, root)
    return race


def unchanged_groups(before: Race, after: Race) -> dict[str, bool]:
    """重试前后，已成功组的哈希是否保持不变。"""
    result: dict[str, bool] = {}
    for group, state in after.groups.items():
        if state.state != "done":
            continue
        previous = before.groups.get(group)
        if previous is None or previous.state != "done":
            continue
        result[group] = previous.draft_hash == state.draft_hash
    return result
