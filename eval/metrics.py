"""评测指标：纯函数，便于单测，不依赖模型与文件系统。"""
from __future__ import annotations


def _corpus(texts: list[str]) -> str:
    return "\n".join(texts).lower()


def coverage(requirement_texts: list[str], must_have: list[dict]) -> dict:
    """按关键词判定必含项是否出现，返回覆盖明细与比率。"""
    corpus = _corpus(requirement_texts)
    covered, missing = [], []
    for item in must_have:
        keywords = [k.lower() for k in item.get("keywords") or []]
        hit = bool(keywords) and all(k in corpus for k in keywords)
        (covered if hit else missing).append(item)
    total = len(must_have)
    return {
        "covered_ids": [item["id"] for item in covered],
        "missing_ids": [item["id"] for item in missing],
        "severe_missing_ids": [item["id"] for item in missing
                               if item.get("severity") == "severe"],
        "rate": round(len(covered) / total, 3) if total else 0.0,
        "total": total,
    }


def invalid_dependencies(tasks: list[dict]) -> list[str]:
    """检查 depends_on：引用存在、无自依赖、无环。返回问题描述列表。"""
    ids = {task["task_id"] for task in tasks}
    problems: list[str] = []
    graph: dict[str, list[str]] = {task_id: [] for task_id in ids}
    for task in tasks:
        for ref in task.get("depends_on") or []:
            if ref not in ids:
                problems.append(f"{task['task_id']} 依赖不存在的任务 {ref}")
            elif ref == task["task_id"]:
                problems.append(f"{task['task_id']} 依赖自身")
            else:
                graph[task["task_id"]].append(ref)

    indegree = {task_id: len(parents) for task_id, parents in graph.items()}
    children: dict[str, list[str]] = {task_id: [] for task_id in ids}
    for child, parents in graph.items():
        for parent in parents:
            children[parent].append(child)
    queue = [task_id for task_id, degree in indegree.items() if degree == 0]
    seen = 0
    while queue:
        current = queue.pop(0)
        seen += 1
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if seen != len(ids):
        stuck = sorted(task_id for task_id, degree in indegree.items() if degree > 0)
        problems.append("存在循环依赖：" + ", ".join(stuck))
    return problems


def summarize(case: dict, requirement_texts: list[str], tasks: list[dict],
              *, duration_s: float, tokens: int | None,
              human_interventions: int, mode: str) -> dict:
    cov = coverage(requirement_texts, case.get("must_have") or [])
    deps = invalid_dependencies(tasks)
    return {
        "case": case["case"],
        "mode": mode,
        "run": {
            "duration_s": round(duration_s, 3),
            "tokens": tokens,
            "human_interventions": human_interventions,
        },
        "plan": {
            "tasks": len(tasks),
            "invalid_dependencies": deps,
        },
        "requirements": {
            "count": len(requirement_texts),
            "coverage_rate": cov["rate"],
            "covered": cov["covered_ids"],
            "missing": cov["missing_ids"],
            "severe_missing": cov["severe_missing_ids"],
        },
    }
