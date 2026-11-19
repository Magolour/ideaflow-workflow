"""交付物：快照、A/B 草案、来源引用与清单。

交付目录结构：

    <run>/
      snapshot.v1.json        需求快照（含来源行号）
      drafts/DRAFT-A.json     A 组草案
      drafts/DRAFT-B.json     B 组草案
      citations.json          草案 → 快照条目的引用边
      manifest.json           文件、哈希与计数

引用边是双向追溯的关键：从任一草案决定可以找到它依据的需求，
再顺着 `sources` 找到原文的章节与行号。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import ContractError
from .drafts import Draft
from .snapshot import Snapshot, save
from .storage import content_hash, now_iso, write_json, write_text


def build_citations(snapshot: Snapshot, drafts: dict[str, Draft]) -> dict[str, Any]:
    """把草案里的引用整理成边表，并校验没有悬空引用或覆盖缺口。"""
    edges: list[dict[str, Any]] = []
    for group in sorted(drafts):
        draft = drafts[group]
        if draft.snapshot_hash != snapshot.hash:
            raise ContractError("SNAPSHOT_MISMATCH",
                                f"{draft.draft_id} 引用的快照版本与当前快照不一致",
                                "snapshot_ref")

        covered: set[str] = set()
        for decision in draft.decisions:
            for item_id in decision["requirement_ids"]:
                edges.append({"draft": draft.draft_id, "from": "decision",
                              "id": decision["id"], "item": item_id})
                covered.add(item_id)
        for claim in draft.claims:
            for item_id in claim.get("requirement_ids", []):
                edges.append({"draft": draft.draft_id, "from": "claim",
                              "id": claim["id"], "item": item_id})
            for item_id in claim.get("constraint_ids", []):
                edges.append({"draft": draft.draft_id, "from": "claim",
                              "id": claim["id"], "item": item_id})

        missing = snapshot.ids("requirement") - covered
        if missing:
            raise ContractError("CITATION_GAP",
                                f"{draft.draft_id} 没有覆盖需求：{', '.join(sorted(missing))}",
                                "drafts")

    known = snapshot.ids()
    dangling = [edge for edge in edges if edge["item"] not in known]
    if dangling:
        raise ContractError("DANGLING_CITATION",
                            f"存在指向快照外的引用：{dangling[0]['item']}", "citations")

    edges.sort(key=lambda edge: (edge["draft"], edge["from"], edge["id"], edge["item"]))
    sources = {
        item["id"]: {
            "type": item["type"],
            "text": item["text"],
            "section": item["source"]["section"],
            "start_line": item["source"]["start_line"],
            "end_line": item["source"]["end_line"],
            "excerpt": item["source"]["excerpt"],
        }
        for item in snapshot.items
    }
    return {
        "snapshot_ref": {"snapshot_id": snapshot.snapshot_id,
                         "version": snapshot.version, "hash": snapshot.hash},
        "sources": sources,
        "edges": edges,
        "counts": {
            "edges": len(edges),
            "requirements": len(snapshot.requirements),
            "drafts": len(drafts),
        },
    }


def deliver(directory: str | Path, snapshot: Snapshot, drafts: dict[str, Draft],
            *, extras: dict[str, Any] | None = None,
            texts: dict[str, str] | None = None) -> dict[str, Any]:
    """写出四件套并返回清单；重复调用结果稳定（哈希不变）。"""
    root = Path(directory)
    save(snapshot, root)

    files: list[str] = [f"snapshot.v{snapshot.version}.json"]
    draft_rows = []
    for group in sorted(drafts):
        draft = drafts[group]
        relative = f"drafts/{draft.draft_id}.json"
        write_json(root / relative, draft.to_json())
        files.append(relative)
        draft_rows.append({
            "draft_id": draft.draft_id,
            "group": draft.group,
            "group_name": draft.group_name,
            "file": relative,
            "hash": draft.hash,
            "commit_days": draft.commit_days,
            "declined": draft.declined,
        })

    citations = build_citations(snapshot, drafts)
    write_json(root / "citations.json", citations)
    files.append("citations.json")

    extras = extras or {}
    extra_rows = []
    for name in sorted(extras):
        write_json(root / name, extras[name])
        files.append(name)
        extra_rows.append({"file": name, "hash": content_hash(extras[name])})

    for name in sorted(texts or {}):
        write_text(root / name, texts[name])
        files.append(name)
        extra_rows.append({"file": name, "hash": content_hash(texts[name])})

    manifest = {
        "run_id": root.name,
        "created_at": now_iso(),
        "snapshot": {"snapshot_id": snapshot.snapshot_id, "version": snapshot.version,
                     "hash": snapshot.hash, "file": f"snapshot.v{snapshot.version}.json"},
        "drafts": draft_rows,
        "citations": {"file": "citations.json", "hash": content_hash(citations),
                      "edges": citations["counts"]["edges"]},
        "extras": extra_rows,
        "files": files,
    }
    write_json(root / "manifest.json", manifest)
    return manifest
