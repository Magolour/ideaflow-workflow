"""逐份评审：一次只给一份草案，评完 A 再评 B，禁止横向参照。

三维评分只用来暴露取舍，不用来替人选择：
- 价值：有多少需求被完整满足；
- 可行性：是否守住期限与禁止项；
- 成本：投入相对期限的比例。

评审的分歧进入 `disagreements`，无法在评审阶段解决的问题进入 `open_items`，
交给整合与人工确认处理。
"""

from __future__ import annotations

from typing import Any

from .constraints import deadline_days
from .drafts import SATISFIES_LABEL, Draft
from .snapshot import Snapshot

CRITERIA = (
    {"key": "value", "label": "价值", "reviewer": "产品评审", "weight": 0.40},
    {"key": "feasibility", "label": "可行性", "reviewer": "技术评审", "weight": 0.35},
    {"key": "cost", "label": "成本", "reviewer": "交付评审", "weight": 0.25},
)
SEVERITY_LABEL = {"blocking": "阻断", "major": "重要", "minor": "一般"}


def _deadline(snapshot: Snapshot) -> int | None:
    values = [deadline_days(item["text"]) for item in snapshot.constraints]
    values = [value for value in values if value]
    return min(values) if values else None


def review_context(snapshot: Snapshot, draft: Draft) -> dict[str, Any]:
    """评审上下文：只有快照、这一份草案和评分口径，没有另一份草案。"""
    return {
        "snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "version": snapshot.version,
            "hash": snapshot.hash,
            "items": [{key: item[key] for key in ("id", "type", "text", "priority")}
                      for item in snapshot.items],
        },
        "draft": draft.to_json(),
        "criteria": [dict(row) for row in CRITERIA],
        "rules": ["一次只评一份草案", "每条分歧必须指向草案中的决定或主张"],
    }


def _score(draft: Draft, snapshot: Snapshot) -> dict[str, Any]:
    requirements = snapshot.requirements
    full = [row for row in draft.decisions if row["satisfies"] == "full"]
    value = round(1 + 4 * len(full) / max(1, len(requirements)), 1)

    deadline = _deadline(snapshot)
    conflicts = [row for row in draft.constraints_checked if not row["complies"]]
    if deadline and draft.commit_days > deadline:
        feasibility = 1.0 if draft.commit_days > deadline * 1.3 else 2.0
    elif conflicts:
        feasibility = 3.0
    else:
        feasibility = 5.0

    if deadline:
        ratio = draft.commit_days / deadline
        if ratio <= 0.7:
            cost = 5.0
        elif ratio <= 0.85:
            cost = 4.0
        elif ratio <= 1.0:
            cost = 3.0
        elif ratio <= 1.3:
            cost = 2.0
        else:
            cost = 1.0
    else:
        cost = 3.0

    return {"value": value, "feasibility": feasibility, "cost": cost,
            "deadline_days": deadline, "conflicts": len(conflicts),
            "full": len(full), "total": len(requirements)}


def _criteria_rows(draft: Draft, scores: dict[str, Any]) -> list[dict[str, Any]]:
    third = draft.claims[2]["id"] if len(draft.claims) > 2 else None
    declined = [row for row in draft.decisions if row["satisfies"] != "full"]
    rows = []
    for criterion in CRITERIA:
        key = criterion["key"]
        if key == "value":
            if declined:
                reason = (f"{scores['total']} 条需求中 {scores['full']} 条完全满足，"
                          f"{declined[0]['scope']} 被降级为"
                          f"{SATISFIES_LABEL[declined[0]['satisfies']]}")
            else:
                reason = f"{scores['total']} 条需求全部完全满足"
        elif key == "feasibility":
            if scores["deadline_days"] and draft.commit_days > scores["deadline_days"]:
                reason = (f"承诺 {draft.commit_days} 天，超出 "
                          f"{scores['deadline_days']} 天期限；另有 "
                          f"{scores['conflicts']} 处约束冲突")
            else:
                reason = (f"承诺 {draft.commit_days} 天，在 {scores['deadline_days']} 天期限内，"
                          f"无约束冲突" if scores["deadline_days"]
                          else f"承诺 {draft.commit_days} 天，约束信息不足")
        else:
            if scores["deadline_days"]:
                ratio = draft.commit_days / scores["deadline_days"]
                reason = f"投入占期限 {ratio:.0%}，{'留有余量' if ratio <= 1 else '已经超期'}"
            else:
                reason = "缺少期限约束，按经验值评估"
        row = {"key": key, "label": criterion["label"], "reviewer": criterion["reviewer"],
               "weight": criterion["weight"], "score": scores[key], "reason": reason}
        if third and key != "value":
            row["claim_ids"] = [third]
        if key == "value":
            row["claim_ids"] = [draft.claims[1]["id"]] if len(draft.claims) > 1 else []
        rows.append(row)
    return rows


