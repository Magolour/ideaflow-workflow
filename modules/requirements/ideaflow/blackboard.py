"""共享黑板：结构化状态中心。

设计依据：文档第一部分 3.1 与第四部分 2。Agent 读写的是结构化数据而不是
整段聊天记录，写入前必须通过契约校验。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .contracts import (CONSENSUS_LABEL, ContractError, STANCE_LABEL,
                        new_id, validate_role_output)


def now_iso() -> str:
    """UTC 时间，秒级。文档 5.1 要求事件带时间且可追溯。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class EventLog:
    """追加式事件流，一行一个事件，便于增量拉取和回放。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.stage = ""
        self.round = 0

    def emit(self, actor: str, action: str, detail: str, *, object_type: str = "blackboard",
             object_id: str = "", version: int = 0) -> dict[str, Any]:
        event = {
            "seq": len(self.events) + 1,
            "ts": now_iso(),
            "actor": actor,
            "object_type": object_type,
            "object_id": object_id,
            "action": action,
            "detail": detail,
            "version": version,
            "stage": self.stage,
            "round": self.round,
        }
        self.events.append(event)
        return event

    def to_jsonl(self) -> str:
        import json
        return "\n".join(json.dumps(item, ensure_ascii=False) for item in self.events)


class Blackboard:
    """一次讨论的全部结构化状态。编号一律由代码分配，模型只提供内容。"""

    def __init__(self, idea: str, constraints: list[str] | None = None) -> None:
        self.idea = idea.strip()
        self.constraints = [item.strip() for item in (constraints or []) if item.strip()]
        self.assumptions: list[dict[str, Any]] = []
        self.claims: list[dict[str, Any]] = []
        self.objections: list[dict[str, Any]] = []
        self.options: list[dict[str, Any]] = []
        self.stances: dict[str, dict[str, str]] = {}
        self.decisions: list[dict[str, Any]] = []
        self.non_goals: list[dict[str, str]] = []
        self.not_adopted: list[dict[str, str]] = []
        self.open_questions: list[dict[str, Any]] = []
        self.work_packages: list[dict[str, Any]] = []
        self.narrative: dict[str, str] = {}
        self.stance_changes: list[dict[str, Any]] = []
        self.divergence: dict[str, str] = {}
        self.responses: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []
        self.termination: dict[str, Any] | None = None
        self.pseudo_check: dict[str, Any] | None = None
        self.consensus_override: dict[str, str] | None = None
        self.stage = "intake"
        self.round = 0
        self.version = 1
        self.history: list[dict[str, Any]] = []

    # ---------- 写入 ----------

    def add_role_output(self, raw: Any, *, role_id: str, stage: str) -> dict[str, Any]:
        """校验并写入一个角色的输出，返回归一化结果与新增编号。"""
        known = {claim["id"] for claim in self.claims}
        output = validate_role_output(
            raw, role_id=role_id, stage=stage, known_claims=known,
            known_objections={row["id"] for row in self.objections})

        assumption_ids: list[str] = []
        for row in output["assumptions"]:
            assumption = {
                "id": new_id("ASM", self.assumptions),
                "text": row["text"],
                "verify_by": row["verify_by"],
                "status": "unverified",
                "role": role_id,
                "round": self.round,
            }
            self.assumptions.append(assumption)
            assumption_ids.append(assumption["id"])

        claim_ids: list[str] = []
        for row in output["claims"]:
            assumed = []
            for index in row["assumes"]:
                if not 0 <= index < len(assumption_ids):
                    raise ContractError(
                        "UNKNOWN_ASSUMPTION",
                        f"主张引用了本轮不存在的假设序号 {index}",
                        f"claims[{len(claim_ids)}].assumes")
                assumed.append(assumption_ids[index])
            claim = {
                "id": new_id("CLM", self.claims),
                "role": role_id,
                "stage": stage,
                "round": self.round,
                "text": row["text"],
                "reason": row["reason"],
                "evidence": row["evidence"],
                "assumes": assumed,
            }
            if row.get("requirement_ids"):
                claim["requirement_ids"] = list(row["requirement_ids"])
            self.claims.append(claim)
            claim_ids.append(claim["id"])

        for row in output["objections"]:
            self.objections.append({
                "id": new_id("OBJ", self.objections),
                "role": role_id,
                "round": self.round,
                "target": row["target"],
                "text": row["text"],
                "consequence": row["consequence"],
                "severity": row["severity"],
                "status": "open",
            })

        for text in output["questions"]:
            self.add_question(text, source=role_id)

        if output["position_changed"]:
            self.stance_changes.append({
                "role": role_id,
                "round": self.round,
                **output["position_changed"],
            })

        return {**output, "assumption_ids": assumption_ids, "claim_ids": claim_ids}

    def add_question(self, text: str, *, source: str, blocking: bool = False,
                     owner: str = "human") -> dict[str, Any]:
        for row in self.open_questions:
            if row["text"] == text:
                return row
        row = {
            "id": new_id("Q", self.open_questions),
            "text": text,
            "source": source,
            "blocking": blocking,
            "owner": owner,
            "resolution": None,
        }
        self.open_questions.append(row)
        return row

    def resolve_question(self, question_id: str, resolution: str, *, owner: str = "human") -> None:
        for row in self.open_questions:
            if row["id"] == question_id:
                row["resolution"] = resolution
                row["owner"] = owner
                return
        raise ContractError("UNKNOWN_QUESTION", f"不存在的问题 {question_id}", "question_id")

    def resolve_objection(self, objection_id: str, resolution: str, *,
                          decider: str = "human") -> None:
        """人工裁决未解决异议。模型不能替人关闭异议。"""
        for row in self.objections:
            if row["id"] == objection_id:
                row["status"] = "resolved"
                row["resolution"] = resolution
                row["decided_by"] = decider
                row["outcome"] = "human"
                return
        raise ContractError("UNKNOWN_OBJECTION", f"不存在的异议 {objection_id}", "objection_id")

    def apply_responses(self, role_id: str, responses: list[dict[str, Any]]) -> None:
        """写入角色对异议的回应。

        接受或反驳都会关闭异议：反驳必须给出理由，不允许静默忽略；
        有条件接受保持 open，等证据或条件落地。
        """
        for row in responses:
            target = self.objection_by_id(row["objection"])
            if target["status"] != "open":
                raise ContractError("OBJECTION_CLOSED",
                                    f"异议 {target['id']} 已关闭，不能重复回应",
                                    "objection")
            target["response"] = {"role": role_id, "position": row["position"],
                                  "argument": row["argument"],
                                  "evidence": row["evidence"]}
            if row["position"] in ("accept", "refute"):
                target["status"] = "resolved"
                target["outcome"] = row["position"]
                target["resolution"] = row["argument"]
            self.responses.append({
                "role": role_id,
                "objection": target["id"],
                "position": row["position"],
                "argument": row["argument"],
                "evidence": row["evidence"],
                "round": self.round,
            })

    def apply_reviews(self, role_id: str, reviews: list[dict[str, str]]) -> None:
        """写入整合复核结论。原角色没有否决权，但失真会作为阻断项上报。"""
        own = {claim["id"] for claim in self.claims if claim["role"] == role_id}
        for row in reviews:
            if row["claim"] not in own:
                raise ContractError("NOT_OWN_CLAIM",
                                    f"角色只能复核自己的论点：{row['claim']}", "claim")
            self.reviews.append({"role": role_id, **row, "round": self.round})

    def objection_by_id(self, objection_id: str) -> dict[str, Any]:
        for row in self.objections:
            if row["id"] == objection_id:
                return row
        raise ContractError("UNKNOWN_OBJECTION", f"不存在异议 {objection_id}", "objection_id")

    def set_termination(self, termination: dict[str, Any]) -> None:
        self.termination = termination

    def set_pseudo_check(self, check: dict[str, Any]) -> None:
        self.pseudo_check = check

    def set_consensus(self, state: str, reason: str) -> None:
        self.consensus_override = {"state": state, "reason": reason}

    def add_option(self, *, key: str, name: str, scope: list[str], cost_days: str,
                   risk: str, tradeoffs: str, based_on: list[str]) -> dict[str, Any]:
        option = {
            "id": f"OPT-{key}",
            "key": key,
            "name": name,
            "scope": list(scope),
            "cost_days": cost_days,
            "risk": risk,
            "tradeoffs": tradeoffs,
            "based_on": list(based_on),
        }
        self.options.append(option)
        return option

    def record_stance(self, role_id: str, option_id: str, stance: str, *,
                      reason: str = "", condition: str = "") -> None:
        if stance not in STANCE_LABEL:
            raise ContractError("INVALID_ENUM", f"未知立场 {stance}", "stance")
        if stance == "conditional" and not condition.strip():
            raise ContractError("MISSING_CONDITION", "有条件赞同必须写明条件", "condition")
        if stance == "oppose" and not reason.strip():
            raise ContractError("MISSING_REASON", "反对必须写明理由", "reason")
        self.stances.setdefault(role_id, {})[option_id] = stance
        for option in self.options:
            if option["id"] == option_id:
                option.setdefault("stance_reasons", {})[role_id] = {
                    "stance": stance, "reason": reason, "condition": condition}
                return
        raise ContractError("UNKNOWN_OPTION", f"不存在方案 {option_id}", "option_id")

    def add_decision(self, *, scope: str, choice: str, reason: str,
                     based_on: list[str] | None = None,
                     decider: str = "human") -> dict[str, Any]:
        decision = {
            "id": new_id("DEC", self.decisions),
            "scope": scope,
            "choice": choice,
            "reason": reason,
            "based_on": list(based_on or []),
            "decider": decider,
        }
        self.decisions.append(decision)
        return decision

    def add_non_goal(self, item: str, reason: str) -> None:
        self.non_goals.append({"item": item, "reason": reason})

    # ---------- 派生判断 ----------

    def open_objections(self, severity: str | None = None) -> list[dict[str, Any]]:
        return [row for row in self.objections
                if row["status"] == "open" and (severity is None or row["severity"] == severity)]

    def unresolved_blocking(self) -> list[dict[str, Any]]:
        return self.open_objections("blocking")

    def consensus_state(self) -> str:
        """基础判定：阻断项未解决即不能称共识。

        流水线在收敛阶段会写入更完整的判定（含分歧分类与终止条件），
        此时以写入结果为准。
        """
        if self.consensus_override:
            return self.consensus_override["state"]
        if self.unresolved_blocking():
            return "no_consensus"
        if any(row["status"] == "unverified" and row["role"] != "tech"
               for row in self.assumptions):
            return "pending_verification"
        if any(stance == "conditional" for row in self.stances.values()
               for stance in row.values()):
            return "conditional"
        return "consensus"

    def consensus_reason(self) -> str:
        if self.consensus_override:
            return self.consensus_override["reason"]
        blocking = self.unresolved_blocking()
        if blocking:
            return f"存在 {len(blocking)} 条未解决的阻断异议（{blocking[0]['id']}）"
        if self.consensus_state() == "pending_verification":
            return "部分关键假设尚未验证"
        if self.consensus_state() == "conditional":
            return "存在有条件赞同，条件需写入确认书"
        return "无未解决阻断项，立场齐备"

    # ---------- 输出 ----------

    def checkpoint(self, label: str) -> None:
        self.history.append({"label": label, "stage": self.stage,
                             "version": self.version, "state": self.snapshot()})
        self.version += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "idea": self.idea,
            "constraints": deepcopy(self.constraints),
            "assumptions": deepcopy(self.assumptions),
            "claims": deepcopy(self.claims),
            "objections": deepcopy(self.objections),
            "options": deepcopy(self.options),
            "stances": deepcopy(self.stances),
            "decisions": deepcopy(self.decisions),
            "non_goals": deepcopy(self.non_goals),
            "open_questions": deepcopy(self.open_questions),
            "stance_changes": deepcopy(self.stance_changes),
            "divergence": deepcopy(self.divergence),
            "responses": deepcopy(self.responses),
            "reviews": deepcopy(self.reviews),
            "termination": deepcopy(self.termination),
            "pseudo_check": deepcopy(self.pseudo_check),
            "not_adopted": deepcopy(self.not_adopted),
            "work_packages": deepcopy(self.work_packages),
            "narrative": deepcopy(self.narrative),
            "stage": self.stage,
            "round": self.round,
            "version": self.version,
            "consensus": {
                "state": self.consensus_state(),
                "label": CONSENSUS_LABEL[self.consensus_state()],
                "reason": self.consensus_reason(),
            },
        }

    def claim_by_id(self, claim_id: str) -> dict[str, Any]:
        for claim in self.claims:
            if claim["id"] == claim_id:
                return claim
        raise ContractError("UNKNOWN_CLAIM", f"不存在论点 {claim_id}", "claim_id")
