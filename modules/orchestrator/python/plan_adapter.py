"""计划阶段：把已确认的设计规格（spec.json）转换为任务计划（plan.json）。

用法：
    py -3 plan_adapter.py spec.json --out output/plan_sample
    py -3 plan_adapter.py spec.json --out output/plan_sample --llm   # 调模型拆任务

两种模式：
- 默认：确定性映射，1 个模块 -> 1 个任务；
- --llm：把规格交给模型，拆出更细的任务清单（仍必须覆盖所有模块、依赖合法、无环）。
生成的 plan.json 可直接 POST 给编排器 /api/start。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

FALLBACK_STATUS = {401, 403, 404, 408, 409, 429}


def _load_env() -> None:
    """独立运行时读取同目录 .env。"""
    env_file = Path(__file__).resolve().parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _model_candidates() -> list[str]:
    models = [item.strip() for item in os.getenv("AI_MODELS", "").split(",") if item.strip()]
    primary = os.getenv("AI_MODEL", "").strip()
    if primary and primary not in models:
        models.insert(0, primary)
    return models


def chat_json(system: str, user: str) -> dict:
    """调用 OpenAI 兼容接口，返回 JSON；主模型不可用时按 AI_MODELS 切换。"""
    _load_env()
    api_key = os.getenv("AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""
    if not api_key:
        raise RuntimeError("未配置 AI_API_KEY，无法调用模型")
    base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    timeout = float(os.getenv("AI_TIMEOUT", "90"))
    errors: list[str] = []
    for model in _model_candidates():
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            if exc.code in FALLBACK_STATUS or exc.code >= 500:
                errors.append(f"{model} HTTP {exc.code}: {detail}")
                continue
            raise RuntimeError(f"模型调用失败：{model} HTTP {exc.code} {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            errors.append(f"{model} 不可达: {exc}")
            continue
        content = body["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`")
            content = content[4:] if content.lower().startswith("json") else content
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end <= start:
            raise RuntimeError("模型未返回 JSON 对象")
        return json.loads(content[start:end + 1])
    raise RuntimeError("所有模型均不可用：" + "；".join(errors))


def _now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


MAX_PART_DAYS = 3.0
MIN_PART_DAYS = 0.5


# 触发拆分的最低阈值：接口数≥4 或 验收项≥4 或 写路径≥4，即使工期≤3d 也拆
SPLIT_MIN_INTERFACES = 4
SPLIT_MIN_ACCEPTANCE = 4
SPLIT_MIN_WRITE_PATHS = 4


def _split_parts(module: dict) -> list[dict]:
    """把一个模块拆成若干段，每段 ≤ 3 天，接口数均分。

    规则：
      - 工期 > 3d → 强制拆分（每段 ≤ 3d）
      - 接口 ≥ 4 → 按接口数均分（最多 4 段，每段 ≥ 0.5d）
      - 验收 ≥ 4 / 路径 ≥ 4 → 同理
      - 全部不满足 → 单段
    """
    total_days = float(module.get("estimate_days") or 1.0)
    interfaces = module.get("interfaces") or []
    acceptance = module.get("acceptance_items") or []
    write_paths = module.get("write_paths") or []

    MAX_PARTS = 4  # 最多拆 4 段，再多没意义
    PART_DAYS_MIN = 0.5
    PART_DAYS_MAX = 3.0

    big_by_days = total_days > PART_DAYS_MAX
    big_by_if = len(interfaces) >= 4
    big_by_ac = len(acceptance) >= 4
    big_by_path = len(write_paths) >= 4

    # 完全没触发 → 单段返回
    if not (big_by_days or big_by_if or big_by_ac or big_by_path):
        return [_make_part("", interfaces, acceptance, write_paths, total_days)]

    # 各维度各自建议的段数
    # 工期约束：每段 ≤ 3d
    parts_by_days = max(1, int((total_days + PART_DAYS_MAX - 0.1) / PART_DAYS_MAX))
    parts_by_days = min(parts_by_days, MAX_PARTS)

    # 接口约束：13 接口 → 4 段（每段 3-4 个），不是 13 段
    parts_by_if = max(1, min(MAX_PARTS, len(interfaces) // 3 + (1 if len(interfaces) % 3 else 0))) if big_by_if else 1
    parts_by_ac = max(1, min(MAX_PARTS, len(acceptance) // 3 + (1 if len(acceptance) % 3 else 0))) if big_by_ac else 1
    parts_by_path = max(1, min(MAX_PARTS, len(write_paths) // 3 + (1 if len(write_paths) % 3 else 0))) if big_by_path else 1

    # 组合：取所有触发条件里的最大值（接口多就多拆）
    all = [parts_by_days, parts_by_if, parts_by_ac, parts_by_path]
    needed = max(all)

    # 硬约束：工期强制拆分时不超 parts_by_days
    if big_by_days:
        needed = min(needed, parts_by_days)

    # 防碎：每段至少 0.5d
    if total_days / needed < PART_DAYS_MIN:
        needed = max(1, int(total_days / PART_DAYS_MIN))

    # 兜底：needed 必须 ≤ 接口总数（否则没东西切）
    if len(interfaces) >= needed:
        return _chunk_interfaces(interfaces, acceptance, write_paths, needed, total_days)
    if len(acceptance) >= needed:
        return _chunk_acceptance(interfaces, acceptance, write_paths, needed, total_days)
    if len(write_paths) >= needed:
        return _chunk_paths(interfaces, acceptance, write_paths, needed, total_days)

    # 兜底：按工期均分，label 用阶段号
    return [{"label": f"阶段 {i + 1}", "interfaces": list(interfaces),
             "acceptance_items": list(acceptance), "write_paths": list(write_paths),
             "days": _distribute_days(total_days, needed, i)}
            for i in range(needed)]


def _make_part(label: str, interfaces: list, acceptance: list, write_paths: list,
               days: float, reason: str | None = None) -> dict:
    return {"label": label or "", "interfaces": list(interfaces),
            "acceptance_items": list(acceptance), "write_paths": list(write_paths),
            "days": float(days), "split_reason": reason}


def _chunk_interfaces(interfaces: list, acceptance: list, write_paths: list,
                      needed: int, total_days: float) -> list[dict]:
    """按接口数均分，验收项和写路径全复用。"""
    n = len(interfaces)
    chunk_size = max(1, (n + needed - 1) // needed)  # 向上取整
    parts = []
    for i in range(0, n, chunk_size):
        idx = len(parts)
        segment = interfaces[i:i + chunk_size]
        days = _distribute_days(total_days, needed, idx)
        label = _interface_label(segment) if idx > 0 else ""
        parts.append(_make_part(label, segment, acceptance, write_paths, days, "interfaces"))
    # 如果接口数不够填满 needed（比如 needed=4 但接口分成 3 段），最后一段兜底补
    while len(parts) < needed:
        days = _distribute_days(total_days, needed, len(parts))
        parts.append(_make_part(f"阶段 {len(parts) + 1}", [], acceptance, write_paths, days, "fallback"))
    return parts[:needed]


def _chunk_acceptance(interfaces: list, acceptance: list, write_paths: list,
                      needed: int, total_days: float) -> list[dict]:
    """按验收项均分，接口全复用。"""
    n = len(acceptance)
    chunk_size = max(1, (n + needed - 1) // needed)
    parts = []
    for i in range(0, n, chunk_size):
        idx = len(parts)
        segment = acceptance[i:i + chunk_size]
        days = _distribute_days(total_days, needed, idx)
        label = _interface_label(segment) if idx > 0 else ""
        parts.append(_make_part(label, interfaces, segment, write_paths, days, "acceptance"))
    return parts[:needed]


def _chunk_paths(interfaces: list, acceptance: list, write_paths: list,
                 needed: int, total_days: float) -> list[dict]:
    """按写路径均分。"""
    n = len(write_paths)
    chunk_size = max(1, (n + needed - 1) // needed)
    parts = []
    for i in range(0, n, chunk_size):
        idx = len(parts)
        segment = write_paths[i:i + chunk_size]
        days = _distribute_days(total_days, needed, idx)
        label = _interface_label(segment) if idx > 0 else ""
        parts.append(_make_part(label, interfaces, acceptance, segment, days, "paths"))
    return parts[:needed]


def _chunk_items(items: list, needed: int, total_days: float, kind: str) -> list[dict]:
    """把 items 均分给 needed 段，每段 label 用首个元素。"""
    n = len(items)
    chunk = max(1, int(n / needed + 0.99))  # 向上取整每段大小
    # 先均分 chunk 数，再调整
    actual_count = max(1, int((n + chunk - 1) / chunk))
    parts = []
    for i in range(0, n, chunk):
        segment = items[i:i + chunk]
        idx = i // chunk
        days = _distribute_days(total_days, actual_count, idx)
        parts.append({"label": _interface_label(segment),
                      "interfaces": segment if kind == "接口" else [],
                      "acceptance_items": segment if kind == "验收" else [],
                      "write_paths": segment if kind == "写路径" else [],
                      "days": days})
    return parts


def _interface_label(items: list) -> str:
    if not items:
        return ""
    first = items[0]
    if isinstance(first, dict):
        name = first.get("name") or first.get("acceptance") or first
        return str(name)[:18]
    return str(first)[:18]


def _interface_acceptance(itf) -> str:
    """把 {name, caller, input, output} 接口描述转成可读验收项。"""
    if not isinstance(itf, dict):
        return str(itf)
    name = itf.get("name") or "接口"
    caller = itf.get("caller") or "调用方"
    output = itf.get("output") or "正确返回"
    return f"{caller} 调用 {name} → {output}"


def _distribute_days(total: float, count: int, index: int) -> float:
    """把 total 均匀分给 count 段，落在 [MIN_PART_DAYS, MAX_PART_DAYS]。"""
    if count <= 0:
        return min(MAX_PART_DAYS, max(MIN_PART_DAYS, total))
    base = total / count
    # 用 round 避免 1.33333333 这种浮点数尾巴
    base = round(base * 2) / 2
    base = min(MAX_PART_DAYS, max(MIN_PART_DAYS, base))
    # 最后一段吃掉剩余误差
    if index == count - 1:
        return round(total - base * (count - 1), 2)
    return base


def plan_from_spec(spec: dict, *, plan_id: str | None = None,
                   max_parallel: int = 2, created_at: str | None = None) -> dict:
    """spec.json -> plan.json。

    做确定性映射 + 模块内拆分：estimate_days > 3 的模块按接口/验收项/写路径/工期上限拆。
    """
    modules = spec.get("modules") or []
    if not modules:
        raise ValueError("设计规格没有 modules，无法生成计划")
    spec_id = spec.get("spec_id")
    version = spec.get("version")
    if not spec_id or not isinstance(version, int):
        raise ValueError("设计规格缺少 spec_id 或 version")

    tasks = []
    # module_id -> [task_id, ...]，用于跨模块依赖取最后一段
    module_task_map: dict[str, list[str]] = {}
    next_idx = 1

    for module in modules:
        module_id = module.get("id")
        if module_id:
            module_task_map[module_id] = []

    for m_idx, module in enumerate(modules):
        name = module.get("name") or f"模块 {m_idx + 1}"
        role = module.get("suggested_role") or "ai"
        module_id = module.get("id")
        parts = _split_parts(module)
        part_days = [p["days"] for p in parts]

        # 模块外依赖：每个新段依赖被依赖模块的最后一段
        outer_deps = []
        for ref in module.get("depends_on") or []:
            if ref not in module_task_map:
                raise ValueError(f"模块 {module_id or name} 依赖了不存在的模块：{ref}")
            segs = module_task_map[ref]
            if segs:
                outer_deps.append(segs[-1])

        module_task_ids = []
        for p_idx, part in enumerate(parts):
            task_id = f"T{next_idx}"
            next_idx += 1
            module_task_ids.append(task_id)

            # 第一段依赖模块外依赖；后续段依赖前一段
            deps = list(outer_deps) if p_idx == 0 else [module_task_ids[p_idx - 1]]

            task_name = f"{name}" if p_idx == 0 else f"{name} · {part['label'] or f'阶段 {p_idx + 1}'}"

            # 验收项：优先用 part 里拆分出的验收项，其次把接口转成可读描述，最后兜底
            acceptance = part.get("acceptance_items") or []
            if not acceptance and part.get("interfaces"):
                acceptance = [_interface_acceptance(itf) for itf in part["interfaces"]]
            if not acceptance and p_idx == 0:
                acceptance = list(module.get("acceptance_items") or ["验收通过"])
            if not acceptance:
                acceptance = [f"阶段 {p_idx + 1} 功能验收通过"]

            # 写路径：每段带上自己拆分的路径，没有就兜底模块路径
            write_paths = list(part.get("write_paths") or module.get("write_paths") or [])

            # 把 part 里的接口信息挂到任务上（不是契约字段，作为注释性补充）
            part_interfaces = part.get("interfaces") or []
            interfaces_payload = [{"name": _if.get("name"), "caller": _if.get("caller"),
                                   "input": _if.get("input"), "output": _if.get("output")}
                                  for _if in part_interfaces if isinstance(_if, dict)]

            task = {
                "task_id": task_id,
                "name": task_name,
                "deliverable": module.get("deliverable") or f"{name}交付物",
                "acceptance_items": acceptance,
                "interfaces": interfaces_payload,
                "suggested_role": role,
                "priority": "high",
                "requirement_refs": list(module.get("requirement_ids") or []),
                "design_refs": [module_id] if module_id else [],
                "depends_on": deps,
                "write_paths": write_paths,
                "review_required": "human" in role,
                "estimate_days": float(part["days"]),
            }
            tasks.append(task)
            if module_id:
                module_task_map[module_id].append(task_id)

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


PLAN_SYSTEM_PROMPT = (
    "你是软件项目计划专家。把设计规格拆成可执行任务清单，只输出一个 JSON 对象。"
    "必须覆盖规格里的每一个模块：每个模块至少被一个任务的 design_refs 引用。"
    "任务要可独立执行、颗粒度适中，单个任务 0.5~3 天。"
    "依赖只能引用已存在的 task_id，不能自依赖、不能成环。"
)


def _task_payload(spec: dict) -> dict:
    modules = []
    for module in spec.get("modules") or []:
        modules.append({
            "id": module.get("id"),
            "name": module.get("name"),
            "responsibility": module.get("responsibility"),
            "requirement_ids": module.get("requirement_ids") or [],
            "interfaces": module.get("interfaces") or [],
            "acceptance_items": module.get("acceptance_items") or [],
            "depends_on": module.get("depends_on") or [],
            "write_paths": module.get("write_paths") or [],
            "estimate_days": module.get("estimate_days"),
        })
    return {
        "spec_id": spec.get("spec_id"),
        "version": spec.get("version"),
        "architecture": spec.get("architecture"),
        "decisions": [{"id": d.get("id"), "choice": d.get("choice")}
                      for d in spec.get("decisions") or []],
        "modules": modules,
    }


def _has_cycle(tasks: list[dict]) -> bool:
    graph = {task["task_id"]: list(task["depends_on"]) for task in tasks}
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        if any(visit(dep) for dep in graph.get(node, [])):
            return True
        state[node] = 2
        return False

    return any(visit(node) for node in graph)


def _spec_requirement_ids(spec: dict) -> set[str]:
    ids: set[str] = set()
    for module in spec.get("modules") or []:
        ids.update(module.get("requirement_ids") or [])
    for row in spec.get("mapping") or []:
        if row.get("requirement_id"):
            ids.add(row["requirement_id"])
    for item in spec.get("acceptance") or []:
        if item.get("requirement_id"):
            ids.add(item["requirement_id"])
    return {str(value) for value in ids if value}


def _budget_days(spec: dict) -> float | None:
    effort = spec.get("effort") or {}
    for key in ("commit_days", "expected_total"):
        value = effort.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    module_total = sum(float(module.get("estimate_days") or 0)
                       for module in spec.get("modules") or [])
    return module_total or None


def _module_info(spec: dict) -> tuple[dict[str, list[str]], dict[str, str]]:
    requirements: dict[str, list[str]] = {}
    roles: dict[str, str] = {}
    for module in spec.get("modules") or []:
        module_id = module.get("id")
        if not module_id:
            continue
        requirements[module_id] = [str(r) for r in module.get("requirement_ids") or []]
        roles[module_id] = str(module.get("suggested_role") or "ai")
    return requirements, roles


def _validate_plan_tasks(data: dict, spec: dict) -> list[dict]:
    raw_tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("模型没有返回 tasks 数组")
    known = set()
    for index, item in enumerate(raw_tasks, 1):
        task_id = str(item.get("task_id") or f"T{index}").strip()
        if task_id in known:
            raise ValueError(f"任务号重复：{task_id}")
        known.add(task_id)
    module_requirements, module_roles = _module_info(spec)
    module_ids = set(module_roles)
    requirement_ids = _spec_requirement_ids(spec)
    tasks = []
    for index, item in enumerate(raw_tasks, 1):
        task_id = str(item.get("task_id") or f"T{index}").strip()
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError(f"{task_id} 缺少 name")
        depends_on = [str(dep) for dep in item.get("depends_on") or []]
        for dep in depends_on:
            if dep == task_id:
                raise ValueError(f"{task_id} 自依赖")
            if dep not in known:
                raise ValueError(f"{task_id} 依赖了不存在的任务 {dep}")
        days = item.get("estimate_days")
        if not isinstance(days, (int, float)) or days <= 0 or days > 3:
            raise ValueError(f"{task_id} 的 estimate_days 必须在 (0, 3] 之间")
        design_refs = [str(ref) for ref in item.get("design_refs") or []]
        unknown = [ref for ref in design_refs if ref not in module_ids]
        if not design_refs or unknown:
            raise ValueError(f"{task_id} 的 design_refs 必须引用规格模块，非法：{unknown}")
        requirement_refs = [str(ref) for ref in item.get("requirement_refs") or []]
        if not requirement_refs:
            for ref in design_refs:
                requirement_refs.extend(module_requirements.get(ref, []))
        illegal = [ref for ref in requirement_refs if ref not in requirement_ids]
        if illegal:
            raise ValueError(f"{task_id} 引用了规格里不存在的需求：{illegal}")
        roles = {module_roles[ref] for ref in design_refs}
        role = "human" if "human" in roles else "ai"
        tasks.append({
            "task_id": task_id,
            "name": name,
            "deliverable": str(item.get("deliverable") or f"{name}交付物"),
            "acceptance_items": list(item.get("acceptance_items") or ["验收通过"]),
            "suggested_role": role,
            "priority": str(item.get("priority") or "high"),
            "requirement_refs": requirement_refs,
            "design_refs": design_refs,
            "depends_on": depends_on,
            "write_paths": list(item.get("write_paths") or []),
            "review_required": role == "human",
            "estimate_days": float(days),
        })
    if _has_cycle(tasks):
        raise ValueError("任务依赖存在环")
    covered = {ref for task in tasks for ref in task["design_refs"]}
    missing = sorted(module_ids - covered)
    if missing:
        raise ValueError(f"以下模块没有被任何任务覆盖：{', '.join(missing)}")
    total_days = sum(task["estimate_days"] for task in tasks)
    budget = _budget_days(spec)
    if budget and total_days > budget * 1.05:
        raise ValueError(f"任务总工期 {total_days} 天超过规格预算 {budget} 天")
    return tasks


def plan_from_spec_llm(spec: dict, *, plan_id: str | None = None,
                       max_parallel: int = 2) -> dict:
    """调用模型，根据规格生成更细的任务清单；失败时回退确定性映射。"""
    modules = spec.get("modules") or []
    spec_id = spec.get("spec_id")
    version = spec.get("version")
    if not modules:
        raise ValueError("设计规格没有 modules，无法生成计划")
    if not spec_id or not isinstance(version, int):
        raise ValueError("设计规格缺少 spec_id 或 version")

    budget = _budget_days(spec)
    budget_text = f"；所有任务 estimate_days 之和不得超过规格预算 {budget} 天" if budget else ""
    user = json.dumps(_task_payload(spec), ensure_ascii=False)
    rules = (
        "输出格式：{\"tasks\":[{\"task_id\":\"T1\",\"name\":\"\",\"deliverable\":\"\","
        "\"acceptance_items\":[\"\"],\"requirement_refs\":[\"R-01\"],"
        "\"design_refs\":[\"M-01\"],\"depends_on\":[\"T0\"],\"write_paths\":[\"\"],"
        "\"suggested_role\":\"ai\",\"priority\":\"high\",\"estimate_days\":1.5}]}。"
        "要求：任务数 8~20；每个任务 0.5~3 天；design_refs 必须使用规格里的模块 id；"
        "requirement_refs 只能使用规格里出现过的编号；不得增加规格没有的需求或功能；"
        "suggested_role 必须取所引用模块的角色，模块是 ai 就用 ai；"
        f"不要输出解释{budget_text}。"
    )
    last_error: Exception | None = None
    for _ in range(2):
        try:
            data = chat_json(PLAN_SYSTEM_PROMPT, rules + "\n规格：" + user)
            tasks = _validate_plan_tasks(data, spec)
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
                "created_at": _now(),
            }
        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
            last_error = exc
    print(f"[plan] 模型规划失败，回退确定性映射：{last_error}", file=sys.stderr)
    return plan_from_spec(spec, plan_id=plan_id, max_parallel=max_parallel)


def write_plan(spec_path: str | Path, out_dir: str | Path,
               plan_id: str | None = None, max_parallel: int = 2,
               use_llm: bool = False) -> Path:
    """读取 spec.json，生成 plan.json 并返回文件路径。"""
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    if use_llm:
        plan = plan_from_spec_llm(spec, plan_id=plan_id, max_parallel=max_parallel)
    else:
        plan = plan_from_spec(spec, plan_id=plan_id, max_parallel=max_parallel)
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    path = target / "plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plan_adapter.py", description="spec.json -> plan.json")
    parser.add_argument("spec", help="设计规格 spec.json 路径")
    parser.add_argument("--out", default="output/plan", help="输出目录，默认 output/plan")
    parser.add_argument("--plan-id", default=None, help="自定义计划号，默认 PLAN-<spec_id>")
    parser.add_argument("--max-parallel", type=int, default=2, help="最大并行数，默认 2")
    parser.add_argument("--llm", action="store_true", help="调用模型拆出更细的任务清单")
    args = parser.parse_args(argv)

    path = write_plan(args.spec, args.out, args.plan_id, args.max_parallel,
                      use_llm=args.llm)
    plan = json.loads(path.read_text(encoding="utf-8"))
    layers = {}
    for task in plan["tasks"]:
        if not task["depends_on"]:
            layers[task["task_id"]] = 1
    changed = True
    while changed:
        changed = False
        for task in plan["tasks"]:
            if task["task_id"] in layers:
                continue
            if all(dep in layers for dep in task["depends_on"]):
                layers[task["task_id"]] = max(layers[dep] for dep in task["depends_on"]) + 1
                changed = True
    print(f"计划已生成：{path}")
    print(f"计划号 {plan['plan_id']}，规格 {plan['spec_ref']['spec_id']} "
          f"v{plan['spec_ref']['version']}，任务 {len(plan['tasks'])} 个，"
          f"分层 {max(layers.values()) if layers else 0} 层")
    for task in plan["tasks"]:
        deps = ",".join(task["depends_on"]) or "-"
        print(f"  L{layers.get(task['task_id'], '?')} {task['task_id']} "
              f"{task['name']} 依赖={deps}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
