"""升级：把无法自动解决的阻断问题变成一个有选项、有代价、有影响的决策请求。

只产出一个问题：优先处理“需求被降级”这类价值冲突；其余阻断问题列在影响范围里，
避免把一张问题清单丢给人。
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .constraints import deadline_days, forbidden_subjects, shares_topic
from .contracts import ContractError
from .snapshot import Snapshot
from .spec import Spec
from .storage import now_iso, write_json

ESCALATION_TYPES = ("REQUIREMENT_OVERRIDDEN", "CONSTRAINT_CONFLICT", "CONSTRAINT_VIOLATION")


def _blocking(audit: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in audit["issues"] if row["severity"] == "blocking"]


def _related_constraint(snapshot: Snapshot, text: str) -> dict[str, Any] | None:
    for constraint in snapshot.constraints:
        for subject in forbidden_subjects(constraint["text"]):
            if shares_topic(subject, text):
                return constraint
    return None


def _deadline(snapshot: Snapshot) -> int | None:
    values = [deadline_days(item["text"]) for item in snapshot.constraints]
    values = [value for value in values if value]
    return min(values) if values else None


def _open_item(spec: Spec, requirement_id: str) -> dict[str, Any] | None:
    return next((row for row in spec.open_items
                 if row["blocking"] and requirement_id in row["impact"]), None)


def _override_question(spec: Spec, snapshot: Snapshot, issue: dict[str, Any],
                       blocking: list[dict[str, Any]]) -> dict[str, Any]:
    requirement_id = issue["requirement_ids"][0]
    requirement = snapshot.by_id(requirement_id)
    decision = next(row for row in spec.decisions
                    if requirement_id in row["requirement_ids"])
    constraint = _related_constraint(snapshot, requirement["text"])
    open_item = _open_item(spec, requirement_id)
    current = float(decision["cost_days"])
    alternative = max((float(row["cost_days"]) for row in (open_item or {}).get("candidates", [])),
                      default=current + 6.0)
    alternative = max(alternative, current + 1.0)
    middle = round((current + alternative) / 2, 1)
    deadline = _deadline(snapshot)

    status_text = ("只满足了一部分" if decision["satisfies"] == "partial"
                   else "实际上没有满足")
    background = [
        f"{requirement_id} 明确要求「{requirement['text']}」，"
        f"当前决策是「{decision['choice']}」，{status_text}。",
    ]
    if constraint:
        background.append(
            f"约束 {constraint['id']}「{constraint['text']}」与它互斥；"
            f"已尝试自动裁剪范围，仍无法同时满足这两条。")
    background.append(
        f"规格 v{spec.version} 当前承诺 {spec.effort['commit_days']} 天"
        + (f"，期限为 {deadline} 天。" if deadline else "。"))
    others = [row for row in blocking if row["id"] != issue["id"]]
    if others:
        background.append("另有 %d 条阻断待处理：%s" % (
            len(others), "；".join(f"{row['id']} {row['message']}" for row in others[:3])))

    options = [
        {"key": "A", "text": f"保留「{requirement['text']}」，"
                             + (f"放宽约束 {constraint['id']}" if constraint else "增加投入"),
         "cost_days": alternative,
         "consequence": (f"工期增加约 {alternative - current:.1f} 天"
                         + (f"，需要重新评估 {deadline} 天期限" if deadline else "")
                         + (f"；需为 {constraint['id']} 申请例外" if constraint else "")),
         "resolves": [issue["id"]]},
        {"key": "B", "text": f"把「{requirement['text']}」列为下一版，首版按现有方案交付",
         "cost_days": current,
         "consequence": "首版无法演示该能力，需要产品负责人书面确认范围",
         "resolves": [issue["id"]]},
        {"key": "C", "text": "折中：保留入口但只记录意向，不接外部通道",
         "cost_days": middle,
         "consequence": "流程看起来完整，但不满足真实场景，后续仍需补齐",
         "resolves": []},
    ]

    return {
        "id": "HUMAN-Q-01",
        "type": "VALUE_CONFLICT",
        "question": f"首版是否必须支持「{requirement['text']}」（{requirement_id}）？",
        "background": background,
        "options": options,
        "affected": {"requirements": [requirement_id],
                     "constraints": [constraint["id"]] if constraint else [],
                     "issues": [row["id"] for row in blocking]},
        "if_undecided": "规格保持未通过，不能作为开发依据；导出的 Markdown 与 JSON 会标注未决。",
        "recommended": "B",
        "status": "open",
        "created_at": now_iso(),
    }


def _deadline_question(spec: Spec, snapshot: Snapshot, issue: dict[str, Any],
                       blocking: list[dict[str, Any]]) -> dict[str, Any]:
    deadline = _deadline(snapshot) or 0
    commit = float(spec.effort["commit_days"])
    return {
        "id": "HUMAN-Q-01",
        "type": "SCHEDULE_CONFLICT",
        "question": f"承诺 {commit} 天超出 {deadline} 天期限，是延长工期还是继续裁剪范围？",
        "background": [
            f"当前规格 v{spec.version} 的 PERT 承诺为 {commit} 天，"
            f"期限约束为 {deadline} 天。",
            "自动裁剪只会简化模块，不会改变验收标准；再压下去会牺牲可演示性。",
            *(["另有 %d 条阻断待处理：%s" % (
                len(others), "；".join(f"{row['id']} {row['message']}"
                                      for row in others[:3]))
               ] if (others := [row for row in blocking if row["id"] != issue["id"]]) else []),
        ],
        "options": [
            {"key": "A", "text": f"把期限放宽到 {commit} 天", "cost_days": commit,
             "consequence": "需要重新约定交付时间，可能影响演示安排",
             "resolves": [issue["id"]]},
            {"key": "B", "text": f"继续裁剪范围到 {deadline} 天以内", "cost_days": float(deadline),
             "consequence": "需要放弃部分验收项，规格要重新复核",
             "resolves": [issue["id"]]},
            {"key": "C", "text": "保持现状，接受超期风险", "cost_days": commit,
             "consequence": "交付日期不可信，风险由项目负责人承担", "resolves": []},
        ],
        "affected": {"requirements": [], "constraints": issue["constraint_ids"],
                     "issues": [row["id"] for row in blocking]},
        "if_undecided": "规格保持未通过，不能作为排期依据。",
        "recommended": "A",
        "status": "open",
        "created_at": now_iso(),
    }


def build_question(spec: Spec, snapshot: Snapshot,
                   audit: dict[str, Any]) -> dict[str, Any] | None:
    blocking = _blocking(audit)
    if not blocking:
        return None
    priority = ("REQUIREMENT_OVERRIDDEN", "CONSTRAINT_CONFLICT",
                "CONSTRAINT_VIOLATION")
    issue = next((row for kind in priority for row in blocking
                  if row["type"] == kind), blocking[0])
    if issue["type"] not in ESCALATION_TYPES:
        return None
    if issue["type"] == "REQUIREMENT_OVERRIDDEN" and issue["requirement_ids"]:
        return _override_question(spec, snapshot, issue, blocking)
    if issue["type"] == "CONSTRAINT_CONFLICT":
        return _deadline_question(spec, snapshot, issue, blocking)
    if issue["type"] == "CONSTRAINT_VIOLATION":
        return _override_question(spec, snapshot, issue, blocking)
    return None


def escalate(spec: Spec, snapshot: Snapshot,
             audit: dict[str, Any]) -> tuple[Spec, dict[str, Any]]:
    if spec.escalation:
        return spec, spec.escalation
    question = build_question(spec, snapshot, audit)
    if question is None:
        raise ContractError("NO_ESCALATION", "当前没有需要人工决策的阻断问题", "issues")
    escalated = deepcopy(spec)
    escalated.status = "awaiting_human"
    escalated.escalation = question
    return escalated, question


def save_question(question: dict[str, Any], directory: str | Path) -> Path:
    return write_json(Path(directory) / "human_question.json", question)
