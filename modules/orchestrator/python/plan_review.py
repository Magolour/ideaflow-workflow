"""计划质量检查：规格 → 计划之间的一致性闸门。

只做确定性静态检查，不调用模型。检查分三层：
1. 基本合法性：依赖/自依赖/环/工期上限
2. 规格覆盖：计划里出现的每一个 requirement_ref / design_ref / interface 必须在规格里有定义
3. 工期守恒：模块内拆分后的任务 estimate_days 总和不得超过原模块工期（5% 容差）
"""

from __future__ import annotations

import re

MAX_ESTIMATE_DAYS = 3.0
DAYS_TOLERANCE = 0.05  # 工期合计容差 5%，因为浮点数均分可能产生微小误差

_INTERFACE_CALL_RE = re.compile(r"调用\s+(\S+?)\s*→")


def _task_interface_names(task: dict) -> list[str]:
    """从验收项里的「… 调用 X → …」反解接口名（契约不允许任务带 interfaces 字段）。"""
    names: list[str] = []
    for item in task.get("acceptance_items") or []:
        match = _INTERFACE_CALL_RE.search(str(item))
        if match:
            names.append(match.group(1))
    return names

SEVERITY_BY_CODE = {
    # 阻断级：任务引用了规格里根本不存在的东西
    "MODULE_WITHOUT_TASK": "blocking",
    "DEPENDENCY_INVALID": "blocking",
    "DEPENDENCY_CYCLE": "blocking",
    "OUT_OF_SPEC_REQUIREMENT": "blocking",
    "OUT_OF_SPEC_DESIGN_REF": "blocking",
    "OUT_OF_SPEC_INTERFACE": "blocking",
    # 重要：工期超预算/任务过大
    "TASK_TOO_LARGE": "major",
    "MODULE_DAYS_EXCEEDED": "major",
}


