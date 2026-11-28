"""规格人工确认：只允许无阻断、无待决问题的规格升版为 confirmed。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from .contracts import ContractError
from .snapshot import load as load_snapshot
from .spec import load_spec, save_spec
from .spec_doc import export_spec
from .storage import now_iso, read_json, write_json


def confirm_spec(run_dir: str | Path, *, actor: str, reason: str) -> dict[str, Any]:
    root = Path(run_dir)
    if not (root / "spec.json").is_file():
        raise ContractError("UNKNOWN_RUN", "找不到规格运行", "run_id")
    actor, reason = actor.strip(), reason.strip()
    if not actor or not reason:
        raise ContractError("INVALID_BODY", "确认人和理由不能为空", "confirm")

    spec = load_spec(root)
    if spec.status == "confirmed":
        return {"spec_id": spec.spec_id, "version": spec.version,
                "status": spec.status, "already": True}
    if spec.escalation:
        raise ContractError("UNRESOLVED_QUESTION", "仍有待人工决策的问题", "question")
    audit = read_json(root / "audit.json") if (root / "audit.json").exists() else {}
    blocking = int((audit.get("counts") or {}).get("blocking", 0))
    if blocking:
        raise ContractError("AUDIT_BLOCKED", f"仍有 {blocking} 条阻断，不能确认", "audit")

    snapshot = load_snapshot(root / f"snapshot.v{spec.snapshot_version}.json")
    confirmed = replace(spec, version=spec.version + 1, status="confirmed",
                        created_at=now_iso())
    save_spec(confirmed, root)
    export_spec(confirmed, snapshot, root)
    flow_path = root / "flow.json"
    if flow_path.exists():
        flow = read_json(flow_path)
        flow["status"] = confirmed.status
        flow["spec"] = {"id": confirmed.spec_id, "version": confirmed.version,
                        "hash": confirmed.hash}
        flow["confirmed_by"] = actor
        flow["confirmed_reason"] = reason
        write_json(flow_path, flow)
    return {"spec_id": confirmed.spec_id, "version": confirmed.version,
            "status": confirmed.status, "hash": confirmed.hash,
            "confirmed_by": actor, "reason": reason}
