"""规格修订：把自审查的问题转成可核对的改动，并在改完后重新审查。

修订不是静默改数字：每一步都记录改前、改后、原因和复核结果，
形成「发现问题 → 修订 → 复核」的完整记录，供人工确认或追责。
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .audit import audit_spec
from .constraints import deadline_days
from .contracts import ContractError
from .drafts import SATISFIES
from .snapshot import Snapshot
from .spec import VALUE_ORDER, Spec, acceptance_for, effort_for, feature_of
from .storage import now_iso, write_json

SIMPLIFY = {
    "发布": "只保留书名、价格与联系方式三个字段，去掉图片与草稿",
    "搜索": "只做书名精确匹配，去掉标签与排序",
    "支付": "只记录成交状态，不做在线支付流程",
    "收藏": "只在列表页记录收藏，不做收藏夹页面",
    "评价": "只记录一次评分，不做申诉与信用分",
    "自提": "只记录约见时间，不做地图与自提点",
}


def _fingerprint(issue: dict[str, Any]) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    return (issue["type"], tuple(sorted(issue["requirement_ids"])),
            tuple(sorted(issue["constraint_ids"])))


def _find_decision(spec: Spec, requirement_id: str) -> dict[str, Any]:
    for decision in spec.decisions:
        if requirement_id in decision["requirement_ids"]:
            return decision
    raise ContractError("UNKNOWN_REQUIREMENT", f"规格里没有需求 {requirement_id}",
                        "requirement_id")


def apply_revision(spec: Spec, snapshot: Snapshot, audit: dict[str, Any],
                   operations: list[dict[str, Any]], *, reason: str,
                   actor: str = "auto-revision",
                   issue_ids: list[str] | None = None) -> tuple[Spec, dict[str, Any]]:
    """应用一组修订并重审；返回新版本与修订记录。"""
    if not operations:
        raise ContractError("NO_OPERATION", "修订至少需要一步操作", "operations")
    if not reason.strip():
        raise ContractError("MISSING_REASON", "修订必须写明原因", "reason")

    target_ids = issue_ids if issue_ids is not None else [
        row["id"] for row in audit["issues"] if row["severity"] == "blocking"]
    known_issues = {row["id"]: row for row in audit["issues"]}
    unknown = [issue_id for issue_id in target_ids if issue_id not in known_issues]
    if unknown:
        raise ContractError("UNKNOWN_ISSUE", f"审查记录里没有问题 {unknown[0]}", "issue_ids")

    revised = deepcopy(spec)
    changes: list[dict[str, Any]] = []
    for index, op in enumerate(operations):
        path = f"operations[{index}]"
        kind = op.get("op")
        if kind == "update_decision":
            decision = _find_decision(revised, str(op.get("requirement_id", "")))
            before = {"choice": decision["choice"],
                      "cost_days": decision["cost_days"],
                      "satisfies": decision["satisfies"]}
            choice = str(op.get("choice", decision["choice"])).strip()
            if not choice:
                raise ContractError("EMPTY_TEXT", "方案描述不能为空", f"{path}.choice")
            cost = float(op.get("cost_days", decision["cost_days"]))
            if cost <= 0:
                raise ContractError("INVALID_FIELD", "成本必须大于 0", f"{path}.cost_days")
            satisfies = op.get("satisfies", decision["satisfies"])
            if satisfies not in SATISFIES:
                raise ContractError("INVALID_ENUM", "满足程度取值不合法",
                                    f"{path}.satisfies")
            decision.update({
                "choice": choice, "cost_days": cost, "satisfies": satisfies,
                "needs_human": satisfies != "full",
                "reason": str(op.get("reason", decision["reason"])),
            })
            requirement_id = decision["requirement_ids"][0]
            for module in revised.modules:
                if requirement_id in module["requirement_ids"]:
                    module["responsibility"] = choice
                    module["estimate_days"] = cost
            position = revised.decisions.index(decision) + 1
            generated = acceptance_for(decision, position)
            for row in revised.acceptance:
                if row["requirement_id"] == requirement_id:
                    row["criterion"] = op.get("criterion", generated["criterion"])
                    row["verify"] = op.get("verify", generated["verify"])
            changes.append({"op": kind, "requirement_id": requirement_id,
                            "before": before,
                            "after": {"choice": choice, "cost_days": cost,
                                      "satisfies": satisfies}})
        elif kind == "update_acceptance":
            requirement_id = str(op.get("requirement_id", ""))
            _find_decision(revised, requirement_id)
            row = next((item for item in revised.acceptance
                        if item["requirement_id"] == requirement_id), None)
            if row is None:
                raise ContractError("UNKNOWN_ACCEPTANCE",
                                    f"{requirement_id} 没有验收条件", f"{path}.requirement_id")
            before = {"criterion": row["criterion"], "verify": row["verify"]}
            row["criterion"] = str(op.get("criterion", row["criterion"])).strip()
            row["verify"] = str(op.get("verify", row["verify"])).strip()
            changes.append({"op": kind, "requirement_id": requirement_id,
                            "before": before,
                            "after": {"criterion": row["criterion"], "verify": row["verify"]}})
        else:
            raise ContractError("INVALID_OP", f"不支持的修订操作 {kind}", path)

    revised.effort = effort_for(revised.modules)
    before_version, before_hash = spec.version, spec.hash
    before_days = spec.effort["commit_days"]
    revised.version = spec.version + 1
    revised.status = "draft"
    revised.created_at = now_iso()

    if revised.content_fingerprint == spec.content_fingerprint:
        raise ContractError("NO_CHANGE", "修订前后规格内容一致", "operations")

    recheck = audit_spec(revised, snapshot)
    new_fingerprints = {_fingerprint(row) for row in recheck["issues"]}
    resolved = [row["id"] for row in audit["issues"]
                if row["id"] in target_ids and _fingerprint(row) not in new_fingerprints]

    record = {
        "id": f"REV-{len(spec.revisions) + 1:03d}",
        "issue_ids": list(target_ids),
        "issue_types": sorted({known_issues[issue_id]["type"] for issue_id in target_ids}),
        "reason": reason.strip(),
        "actor": actor,
        "at": now_iso(),
        "before": {"version": before_version, "hash": before_hash,
                   "commit_days": before_days},
        "after": {"version": revised.version, "hash": revised.hash,
                  "commit_days": revised.effort["commit_days"]},
        "changes": changes,
        "recheck": {
            "passed": recheck["passed"],
            "resolved": resolved,
            "remaining": [{"id": row["id"], "type": row["type"],
                           "label": row["label"], "severity": row["severity"]}
                          for row in recheck["issues"]],
            "verified_at": now_iso(),
        },
    }
    revised.revisions = [*spec.revisions, record]
    return revised, record


def scope_revision(spec: Spec, snapshot: Snapshot,
                   audit: dict[str, Any]) -> tuple[Spec, dict[str, Any], list[dict[str, Any]]]:
    """针对期限冲突做一次确定性的范围裁剪：从价值最低的模块开始简化。"""
    conflicts = [row for row in audit["issues"] if row["type"] == "CONSTRAINT_CONFLICT"]
    if not conflicts:
        raise ContractError("NO_AUTO_FIX", "当前没有可自动修订的期限冲突", "issues")

    deadline = next((value for value in
                     (deadline_days(item["text"]) for item in snapshot.constraints)
                     if value), None)
    if not deadline:
        raise ContractError("NO_AUTO_FIX", "找不到期限约束，无法计算裁剪量", "issues")

    target = spec.effort["commit_days"] - deadline + 0.3

    def value_rank(module: dict[str, Any]) -> int:
        feature = feature_of(module["name"]) or feature_of(module["responsibility"])
        return VALUE_ORDER.index(feature) if feature in VALUE_ORDER else 9

    # 价值越低越先裁剪：收藏、评价、自提在前，搜索最后
    ordered = sorted(spec.modules, key=lambda module: (-value_rank(module),
                                                       -module["estimate_days"]))

    operations: list[dict[str, Any]] = []
    names: list[str] = []
    for module in ordered:
        if target <= 0:
            break
        current = float(module["estimate_days"])
        if current <= 0.6:
            continue
        refs = [str(item) for item in (module.get("requirement_ids") or [])
                if str(item).startswith("R-")]
        if not refs:
            # 功能退化为 F 编号时没有可裁剪的真实需求，不静默改估算。
            continue
        new_cost = max(0.5, round(current * 0.5, 1))
        saving = current - new_cost
        requirement_id = refs[0]
        decision = _find_decision(spec, requirement_id)
        feature = feature_of(module["name"]) or feature_of(decision["scope"])
        choice = SIMPLIFY.get(feature, f"{decision['choice']}（首版只做最小实现）")
        operations.append({
            "op": "update_decision", "requirement_id": requirement_id,
            "choice": choice, "cost_days": new_cost,
            "reason": f"期限 {deadline} 天：{module['name']} 首版只保留最小实现",
        })
        names.append(f"{module['name']} {current}→{new_cost} 天")
        target -= saving

    if not operations:
        raise ContractError("NO_AUTO_FIX", "所有模块都已压到最小，仍无法满足期限", "issues")

    revised, record = apply_revision(
        spec, snapshot, audit, operations,
        reason=f"期限 {deadline} 天：裁剪 {'、'.join(names)}",
        issue_ids=[row["id"] for row in conflicts])
    return revised, record, operations


def save_revisions(spec: Spec, directory: str | Path) -> Path:
    root = Path(directory)
    write_json(root / f"revision.v{spec.version}.json", spec.revisions[-1])
    return write_json(root / "revisions.json", spec.revisions)