def _cycles(task_ids: set[str], prerequisites: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan 强连通分量；返回所有规模大于 1 的环（已排序）。"""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    counter = [0]
    found: list[list[str]] = []

    def strongconnect(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for child in prerequisites.get(node, []):
            if child not in index:
                strongconnect(child)
                low[node] = min(low[node], low[child])
            elif child in on_stack:
                low[node] = min(low[node], index[child])
        if low[node] == index[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                found.append(sorted(component))

    for task_id in sorted(task_ids):
        if task_id not in index:
            strongconnect(task_id)
    return sorted(found)


def _collect_spec_scope(spec: dict) -> dict:
    """一次性收集规格里所有合法引用的集合，供反向检查用。"""
    valid_req_ids: set[str] = set()
    valid_mod_ids: set[str] = set()
    mod_interfaces: dict[str, set[str]] = {}  # module_id -> {interface_name}
    mod_total_days: dict[str, float] = {}    # module_id -> 原始 estimate_days

    for module in spec.get("modules") or []:
        mid = str(module.get("id") or "")
        if not mid:
            continue
        valid_mod_ids.add(mid)
        valid_req_ids.update(str(r) for r in (module.get("requirement_ids") or []))
        mod_interfaces[mid] = {
            (itf.get("name") if isinstance(itf, dict) else str(itf))
            for itf in (module.get("interfaces") or [])
        }
        if isinstance(module.get("estimate_days"), (int, float)):
            mod_total_days[mid] = float(module["estimate_days"])

    for row in spec.get("mapping") or []:
        if row.get("requirement_id"):
            valid_req_ids.add(str(row["requirement_id"]))
    for a in spec.get("acceptance") or []:
        if a.get("requirement_id"):
            valid_req_ids.add(str(a["requirement_id"]))

    return {
        "valid_req_ids": valid_req_ids,
        "valid_mod_ids": valid_mod_ids,
        "mod_interfaces": mod_interfaces,
        "mod_total_days": mod_total_days,
    }


def review_plan(spec: dict, plan: dict) -> dict:
    """检查计划对规格的覆盖、任务粒度与依赖合法性。"""
    tasks = plan.get("tasks") or []
    task_ids = {str(task.get("task_id")) for task in tasks}
    issues: list[dict] = []

    scope = _collect_spec_scope(spec)

    # ── 1. 基本合法性 ────────────────────────────────────────
    prerequisites: dict[str, list[str]] = {}
    for task in tasks:
        task_id = str(task.get("task_id"))
        prerequisites[task_id] = []

        # 依赖边：自依赖 / 不存在
        for ref in task.get("depends_on") or []:
            source = str(ref)
            if source == task_id:
                issues.append({
                    "code": "DEPENDENCY_INVALID", "severity": SEVERITY_BY_CODE["DEPENDENCY_INVALID"],
                    "task_id": task_id, "depends_on": source,
                    "message": f"任务 {task_id} 不能依赖自身",
                })
            elif source not in task_ids:
                issues.append({
                    "code": "DEPENDENCY_INVALID", "severity": SEVERITY_BY_CODE["DEPENDENCY_INVALID"],
                    "task_id": task_id, "depends_on": source,
                    "message": f"任务 {task_id} 依赖了不存在的任务 {source}",
                })
            else:
                prerequisites[task_id].append(source)

        # 工期超限
        days = task.get("estimate_days")
        if isinstance(days, (int, float)) and days > MAX_ESTIMATE_DAYS:
            issues.append({
                "code": "TASK_TOO_LARGE", "severity": SEVERITY_BY_CODE["TASK_TOO_LARGE"],
                "task_id": task_id, "estimate_days": float(days),
                "message": f"任务 {task_id} 预估 {days} 天，超过 {MAX_ESTIMATE_DAYS:g} 天上限",
            })

    # 环检测
    for cycle in _cycles(task_ids, prerequisites):
        issues.append({
            "code": "DEPENDENCY_CYCLE", "severity": SEVERITY_BY_CODE["DEPENDENCY_CYCLE"],
            "task_ids": cycle,
            "message": "依赖成环：" + " → ".join(cycle + [cycle[0]]),
        })

    # ── 2. 规格覆盖：反向检查 ──────────────────────────────
    for task in tasks:
        task_id = str(task.get("task_id"))

        # requirement_refs 必须全在 spec 里出现过
        for ref in task.get("requirement_refs") or []:
            ref = str(ref)
            if ref not in scope["valid_req_ids"]:
                issues.append({
                    "code": "OUT_OF_SPEC_REQUIREMENT", "severity": SEVERITY_BY_CODE["OUT_OF_SPEC_REQUIREMENT"],
                    "task_id": task_id, "requirement_ref": ref,
                    "message": f"任务 {task_id} 引用了规格里不存在的需求 {ref}",
                })

        # design_refs 必须全是规格模块 id
        for ref in task.get("design_refs") or []:
            ref = str(ref)
            if ref not in scope["valid_mod_ids"]:
                issues.append({
                    "code": "OUT_OF_SPEC_DESIGN_REF", "severity": SEVERITY_BY_CODE["OUT_OF_SPEC_DESIGN_REF"],
                    "task_id": task_id, "design_ref": ref,
                    "message": f"任务 {task_id} 引用了规格里不存在的模块 {ref}",
                })

        # 接口名必须属于 design_refs 里的某个模块；接口名从验收项反解
        for itf_name in _task_interface_names(task):
            owner_modules = [str(r) for r in (task.get("design_refs") or [])]
            found = False
            for owner in owner_modules:
                if itf_name in scope["mod_interfaces"].get(owner, set()):
                    found = True
                    break
            if not found:
                issues.append({
                    "code": "OUT_OF_SPEC_INTERFACE", "severity": SEVERITY_BY_CODE["OUT_OF_SPEC_INTERFACE"],
                    "task_id": task_id, "interface": itf_name,
                    "message": f"任务 {task_id} 的接口 {itf_name} 在其归属模块 {owner_modules} 里都不存在",
                })

    # 模块覆盖：每个规格模块至少被一个任务覆盖
    covered = set()
    for task in tasks:
        covered.update(str(ref) for ref in task.get("design_refs") or [])
        covered.update(str(ref) for ref in task.get("module_refs") or [])
        if task.get("module_id"):
            covered.add(str(task["module_id"]))

    for module in spec.get("modules") or []:
        module_id = module.get("id")
        if module_id and str(module_id) not in covered:
            issues.append({
                "code": "MODULE_WITHOUT_TASK", "severity": SEVERITY_BY_CODE["MODULE_WITHOUT_TASK"],
                "module_id": str(module_id),
                "message": f"模块 {module_id}（{module.get('name') or '未命名'}）没有任何任务覆盖",
            })

    # ── 3. 工期守恒：模块拆分后合计不超原模块 ────────────
    module_day_sum: dict[str, float] = {}
    for task in tasks:
        for ref in task.get("design_refs") or []:
            ref = str(ref)
            days = task.get("estimate_days")
            if isinstance(days, (int, float)):
                module_day_sum.setdefault(ref, 0)
                module_day_sum[ref] += float(days)

    for mid, total in module_day_sum.items():
        orig = scope["mod_total_days"].get(mid)
        if orig is None:
            continue
        if total > orig * (1 + DAYS_TOLERANCE):
            issues.append({
                "code": "MODULE_DAYS_EXCEEDED", "severity": SEVERITY_BY_CODE["MODULE_DAYS_EXCEEDED"],
                "module_id": mid, "original_days": orig, "split_days": round(total, 2),
                "message": f"模块 {mid} 原工期 {orig}d，拆分后合计 {round(total, 2)}d，超出 {DAYS_TOLERANCE:.0%} 容差",
            })

    # ── 汇总 ────────────────────────────────────────────────
    counts = {"blocking": 0, "major": 0, "minor": 0, "total": len(issues)}
    for issue in issues:
        sev = issue.get("severity", "minor")
        counts[sev] = counts.get(sev, 0) + 1

    return {
        "plan_id": plan.get("plan_id"),
        "spec_ref": plan.get("spec_ref"),
        "counts": counts,
        "issues": issues,
    }
