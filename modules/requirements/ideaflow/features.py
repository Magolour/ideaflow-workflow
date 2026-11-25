"""讨论收敛后的功能归纳：把观点与方案范围收成可落模块的清单。

归纳结果必须能直接落成模块：名称是功能而不是过程约束，来源必须指向真实的
快照/主张/方案编号，是否推断要显式标注。真实模型和离线模板都走这里的同一套
校验，避免两个实现各说各话。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .constraints import shares_topic
from .contracts import ContractError
from .roles import Role
from .snapshot import Snapshot
from .storage import now_iso, write_json

FEATURE_ROLE = Role(
    id="feature",
    name="功能归纳",
    focus="把讨论结论收敛为可交付的功能，而不是复述问题或过程",
    must_answer=("哪些能力是首版必须有的？", "每条功能能追到哪条讨论结论？"),
    forbidden="不要把问题定义、关键路径、结构边界写成功能名。",
)

# 这些词描述的是讨论过程或方案边界，不是用户能用的功能。
PROCESS_WORDS = ("问题定义", "关键路径", "全部范围", "结构边界", "剩余预算")
PRIORITIES = ("high", "medium", "low")
MIN_FEATURES, MAX_FEATURES = 3, 12


def claim_requirements(snapshot: Snapshot, board: Any) -> dict[str, list[str]]:
    """把讨论主张/方案范围映射到快照需求编号。

    模型给的主张通常不带 requirement_ids；这里先用显式引用，再用正文重叠
    （双向重叠，防止长句包短句时漏掉）反查，最后把方案范围按“范围：…”条目
    对应回需求。映射结果同时用于 features 上下文和规格生成。
    """
    known = {item["id"] for item in snapshot.requirements}
    mapping: dict[str, list[str]] = {}

    def match(text: str) -> list[str]:
        hits: list[str] = []
        for item in snapshot.requirements:
            requirement_text = str(item.get("text") or "")
            if not requirement_text:
                continue
            if (shares_topic(text, requirement_text)
                    or shares_topic(requirement_text, text)):
                hits.append(item["id"])
        return list(dict.fromkeys(hits))[:5]

    for row in getattr(board, "claims", []):
        explicit = [str(item) for item in (row.get("requirement_ids") or [])
                    if str(item) in known]
        mapping[row["id"]] = (list(dict.fromkeys(explicit))
                              if explicit else match(str(row.get("text") or "")))

    for row in getattr(board, "options", []):
        scopes = [str(item) for item in (row.get("scope") or []) if str(item)]
        hits: list[str] = []
        for scope in scopes:
            hits.extend(item["id"] for item in snapshot.requirements
                        if str(item.get("text") or "") == f"范围：{scope}")
        if not hits:
            hits = match(" ".join(scopes))
        mapping[row["id"]] = list(dict.fromkeys(hits))[:5]
    return mapping


def build_context(idea: str, snapshot: Snapshot, board: Any) -> dict[str, Any]:
    refs = claim_requirements(snapshot, board)
    return {
        "idea": idea,
        "snapshot": snapshot.to_json(),
        "claims": [{"id": row["id"], "text": row.get("text", ""),
                    "role": row.get("role", ""),
                    "requirement_ids": refs.get(row["id"], [])}
                   for row in getattr(board, "claims", [])],
        "options": [{"id": row["id"], "name": row.get("name", ""),
                     "scope": list(row.get("scope") or []),
                     "requirement_ids": refs.get(row["id"], [])}
                    for row in getattr(board, "options", [])],
    }


def _allowed_ids(context: dict[str, Any]) -> set[str]:
    allowed = {item["id"] for item in context.get("snapshot", {}).get("items", [])
               if item.get("id")}
    allowed |= {row["id"] for row in context.get("claims", []) if row.get("id")}
    allowed |= {row["id"] for row in context.get("options", []) if row.get("id")}
    return allowed


def _inferred(value: Any, index: int) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
    raise ContractError("FEATURE_INFERRED", "inferred 必须明确标成布尔值",
                        f"features[{index}].inferred")


def normalize_features(data: dict[str, Any],
                       context: dict[str, Any]) -> dict[str, Any]:
    """校验并归一化功能清单；不合格时抛 ContractError，由调用方决定重试。"""
    allowed = _allowed_ids(context)
    rows = data.get("features") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not (MIN_FEATURES <= len(rows) <= MAX_FEATURES):
        raise ContractError("FEATURE_COUNT",
                            f"功能清单需要 {MIN_FEATURES}~{MAX_FEATURES} 条", "features")

    features: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ContractError("INVALID_FIELD", "功能必须是对象", f"features[{index}]")
        name = str(row.get("name") or "").strip()
        if not (2 <= len(name) <= 24):
            raise ContractError("FEATURE_NAME", "功能名需要 2~24 字",
                                f"features[{index}].name")
        if any(word in name for word in PROCESS_WORDS):
            raise ContractError("FEATURE_NAME", f"功能名仍是过程约束：{name}",
                                f"features[{index}].name")
        if name in seen:
            raise ContractError("FEATURE_NAME", f"功能名重复：{name}",
                                f"features[{index}].name")
        seen.add(name)

        refs = [str(item) for item in (row.get("source_refs") or [])]
        if not refs:
            raise ContractError("FEATURE_REFS", "每条功能至少一个来源",
                                f"features[{index}]")
        unknown = [item for item in refs if item not in allowed]
        if unknown:
            raise ContractError("FEATURE_REFS", f"来源不存在：{unknown[0]}",
                                f"features[{index}].source_refs")

        features.append({
            "name": name,
            "description": str(row.get("description") or "").strip() or name,
            "source_refs": refs,
            "inferred": _inferred(row.get("inferred", True), index),
            "priority": (row.get("priority") if row.get("priority") in PRIORITIES
                         else "high"),
        })
    data["features"] = features
    return data


def synthesize(idea: str, snapshot: Snapshot, board: Any, agent: Any,
               out_dir: str | Path) -> dict[str, Any]:
    context = build_context(idea, snapshot, board)
    output = agent.respond("features", FEATURE_ROLE, context)
    if not isinstance(output, dict):
        raise ContractError("FEATURE_OUTPUT", "功能归纳必须返回对象", "features")
    features = normalize_features(output, context)["features"]
    for index, feature in enumerate(features, 1):
        feature["id"] = f"F-{index:02d}"
    payload = {
        "snapshot_ref": {"snapshot_id": snapshot.snapshot_id,
                         "version": snapshot.version},
        "features": features,
        "created_at": now_iso(),
    }
    write_json(Path(out_dir) / "features.json", payload)
    return payload
