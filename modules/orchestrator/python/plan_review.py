"""计划质量检查：规格 → 计划之间的一致性闸门。

只做确定性静态检查，不调用模型；返回的问题列表为空表示计划干净。
"""

from __future__ import annotations

MAX_ESTIMATE_DAYS = 3.0
SEVERITY_BY_CODE = {
    "DEPENDENCY_CYCLE": "blocking",
    "DEPENDENCY_INVALID": "blocking",
    "MODULE_WITHOUT_TASK": "blocking",
    "TASK_TOO_LARGE": "major",
}
SEVERITY_LEVELS = ("blocking", "major", "minor")


def _issue(code: str, message: str, *, task_id: str | None = None, **extra) -> dict:
    return {
        "code": code,
        "severity": SEVERITY_BY_CODE.get(code, "minor"),
        "task_id": task_id,
        "message": message,
        **extra,
    }


def _cycles(task_ids: set[str], prerequisites: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan 强连通分量；返回所有规模大于 1 的环（已排序，便于断言）。"""
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


def review_plan(spec: dict, plan: dict) -> dict:
    """检查计划对规格的覆盖、任务粒度与依赖合法性。"""
    tasks = plan.get("tasks") or []
    task_ids = {str(task.get("task_id")) for task in tasks}
    issues: list[dict] = []

    covered = set()
    for task in tasks:
        covered.update(str(ref) for ref in task.get("design_refs") or [])
        covered.update(str(ref) for ref in task.get("module_refs") or [])
        if task.get("module_id"):
            covered.add(str(task["module_id"]))

    for module in spec.get("modules") or []:
        module_id = module.get("id")
        if module_id and str(module_id) not in covered:
            issues.append(_issue(
                "MODULE_WITHOUT_TASK",
                f"模块 {module_id}（{module.get('name') or '未命名'}）没有任何任务覆盖",
                module_id=str(module_id),
            ))

    prerequisites: dict[str, list[str]] = {}
    for task in tasks:
        task_id = str(task.get("task_id"))
        prerequisites[task_id] = []
        for ref in task.get("depends_on") or []:
            source = str(ref)
            if source == task_id:
                issues.append(_issue(
                    "DEPENDENCY_INVALID",
                    f"任务 {task_id} 不能依赖自身",
                    task_id=task_id,
                    depends_on=source,
                ))
            elif source not in task_ids:
                issues.append(_issue(
                    "DEPENDENCY_INVALID",
                    f"任务 {task_id} 依赖了不存在的任务 {source}",
                    task_id=task_id,
                    depends_on=source,
                ))
            else:
                prerequisites[task_id].append(source)
        days = task.get("estimate_days")
        if isinstance(days, (int, float)) and days > MAX_ESTIMATE_DAYS:
            issues.append(_issue(
                "TASK_TOO_LARGE",
                f"任务 {task_id} 预估 {days} 天，超过 {MAX_ESTIMATE_DAYS:g} 天上限",
                task_id=task_id,
                estimate_days=float(days),
            ))

    for cycle in _cycles(task_ids, prerequisites):
        issues.append(_issue(
            "DEPENDENCY_CYCLE",
            "依赖成环：" + " → ".join(cycle + [cycle[0]]),
            task_id=cycle[0],
            task_ids=cycle,
        ))

    counts = {level: sum(1 for issue in issues if issue["severity"] == level)
              for level in SEVERITY_LEVELS}
    counts["total"] = len(issues)
    return {
        "plan_id": plan.get("plan_id"),
        "spec_ref": plan.get("spec_ref"),
        "counts": counts,
        "issues": issues,
    }
