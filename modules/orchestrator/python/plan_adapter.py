"""把已确认的设计规格转换为编排器可消费的任务计划。"""
from __future__ import annotations

from datetime import datetime


def _now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def plan_from_spec(spec: dict, *, plan_id: str | None = None,
                   max_parallel: int = 2, created_at: str | None = None) -> dict:
    """spec.json -> plan.json。

    只做确定性映射，不调用模型；缺少的字段用规格中的职责/验收兜底。
    """
    modules = spec.get("modules") or []
    if not modules:
        raise ValueError("设计规格没有 modules，无法生成计划")
    spec_id = spec.get("spec_id")
    version = spec.get("version")
    if not spec_id or not isinstance(version, int):
        raise ValueError("设计规格缺少 spec_id 或 version")

    tasks = []
    module_to_task = {}
    for index, module in enumerate(modules, 1):
        if module.get("id"):
            module_to_task[module["id"]] = f"T{index}"

    for index, module in enumerate(modules, 1):
        name = module.get("name") or f"模块 {index}"
        role = module.get("suggested_role") or "ai"
        depends_on = []
        for ref in module.get("depends_on") or []:
            if ref not in module_to_task:
                raise ValueError(f"模块 {module.get('id') or name} 依赖了不存在的模块：{ref}")
            depends_on.append(module_to_task[ref])
        task = {
            "task_id": f"T{index}",
            "name": name,
            "deliverable": module.get("deliverable") or f"{name}交付物",
            "acceptance_items": module.get("acceptance_items") or ["验收通过"],
            "suggested_role": role,
            "priority": "high",
            "requirement_refs": list(module.get("requirement_ids") or []),
            "design_refs": [module.get("id")] if module.get("id") else [],
            "depends_on": depends_on,
            "write_paths": list(module.get("write_paths") or []),
            "review_required": "human" in role,
        }
        if isinstance(module.get("estimate_days"), (int, float)):
            task["estimate_days"] = float(module["estimate_days"])
        tasks.append(task)

    return {
        "plan_id": plan_id or f"PLAN-{spec_id}",
        "version": 1,
        "spec_ref": {
            "spec_id": spec_id,
            "version": version,
            "hash": spec.get("hash") or "0" * 64,
        },
        "max_parallel": max_parallel,
        "tasks": tasks,
        "status": "draft",
        "created_at": created_at or _now(),
    }
