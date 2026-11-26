"""解析编排的四种启动输入，并调用需求模块或规格适配器。"""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from plan_adapter import plan_from_spec, plan_from_spec_llm
from spec_gate import SpecGateError, attach_local_review, validate_spec_review


@dataclass
class StartEntry:
    tasks: list[dict]
    spec_version: str
    list_version: str
    plan_id: str | None
    plan: dict | None
    spec: dict | None
    requirement: str = ""
    summary: str = ""


def normalize_tasks(data) -> tuple[list[dict], str]:
    spec_version = "v1.0"
    if isinstance(data, dict) and "task_list_approved_v1.2" in data:
        payload = data["task_list_approved_v1.2"]
        tasks = payload["tasks"]
        spec_version = payload.get("spec_version", spec_version)
    elif isinstance(data, dict):
        tasks = data.get("tasks", [])
        spec_version = data.get("spec_version", spec_version)
    elif isinstance(data, list):
        tasks = data
    else:
        raise ValueError("输入格式无法识别：需要契约包、{\"tasks\": [...]} 或纯数组")

    normalized, seen = [], set()
    for index, item in enumerate(tasks):
        task_id = str(item.get("task_id") or f"T{index + 1}").strip()
        if task_id in seen:
            raise ValueError(f"任务编号重复：{task_id}")
        seen.add(task_id)
        task = {
            "task_id": task_id,
            "name": str(item.get("name") or "").strip(),
            "deliverable": str(item.get("deliverable") or "").strip(),
            "acceptance_items": list(item.get("acceptance_items") or []),
            "suggested_role": item.get("suggested_role", "ai"),
            "priority": item.get("priority", "medium"),
            "requirement_refs": list(item.get("requirement_refs") or []),
            "design_refs": list(item.get("design_refs") or []),
            "depends_on": [str(ref) for ref in item.get("depends_on") or []],
            "write_paths": list(item.get("write_paths") or []),
            "review_required": bool(item.get("review_required", False)),
        }
        if isinstance(item.get("estimate_days"), (int, float)):
            task["estimate_days"] = float(item["estimate_days"])
        normalized.append(task)
    if not normalized:
        raise ValueError("任务清单为空")
    return normalized, spec_version


class RequirementsUnavailable(RuntimeError):
    """需求模块不可用：HTTP 层转成 503，不静默降级成一句话直拆。"""


def requirements_dir() -> Path:
    configured = os.getenv("REQUIREMENTS_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "requirements"


def requirement_to_spec(requirement: str, out_dir: Path) -> dict:
    """一句话需求 -> 需求模块离线生成 spec.json。"""
    source = requirements_dir()
    if not (source / "ideaflow" / "specflow.py").exists():
        raise RequirementsUnavailable(f"需求模块不可用：{source}")
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    try:
        from ideaflow.specflow import run as run_specflow
    except ImportError as exc:
        raise RequirementsUnavailable(f"需求模块导入失败：{exc}") from None

    agent = None
    if os.getenv("REQUIREMENTS_LIVE", "").lower() in {"1", "true", "yes"}:
        try:
            from ideaflow.agents import LLMAgent
            agent = LLMAgent()
        except Exception as exc:
            raise RequirementsUnavailable(f"需求模块模型初始化失败：{exc}") from None
    try:
        result = run_specflow(requirement, out_dir=out_dir, agent=agent, discuss=True)
        run_dir = Path(result.out_dir)
        spec = json.loads((run_dir / "spec.json").read_text(encoding="utf-8"))
        if spec.get("review") is None:
            audit = json.loads((run_dir / "audit.json").read_text(encoding="utf-8"))
            spec = attach_local_review(spec, audit, run_dir.name)
    except Exception as exc:
        raise RequirementsUnavailable(f"需求模块执行失败：{exc}") from exc
    try:
        validate_spec_review(spec)
    except SpecGateError as exc:
        exc.details = {**exc.details, "run_id": run_dir.name}
        raise
    return spec


def _build_plan(spec: dict, use_llm: bool) -> dict:
    return plan_from_spec_llm(spec) if use_llm else plan_from_spec(spec)


def parse_start(data, requirements_output: Path, list_version: str,
                use_llm: bool = False) -> StartEntry:
    spec = None
    requirement = ""
    summary = ""
    plan = None

    if isinstance(data, dict) and ("spec_id" in data or "modules" in data):
        spec = data
        validate_spec_review(spec)
        try:
            plan = _build_plan(spec, use_llm)
        except ValueError as exc:
            raise ValueError(f"设计规格无法生成计划：{exc}") from None
    elif isinstance(data, dict) and not data.get("tasks"):
        requirement = str(data.get("requirement") or "").strip()
        if requirement:
            spec = requirement_to_spec(requirement, requirements_output)
            try:
                plan = _build_plan(spec, use_llm)
            except ValueError as exc:
                raise ValueError(f"需求规格无法生成计划：{exc}") from None
            summary = f"需求模块生成 {spec['spec_id']} v{spec['version']}"

    if plan is not None:
        tasks, _ = normalize_tasks(plan)
        return StartEntry(tasks, f"v{plan['spec_ref']['version']}", list_version,
                          str(plan["plan_id"]), plan, spec, requirement, summary)

    tasks, spec_version = normalize_tasks(data)
    plan_id = None
    if isinstance(data, dict):
        payload = data.get("task_list_approved_v1.2", data)
        list_version = payload.get("list_version", list_version)
        if payload.get("plan_id"):
            plan_id = str(payload["plan_id"])
            if payload.get("tasks"):
                plan = payload
        spec_ref = payload.get("spec_ref")
        if isinstance(spec_ref, dict) and isinstance(spec_ref.get("version"), int):
            spec_version = f"v{spec_ref['version']}"
    return StartEntry(tasks, spec_version, list_version, plan_id, plan, spec)


def reviewed_replacement(current: dict, replacement: dict | None) -> dict:
    if replacement is None:
        raise ValueError("退回规格需要提交已复审通过的下一版规格")
    if (not isinstance(replacement, dict) or replacement.get("spec_id") != current["spec_id"]
            or replacement.get("version") != current["version"] + 1):
        raise ValueError("修订稿必须是同一规格的下一版")
    validate_spec_review(replacement)
    return replacement