def _disagreements(draft: Draft) -> list[dict[str, Any]]:
    rows = []
    for decision in draft.decisions:
        if decision["satisfies"] == "full":
            continue
        rows.append({
            "id": f"DIS-{draft.group}-{len(rows) + 1:02d}",
            "draft": draft.draft_id,
            "requirement_ids": decision["requirement_ids"],
            "claim_id": next((claim["id"] for claim in draft.claims
                              if set(claim.get("requirement_ids", [])) &
                              set(decision["requirement_ids"])), ""),
            "reviewer": "产品评审",
            "severity": "blocking" if decision["satisfies"] == "declined" else "major",
            "issue": (f"需求「{decision['scope']}」未被完全满足：{decision['choice']}"),
        })
    for check in draft.constraints_checked:
        if check["complies"]:
            continue
        rows.append({
            "id": f"DIS-{draft.group}-{len(rows) + 1:02d}",
            "draft": draft.draft_id,
            "requirement_ids": [],
            "constraint_ids": [check["constraint_id"]],
            "claim_id": next((claim["id"] for claim in draft.claims
                              if check["constraint_id"] in claim.get("constraint_ids", [])), ""),
            "reviewer": "技术评审",
            "severity": "blocking",
            "issue": check["note"],
        })
    return rows


def review_draft(snapshot: Snapshot, draft: Draft) -> dict[str, Any]:
    """评审一份草案；函数只接触这一份草案。"""
    context = review_context(snapshot, draft)
    scores = _score(draft, snapshot)
    criteria = _criteria_rows(draft, scores)
    weighted = round(sum(row["score"] * row["weight"] for row in criteria), 2)
    disagreements = _disagreements(draft)

    open_items = []
    for row in disagreements:
        if row["severity"] != "blocking":
            continue
        if row["requirement_ids"]:
            item = next(item for item in snapshot.items
                        if item["id"] == row["requirement_ids"][0])
            question = f"首版是否必须支持「{item['text']}」？如果不支持，需要谁确认范围？"
        else:
            constraint_id = row["constraint_ids"][0]
            item = snapshot.by_id(constraint_id)
            question = f"是否放宽约束「{item['text']}」以保留当前方案？"
        open_items.append({
            "id": f"OPEN-{draft.group}-{len(open_items) + 1:02d}",
            "draft": draft.draft_id,
            "question": question,
            "impact": row["requirement_ids"] or row.get("constraint_ids", []),
            "blocking": True,
            "candidates": [{"draft": draft.draft_id, "choice": row["issue"],
                            "cost_days": draft.commit_days}],
            "resolution": None,
        })

    return {
        "draft_id": draft.draft_id,
        "group": draft.group,
        "context_keys": sorted(context),
        "scores": scores,
        "criteria": criteria,
        "weighted": weighted,
        "disagreements": disagreements,
        "open_items": open_items,
        "summary": (f"{draft.group_name}：价值 {scores['value']} ｜ "
                    f"可行 {scores['feasibility']} ｜ 成本 {scores['cost']} ｜ "
                    f"加权 {weighted}"),
    }


def review_all(snapshot: Snapshot, drafts: dict[str, Draft]) -> dict[str, Any]:
    """分别评审每一份草案，再给出排序；评审阶段不横向比较。"""
    reviews = {draft.draft_id: review_draft(snapshot, drafts[group])
               for group, draft in sorted(drafts.items())}
    ranking = sorted(reviews.values(),
                     key=lambda row: (-row["weighted"], row["scores"]["conflicts"]))
    base = ranking[0]
    other = ranking[1] if len(ranking) > 1 else None
    reason = f"{base['draft_id']} 加权 {base['weighted']}"
    if other:
        reason += f"，高于 {other['draft_id']} 的 {other['weighted']}"

    open_items = []
    seen: set[str] = set()
    for review in reviews.values():
        for item in review["open_items"]:
            key = item["question"]
            if key in seen:
                continue
            seen.add(key)
            open_items.append({**item, "id": f"OPEN-{len(open_items) + 1:02d}",
                               "origin": "review"})

    return {
        "snapshot_ref": {"snapshot_id": snapshot.snapshot_id,
                         "version": snapshot.version, "hash": snapshot.hash},
        "criteria": [dict(row) for row in CRITERIA],
        "drafts": reviews,
        "ranking": [{"draft_id": row["draft_id"], "weighted": row["weighted"]}
                    for row in ranking],
        "verdict": {"base_draft": base["draft_id"], "weighted": base["weighted"],
                    "reason": reason},
        "open_items": open_items,
    }
