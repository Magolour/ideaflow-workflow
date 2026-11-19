"""取舍分析：把 A/B 在同一需求上的不同选择算成可核对的记录。

只记录事实与差额，不替人做决定：哪一条值得多花时间，留给评审与人工确认。
"""

from __future__ import annotations

from typing import Any

from .constraints import deadline_days
from .drafts import Draft
from .snapshot import Snapshot

TENSION_LABEL = {
    "requirement_vs_schedule": "放弃需求换工期",
    "experience_vs_schedule": "多投入换体验",
    "minor": "差异较小",
}


def _deadline(snapshot: Snapshot) -> int | None:
    days = [deadline_days(item["text"]) for item in snapshot.constraints]
    values = [value for value in days if value]
    return min(values) if values else None


def build_tradeoffs(snapshot: Snapshot, drafts: dict[str, Draft]) -> dict[str, Any]:
    a, b = drafts["A"], drafts["B"]
    rows: list[dict[str, Any]] = []

    for index, item in enumerate(snapshot.requirements, 1):
        left = a.decision_for(item["id"])
        right = b.decision_for(item["id"])
        if left is None or right is None:
            continue
        if left["choice"] == right["choice"]:
            continue

        delta = round(float(right["cost_days"]) - float(left["cost_days"]), 1)
        if left["satisfies"] != right["satisfies"]:
            tension = "requirement_vs_schedule"
            summary = (f"A 放弃「{item['text']}」换取 {abs(delta)} 天工期，"
                       f"B 保留该需求")
        elif delta > 0:
            tension = "experience_vs_schedule"
            summary = f"B 多投入 {delta} 天换取更好的体验"
        else:
            tension = "minor"
            summary = "两种实现成本接近，差别在体验取舍"

        rows.append({
            "id": f"TRD-{index:02d}",
            "requirement_id": item["id"],
            "requirement": item["text"],
            "a": {"draft": a.draft_id, "decision": left["id"], "choice": left["choice"],
                  "cost_days": float(left["cost_days"]), "satisfies": left["satisfies"],
                  "reason": left["reason"]},
            "b": {"draft": b.draft_id, "decision": right["id"], "choice": right["choice"],
                  "cost_days": float(right["cost_days"]), "satisfies": right["satisfies"],
                  "reason": right["reason"]},
            "delta_days": delta,
            "tension": tension,
            "tension_label": TENSION_LABEL[tension],
            "summary": summary,
            "source": item["source"],
        })

    deadline = _deadline(snapshot)
    headline = next((row["id"] for row in rows
                     if row["tension"] == "requirement_vs_schedule"), None)
    if headline is None and rows:
        headline = max(rows, key=lambda row: abs(row["delta_days"]))["id"]

    return {
        "snapshot_ref": {"snapshot_id": snapshot.snapshot_id, "version": snapshot.version,
                         "hash": snapshot.hash},
        "project": {
            "a_days": a.commit_days,
            "b_days": b.commit_days,
            "deadline_days": deadline,
            "a_within_deadline": None if deadline is None else a.commit_days <= deadline,
            "b_within_deadline": None if deadline is None else b.commit_days <= deadline,
        },
        "items": rows,
        "counts": {
            "total": len(rows),
            "requirement_vs_schedule": sum(1 for row in rows
                                           if row["tension"] == "requirement_vs_schedule"),
            "experience_vs_schedule": sum(1 for row in rows
                                          if row["tension"] == "experience_vs_schedule"),
        },
        "headline": headline,
    }
