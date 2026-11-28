"""需求单条文本编辑：新快照 + 新规格版本，不覆盖旧版本。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .audit import audit_spec, save_audit
from .contracts import ContractError
from .snapshot import load as load_snapshot, save as save_snapshot
from .spec import load_spec, save_spec
from .spec_doc import export_spec
from .storage import now_iso, read_json, write_json

MAX_TEXT = 500


def edit_spec_item(directory: str | Path, *, item_id: str, text: str, actor: str,
                   expected_version: int | None = None,
                   expected_hash: str | None = None) -> dict[str, Any]:
    root = Path(directory)
    if not (root / "spec.json").is_file():
        raise ContractError("UNKNOWN_RUN", "找不到规格运行", "run_id")
    item_id, text, actor = item_id.strip(), text.strip(), actor.strip()
    if not item_id:
        raise ContractError("INVALID_BODY", "缺少 item_id", "item_id")
    if not text:
        raise ContractError("INVALID_TEXT", "需求文本不能为空", "text")
    if len(text) > MAX_TEXT:
        raise ContractError("INVALID_TEXT", f"需求文本不能超过 {MAX_TEXT} 字", "text")
    if not actor:
        raise ContractError("INVALID_BODY", "缺少操作人", "actor")

    spec = load_spec(root)
    snapshot = load_snapshot(root / f"snapshot.v{spec.snapshot_version}.json")
    if expected_version is not None and expected_version != spec.version:
        raise ContractError("STALE_VERSION", "规格版本已变化，请刷新", "expected_version")
    if expected_hash and expected_hash != spec.hash:
        raise ContractError("STALE_VERSION", "规格哈希已变化，请刷新", "expected_hash")

    target = next((row for row in snapshot.items if row["id"] == item_id), None)
    if target is None:
        raise ContractError("UNKNOWN_ITEM", f"找不到需求条目 {item_id}", "item_id")
    if target.get("type") != "requirement":
        raise ContractError("NOT_EDITABLE", "只允许编辑需求条目", "item_id")

    old_text = target["text"]
    snapshot.version += 1
    snapshot.parent_version = spec.snapshot_version
    snapshot.created_at = now_iso()
    snapshot.confirmed_by = actor
    snapshot.confirmed_at = now_iso()
    snapshot.items = deepcopy(snapshot.items)
    for row in snapshot.items:
        if row["id"] == item_id:
            row["text"] = text
    snapshot.history = [*snapshot.history, {
        "action": "edit_item", "item_id": item_id, "old_text": old_text,
        "text": text, "actor": actor, "at": now_iso(),
    }]
    save_snapshot(snapshot, root)

    edited = deepcopy(spec)
    edited.version += 1
    edited.created_at = now_iso()
    edited.snapshot_version = snapshot.version
    edited.snapshot_hash = snapshot.hash
    edited.status = "draft"
    edited.revisions = [*edited.revisions, {
        "version": edited.version, "action": "edit_item", "item_id": item_id,
        "old_text": old_text, "text": text, "actor": actor, "at": now_iso(),
    }]
    audit = audit_spec(edited, snapshot)
    save_audit(audit, root, edited.version)
    if audit["counts"]["blocking"]:
        edited.status = "blocked"
    save_spec(edited, root)
    export_spec(edited, snapshot, root)

    flow_path = root / "flow.json"
    if flow_path.exists():
        flow = read_json(flow_path)
        flow["status"] = edited.status
        flow["spec"] = {"id": edited.spec_id, "version": edited.version,
                        "hash": edited.hash}
        write_json(flow_path, flow)

    return {
        "run_id": root.name, "item": {"id": item_id, "text": text},
        "spec_id": edited.spec_id, "version": edited.version, "hash": edited.hash,
        "snapshot_version": snapshot.version, "snapshot_hash": snapshot.hash,
        "status": edited.status, "audit": {"counts": audit["counts"]},
    }
