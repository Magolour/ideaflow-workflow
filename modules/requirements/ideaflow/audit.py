"""规格自审查：需求覆盖、约束冲突、验收条件三类确定性检查。

审查不看模型措辞，只看规格里的结构化字段，因此结论可复现：
- 覆盖：每条需求是否有决策、模块、验收项；
- 冲突：期限是否小于估算、人力是否撑不住并行度、是否包含被禁止的内容；
- 验收：每条验收条件是否可度量（阈值、数量、比例或明确判定方式）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .constraints import deadline_days, forbidden_subjects, headcount, shares_topic
from .drafts import NEGATIONS
from .snapshot import Snapshot
from .spec import Spec
from .storage import content_hash, write_json

TYPE_SEVERITY = {
    "COVERAGE_MISSING_DECISION": "blocking",
    "COVERAGE_MISSING_MODULE": "blocking",
    "COVERAGE_MISSING_ACCEPTANCE": "blocking",
    "REQUIREMENT_OVERRIDDEN": "blocking",
    "CONSTRAINT_CONFLICT": "blocking",
    "CONSTRAINT_VIOLATION": "blocking",
    "CONSTRAINT_RISK": "major",
    "CONSTRAINT_UNVERIFIED": "major",
    "ACCEPTANCE_NOT_MEASURABLE": "major",
    "MAPPING_INCONSISTENT": "major",
    "NON_GOAL_OVERLAP": "major",
}
TYPE_LABEL = {
    "COVERAGE_MISSING_DECISION": "需求没有对应决策",
    "COVERAGE_MISSING_MODULE": "需求没有对应模块",
    "COVERAGE_MISSING_ACCEPTANCE": "需求没有验收条件",
    "REQUIREMENT_OVERRIDDEN": "需求被降级或未满足",
    "CONSTRAINT_CONFLICT": "估算与期限冲突",
    "CONSTRAINT_VIOLATION": "方案违反禁止项",
    "CONSTRAINT_RISK": "人力与并行度不匹配",
    "CONSTRAINT_UNVERIFIED": "约束没有核对方式",
    "ACCEPTANCE_NOT_MEASURABLE": "验收条件不可度量",
    "MAPPING_INCONSISTENT": "需求与模块映射不一致",
    "NON_GOAL_OVERLAP": "非目标与方案范围重叠",
}

MEASURABLE = re.compile(
    r"\d+\s*(?:%|台|步|小时|分钟|天|人|次|条|个|ms|秒)|[≤≥<>]|通过率|成功率|阈值|100%")


def _issue(rows: list[dict[str, Any]], issue_type: str, message: str, *,
           requirement_ids: list[str] | None = None,
           constraint_ids: list[str] | None = None,
           fix_hint: str = "") -> None:
    rows.append({
        "id": f"AUD-{len(rows) + 1:02d}",
        "type": issue_type,
        "label": TYPE_LABEL[issue_type],
        "severity": TYPE_SEVERITY[issue_type],
        "message": message,
        "requirement_ids": requirement_ids or [],
        "constraint_ids": constraint_ids or [],
        "fix_hint": fix_hint,
        "status": "open",
    })


def audit_spec(spec: Spec, snapshot: Snapshot) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    decisions = {row["requirement_ids"][0]: row for row in spec.decisions
                 if row["requirement_ids"]}
    modules = {module_id for row in spec.modules for module_id in [row["id"]]}
    mapped = {row["requirement_id"] for row in spec.mapping}
    accepted = {row["requirement_id"] for row in spec.acceptance}

    # 功能清单模式：模块只来自功能，原文需求通过 source_refs 追溯。
    # 未被任何功能引用的需求不再要求单独有决策/模块/验收，覆盖率也只按
    # 被引用的需求计算，避免把“功能清单没收录”误判成“需求未覆盖”。
    feature_mode = (any(row.get("origin") == "features" for row in spec.decisions)
                    or any(not row["requirement_ids"] for row in spec.modules)
                    or any(row.get("requirement_id") == "" for row in spec.mapping))
    mapped_requirements = snapshot.ids("requirement") & mapped
    coverage_total = (len(mapped_requirements) if feature_mode
                      else len(snapshot.requirements))
    covered_decisions = 0
    covered_modules = 0
    for requirement in snapshot.requirements:
        rid = requirement["id"]
        if feature_mode and rid not in mapped_requirements:
            continue
        decision = decisions.get(rid)
        if decision is None:
            _issue(issues, "COVERAGE_MISSING_DECISION",
                   f"{rid}「{requirement['text']}」没有对应决策",
                   requirement_ids=[rid], fix_hint="为该需求补一条决策")
        else:
            covered_decisions += 1
            if decision["satisfies"] != "full":
                _issue(issues, "REQUIREMENT_OVERRIDDEN",
                       f"{rid}「{requirement['text']}」未被完全满足：{decision['choice']}",
                       requirement_ids=[rid],
                       fix_hint="确认是否放宽约束，或把该需求正式列为非目标")
            module_ids = [row["module_ids"] for row in spec.mapping
                          if row["requirement_id"] == rid]
            if not module_ids or not module_ids[0]:
                _issue(issues, "COVERAGE_MISSING_MODULE",
                       f"{rid}「{requirement['text']}」没有对应模块",
                       requirement_ids=[rid], fix_hint="补充模块并在 mapping 中登记")
            else:
                covered_modules += 1
        if rid not in accepted:
            _issue(issues, "COVERAGE_MISSING_ACCEPTANCE",
                   f"{rid}「{requirement['text']}」没有验收条件",
                   requirement_ids=[rid], fix_hint="补充可度量的验收条件")

    if mapped != snapshot.ids("requirement") and not feature_mode:
        missing = snapshot.ids("requirement") - mapped
        _issue(issues, "MAPPING_INCONSISTENT",
               f"映射表缺少需求：{', '.join(sorted(missing)) or '—'}",
               fix_hint="补齐 R→M 映射")

    mapping_modules = {module_id for row in spec.mapping for module_id in row["module_ids"]}
    unknown = mapping_modules - modules
    if unknown:
        _issue(issues, "MAPPING_INCONSISTENT",
               f"映射表引用了不存在的模块：{', '.join(sorted(unknown))}",
               fix_hint="修正 mapping 中的模块编号")
    orphans = modules - mapping_modules
    if orphans:
        _issue(issues, "MAPPING_INCONSISTENT",
               f"模块没有登记到任何需求：{', '.join(sorted(orphans))}",
               fix_hint="为模块补充 requirement_ids 并登记映射")

    verified = 0
    active_rows = [*spec.decisions, *spec.modules]
    active_text = " ".join(str(row.get("choice") or row.get("responsibility", ""))
                           for row in active_rows)
    for constraint in snapshot.constraints:
        text = constraint["text"]
        cid = constraint["id"]
        days = deadline_days(text)
        people = headcount(text)
        subjects = forbidden_subjects(text)

        if days:
            verified += 1
            if spec.effort["commit_days"] > days:
                _issue(issues, "CONSTRAINT_CONFLICT",
                       f"承诺 {spec.effort['commit_days']} 天，超出期限 {days} 天",
                       constraint_ids=[cid],
                       fix_hint="裁剪范围、延长工期或增加人力")
            continue
        if people:
            verified += 1
            if len(spec.modules) > people * 2:
                _issue(issues, "CONSTRAINT_RISK",
                       f"{len(spec.modules)} 个模块与 {people} 名开发不匹配，并行度过高",
                       constraint_ids=[cid], fix_hint="合并模块或减少并行工作流")
            continue
        if subjects:
            verified += 1
            hits = [row for row in spec.decisions
                    if row["satisfies"] != "declined"
                    and not any(word in row["choice"] for word in NEGATIONS)
                    and any(shares_topic(subject, row["choice"]) for subject in subjects)]
            if hits:
                _issue(issues, "CONSTRAINT_VIOLATION",
                       f"{hits[0]['id']} 包含被约束排除的内容：{subjects[0]}",
                       requirement_ids=hits[0]["requirement_ids"],
                       constraint_ids=[cid],
                       fix_hint="删除相关模块，或由人确认放宽该约束")
            continue
        _issue(issues, "CONSTRAINT_UNVERIFIED", f"{cid}「{text}」没有核对方式",
               constraint_ids=[cid], fix_hint="写明如何验证该约束")

    measurable = 0
    for row in spec.acceptance:
        if MEASURABLE.search(row["criterion"]):
            measurable += 1
        else:
            _issue(issues, "ACCEPTANCE_NOT_MEASURABLE",
                   f"{row['id']} 的验收条件缺少可度量口径：{row['criterion']}",
                   requirement_ids=[row["requirement_id"]],
                   fix_hint="补充阈值、数量或判定方式")

    for non_goal in spec.non_goals:
        choice = str(non_goal.get("choice", ""))
        if choice and choice in active_text:
            _issue(issues, "NON_GOAL_OVERLAP",
                   f"非目标「{non_goal['item']}」仍出现在方案中",
                   requirement_ids=non_goal.get("requirement_ids", []),
                   fix_hint="从模块职责或决策中移除该内容")

    counts = {
        "blocking": sum(1 for row in issues if row["severity"] == "blocking"),
        "major": sum(1 for row in issues if row["severity"] == "major"),
        "minor": sum(1 for row in issues if row["severity"] == "minor"),
    }
    counts["total"] = len(issues)
    return {
        "spec_ref": {"spec_id": spec.spec_id, "version": spec.version,
                     "hash": spec.hash},
        "snapshot_ref": {"snapshot_id": snapshot.snapshot_id,
                         "version": snapshot.version, "hash": snapshot.hash},
        "issues": issues,
        "counts": counts,
        "passed": counts["blocking"] == 0,
        "checks": {
            "coverage": {"requirements": coverage_total,
                         "with_decision": covered_decisions,
                         "with_module": covered_modules,
                         "with_acceptance": len(accepted & snapshot.ids("requirement"))},
            "constraints": {"total": len(snapshot.constraints), "verified": verified},
            "acceptance": {"total": len(spec.acceptance), "measurable": measurable},
        },
    }


def audit_hash(audit: dict[str, Any]) -> str:
    return content_hash({"spec_ref": audit["spec_ref"], "issues": audit["issues"],
                         "checks": audit["checks"]})


def save_audit(audit: dict[str, Any], directory: str | Path, version: int) -> Path:
    root = Path(directory)
    payload = {**audit, "hash": audit_hash(audit)}
    write_json(root / f"audit.v{version}.json", payload)
    write_json(root / "audit.json", payload)
    return root / f"audit.v{version}.json"
