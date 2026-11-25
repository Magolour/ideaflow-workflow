"""讨论收敛后的功能归纳：把观点与方案范围收成可落模块的清单。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .roles import Role
from .snapshot import Snapshot
from .storage import now_iso, write_json

FEATURE_ROLE = Role(
    id="feature",
    name="功能归纳",
    focus="把讨论结论收敛为可交付的功能，而不是复述问题或过程",
    must_answer=("哪些能力是首版必须有的？", "每条功能能追到哪条讨论结论？"),
    forbidden="不要把问题定义、关键路径、结构边界写成功能名。",
)


def build_context(idea: str, snapshot: Snapshot, board: Any) -> dict[str, Any]:
    return {
        "idea": idea,
        "snapshot": snapshot.to_json(),
        "claims": [{"id": row["id"], "text": row.get("text", ""),
                    "role": row.get("role", "")} for row in getattr(board, "claims", [])],
        "options": [{"id": row["id"], "name": row.get("name", ""),
                     "scope": list(row.get("scope") or [])}
                    for row in getattr(board, "options", [])],
    }


def synthesize(idea: str, snapshot: Snapshot, board: Any, agent: Any,
               out_dir: str | Path) -> dict[str, Any]:
    context = build_context(idea, snapshot, board)
    output = agent.respond("features", FEATURE_ROLE, context)
    features = output.get("features") if isinstance(output, dict) else None
    if not isinstance(features, list):
        raise ValueError("功能归纳没有返回 features")
    for index, feature in enumerate(features, 1):
        feature["id"] = f"F-{index:02d}"
    payload = {
        "snapshot_ref": {"snapshot_id": snapshot.snapshot_id,
                         "version": snapshot.version},
        "features": features,
        "created_at": now_iso(),
    }
    write_json(Path(out_dir) / "features.json", payload)
    return payload
