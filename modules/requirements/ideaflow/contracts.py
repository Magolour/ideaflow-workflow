"""领域契约：编号规则、结构校验与错误类型。

对应《IdeaFlow 多智能体人机协同工作流设计方案 V2 修订版》：

- 第二部分 2.4 关键意见记录：依据类型只有四类，不能含糊。
- 第二部分 3.2 立场与签署：四态立场，未评审不等于支持。
- 第四部分 2 核心数据对象：黑板字段与统一输出结构。

本模块只做确定性校验，不调用模型。任何 Agent 输出都要先过这里，
不合格的输入不进入黑板。
"""

from __future__ import annotations

from typing import Any, Iterable

# 立场四态（文档 3.2）
STANCES = ("agree", "conditional", "oppose", "unreviewed")
STANCE_LABEL = {
    "agree": "赞同",
    "conditional": "有条件赞同",
    "oppose": "反对",
    "unreviewed": "未评审",
}

# 依据类型（文档 2.4）：材料事实 / 规则推演 / 价值偏好 / 待验证假设
EVIDENCE_TYPES = ("fact", "rule", "value", "assumption")
EVIDENCE_LABEL = {
    "fact": "材料事实",
    "rule": "规则推演",
    "value": "价值偏好",
    "assumption": "待验证假设",
}

# 分歧类型（文档 3.1）
DIVERGENCE_TYPES = ("fact", "value", "tradeoff")
DIVERGENCE_LABEL = {
    "fact": "事实性分歧",
    "value": "价值取向分歧",
    "tradeoff": "权衡性分歧",
}

# 共识状态（文档 3.3）
CONSENSUS_STATES = ("consensus", "conditional", "pending_verification", "no_consensus")
CONSENSUS_LABEL = {
    "consensus": "共识通过",
    "conditional": "有条件通过",
    "pending_verification": "待验证",
    "no_consensus": "未达成共识",
}

SEVERITY = ("blocking", "major", "minor")
SEVERITY_LABEL = {"blocking": "阻断", "major": "重要", "minor": "一般"}

# 对异议的回应（文档第二部分 3.2 的“表态”，收敛阶段使用）
RESPONSE_POSITIONS = ("accept", "refute", "conditional")
RESPONSE_LABEL = {
    "accept": "接受并修订",
    "refute": "反驳",
    "conditional": "有条件接受",
}

# 整合复核结论（文档第二部分 3.4）
REVIEW_VERDICTS = ("preserved", "deferred", "omitted", "distorted")
REVIEW_LABEL = {
    "preserved": "已保留",
    "deferred": "已登记延期",
    "omitted": "被遗漏",
    "distorted": "被歪曲",
}

STAGES = ("intake", "clarify", "diverge", "critique", "synthesize", "proposal")


class ContractError(ValueError):
    """结构化错误：错误码 + 可定位路径，供接口和前端直接展示。"""

    def __init__(self, code: str, message: str, path: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path

    def as_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message,
                          "details": {"path": self.path}}}


def new_id(prefix: str, rows: Iterable[dict], width: int = 3) -> str:
    """按前缀分配下一个编号，例如 CLM-001。编号由代码分配，模型不参与。"""
    used = {str(row.get("id")) for row in rows}
    number = 1
    while f"{prefix}-{number:0{width}d}" in used:
        number += 1
    return f"{prefix}-{number:0{width}d}"


def _text(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ContractError("INVALID_FIELD", "必须是文本", path)
    text = value.strip()
    if not text and not allow_empty:
        raise ContractError("INVALID_FIELD", "不能为空", path)
    return text


def _enum(value: Any, allowed: tuple[str, ...], path: str) -> str:
    text = _text(value, path)
    if text not in allowed:
        raise ContractError("INVALID_ENUM", f"取值必须是 {'/'.join(allowed)} 之一", path)
    return text


def _rows(value: Any, path: str, *, nonempty: bool = False) -> list:
    if not isinstance(value, list):
        raise ContractError("INVALID_FIELD", "必须是数组", path)
    if nonempty and not value:
        raise ContractError("INVALID_FIELD", "不能是空数组", path)
    return value


def validate_role_output(payload: Any, *, role_id: str, stage: str,
                         known_claims: set[str] | None = None,
                         known_objections: set[str] | None = None) -> dict[str, Any]:
    """校验并归一化单角色输出（文档第四部分 2 的统一输出结构）。

    校验失败抛 ContractError，由调用方决定回注重试还是转人工。
    """
    if not isinstance(payload, dict):
        raise ContractError("INVALID_OUTPUT", "角色输出必须是对象", "")

    if payload.get("role") != role_id:
        raise ContractError("ROLE_MISMATCH", "输出角色与请求角色不一致", "role")

    claims_out: list[dict[str, Any]] = []
    for index, row in enumerate(_rows(payload.get("claims", []), "claims")):
        path = f"claims[{index}]"
        if not isinstance(row, dict):
            raise ContractError("INVALID_FIELD", "主张必须是对象", path)
        evidence = _enum(row.get("evidence"), EVIDENCE_TYPES, f"{path}.evidence")
        assumed_raw = _rows(row.get("assumes", []), f"{path}.assumes")
        assumed: list[int] = []
        for order, item in enumerate(assumed_raw):
            if type(item) is not int or item < 0:
                raise ContractError("INVALID_FIELD", "assumes 填写本轮假设的序号（从 0 开始）",
                                    f"{path}.assumes[{order}]")
            assumed.append(item)
        claims_out.append({
            "text": _text(row.get("text"), f"{path}.text"),
            "reason": _text(row.get("reason"), f"{path}.reason"),
            "evidence": evidence,
            "assumes": assumed,
        })
        refs = [str(item).strip() for item in (row.get("requirement_ids") or [])
                if str(item).strip()]
        if refs:
            claims_out[-1]["requirement_ids"] = list(dict.fromkeys(refs))

    assumptions_out: list[dict[str, str]] = []
    for index, row in enumerate(_rows(payload.get("assumptions", []), "assumptions")):
        path = f"assumptions[{index}]"
        if not isinstance(row, dict):
            raise ContractError("INVALID_FIELD", "假设必须是对象", path)
        assumptions_out.append({
            "text": _text(row.get("text"), f"{path}.text"),
            "verify_by": _text(row.get("verify_by"), f"{path}.verify_by"),
        })

    objections_out: list[dict[str, Any]] = []
    if stage == "critique":
        objections = _rows(payload.get("objections", []), "objections")
        known = known_claims or set()
        for index, row in enumerate(objections):
            path = f"objections[{index}]"
            if not isinstance(row, dict):
                raise ContractError("INVALID_FIELD", "异议必须是对象", path)
            target = _text(row.get("target"), f"{path}.target")
            if target not in known:
                raise ContractError("UNKNOWN_CLAIM", f"引用了不存在的论点 {target}", f"{path}.target")
            objections_out.append({
                "target": target,
                "text": _text(row.get("text"), f"{path}.text"),
                "consequence": _text(row.get("consequence"), f"{path}.consequence"),
                "severity": _enum(row.get("severity"), SEVERITY, f"{path}.severity"),
            })

    questions = [_text(item, f"questions[{index}]")
                 for index, item in enumerate(_rows(payload.get("questions", []), "questions"))]

    responses_out: list[dict[str, str]] = []
    if stage == "respond":
        known = known_objections or set()
        for index, row in enumerate(_rows(payload.get("responses"), "responses", nonempty=True)):
            path = f"responses[{index}]"
            if not isinstance(row, dict):
                raise ContractError("INVALID_FIELD", "回应必须是对象", path)
            objection_id = _text(row.get("objection"), f"{path}.objection")
            if objection_id not in known:
                raise ContractError("UNKNOWN_OBJECTION",
                                    f"回应了不存在的异议 {objection_id}",
                                    f"{path}.objection")
            position = _enum(row.get("position"), RESPONSE_POSITIONS, f"{path}.position")
            evidence = row.get("evidence", "rule")
            responses_out.append({
                "objection": objection_id,
                "position": position,
                "argument": _text(row.get("argument"), f"{path}.argument"),
                "evidence": _enum(evidence, EVIDENCE_TYPES, f"{path}.evidence"),
            })

    reviews_out: list[dict[str, str]] = []
    if stage == "review":
        known = known_claims or set()
        for index, row in enumerate(_rows(payload.get("reviews"), "reviews", nonempty=True)):
            path = f"reviews[{index}]"
            if not isinstance(row, dict):
                raise ContractError("INVALID_FIELD", "复核记录必须是对象", path)
            claim_id = _text(row.get("claim"), f"{path}.claim")
            if claim_id not in known:
                raise ContractError("UNKNOWN_CLAIM", f"复核了不存在的论点 {claim_id}",
                                    f"{path}.claim")
            reviews_out.append({
                "claim": claim_id,
                "verdict": _enum(row.get("verdict"), REVIEW_VERDICTS, f"{path}.verdict"),
                "note": _text(row.get("note"), f"{path}.note"),
            })

    position_changed = payload.get("position_changed")
    if position_changed is not None:
        if not isinstance(position_changed, dict):
            raise ContractError("INVALID_FIELD", "立场变化必须是对象", "position_changed")
        position_changed = {
            "from": _enum(position_changed.get("from"), STANCES, "position_changed.from"),
            "to": _enum(position_changed.get("to"), STANCES, "position_changed.to"),
            "why": _text(position_changed.get("why"), "position_changed.why"),
        }
        if position_changed["from"] == position_changed["to"]:
            raise ContractError("INVALID_FIELD", "立场变化前后不能相同", "position_changed.to")

    if not claims_out and not objections_out and not questions and not responses_out and not reviews_out:
        raise ContractError("EMPTY_OUTPUT", "本轮没有任何主张、异议、回应或复核", "")

    return {
        "role": role_id,
        "claims": claims_out,
        "assumptions": assumptions_out,
        "objections": objections_out,
        "responses": responses_out,
        "reviews": reviews_out,
        "questions": questions,
        "position_changed": position_changed,
    }


def validate_stances(payload: Any, *, role_id: str,
                     known_options: set[str]) -> dict[str, Any]:
    """校验方案签署：四态立场，有条件必须写条件，反对必须写理由。"""
    if not isinstance(payload, dict):
        raise ContractError("INVALID_OUTPUT", "签署输出必须是对象", "")
    if payload.get("role") != role_id:
        raise ContractError("ROLE_MISMATCH", "输出角色与请求角色不一致", "role")

    rows = _rows(payload.get("stances"), "stances", nonempty=True)
    stances: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        path = f"stances[{index}]"
        if not isinstance(row, dict):
            raise ContractError("INVALID_FIELD", "立场必须是对象", path)
        option = _text(row.get("option"), f"{path}.option")
        if option not in known_options:
            raise ContractError("UNKNOWN_OPTION", f"不存在方案 {option}", f"{path}.option")
        if option in seen:
            raise ContractError("DUPLICATE", "同一方案只能签署一次", f"{path}.option")
        seen.add(option)
        stance = _enum(row.get("stance"), STANCES, f"{path}.stance")
        reason = _text(row.get("reason", ""), f"{path}.reason", allow_empty=True)
        condition = _text(row.get("condition", ""), f"{path}.condition", allow_empty=True)
        if stance == "conditional" and not condition:
            raise ContractError("MISSING_CONDITION", "有条件赞同必须写明条件", f"{path}.condition")
        if stance == "oppose" and not reason:
            raise ContractError("MISSING_REASON", "反对必须写明理由", f"{path}.reason")
        stances.append({"option": option, "stance": stance,
                        "reason": reason, "condition": condition})
    return {"role": role_id, "stances": stances}


def validate_synthesis(payload: Any, *, known_claims: set[str]) -> dict[str, Any]:
    """校验整合输出：候选方案、决策、非目标、叙述与估算明细。"""
    if not isinstance(payload, dict):
        raise ContractError("INVALID_OUTPUT", "整合输出必须是对象", "")

    options: list[dict[str, Any]] = []
    for index, row in enumerate(_rows(payload.get("options"), "options", nonempty=True)):
        path = f"options[{index}]"
        if not isinstance(row, dict):
            raise ContractError("INVALID_FIELD", "候选方案必须是对象", path)
        scope = [_text(item, f"{path}.scope[{i}]")
                 for i, item in enumerate(_rows(row.get("scope"), f"{path}.scope", nonempty=True))]
        based_on = [str(item) for item in row.get("based_on", [])]
        unknown = [item for item in based_on if item not in known_claims]
        if unknown:
            raise ContractError("UNKNOWN_CLAIM", f"方案引用了不存在的论点 {unknown[0]}",
                                f"{path}.based_on")
        options.append({
            "key": _text(row.get("key"), f"{path}.key"),
            "name": _text(row.get("name"), f"{path}.name"),
            "scope": scope,
            "cost_days": _text(row.get("cost_days"), f"{path}.cost_days"),
            "risk": _text(row.get("risk"), f"{path}.risk"),
            "tradeoffs": _text(row.get("tradeoffs"), f"{path}.tradeoffs"),
            "based_on": based_on,
        })
    if len(options) < 2:
        raise ContractError("INVALID_OUTPUT", "候选方案至少两份", "options")

    decisions: list[dict[str, Any]] = []
    for index, row in enumerate(_rows(payload.get("decisions", []), "decisions")):
        path = f"decisions[{index}]"
        if not isinstance(row, dict):
            raise ContractError("INVALID_FIELD", "决策必须是对象", path)
        based_on = [str(item) for item in row.get("based_on", [])]
        unknown = [item for item in based_on if item not in known_claims]
        if unknown:
            raise ContractError("UNKNOWN_CLAIM", f"决策引用了不存在的论点 {unknown[0]}",
                                f"{path}.based_on")
        decisions.append({
            "scope": _text(row.get("scope"), f"{path}.scope"),
            "choice": _text(row.get("choice"), f"{path}.choice"),
            "reason": _text(row.get("reason"), f"{path}.reason"),
            "based_on": based_on,
        })

    not_adopted: list[dict[str, str]] = []
    for index, row in enumerate(_rows(payload.get("not_adopted", []), "not_adopted")):
        path = f"not_adopted[{index}]"
        if not isinstance(row, dict):
            raise ContractError("INVALID_FIELD", "未采纳记录必须是对象", path)
        claim_id = _text(row.get("claim"), f"{path}.claim")
        if claim_id not in known_claims:
            raise ContractError("UNKNOWN_CLAIM", f"未采纳记录引用了不存在的论点 {claim_id}",
                                f"{path}.claim")
        not_adopted.append({
            "claim": claim_id,
            "reason": _text(row.get("reason"), f"{path}.reason"),
        })

    non_goals: list[dict[str, str]] = []
    for index, row in enumerate(_rows(payload.get("non_goals", []), "non_goals")):
        path = f"non_goals[{index}]"
        if not isinstance(row, dict):
            raise ContractError("INVALID_FIELD", "非目标必须是对象", path)
        non_goals.append({
            "item": _text(row.get("item"), f"{path}.item"),
            "reason": _text(row.get("reason"), f"{path}.reason"),
        })

    narrative_raw = payload.get("narrative")
    if not isinstance(narrative_raw, dict):
        raise ContractError("INVALID_FIELD", "叙述必须是对象", "narrative")
    narrative = {key: _text(narrative_raw.get(key), f"narrative.{key}")
                 for key in ("background", "users", "value", "tech", "ops", "metrics")}

    packages: list[dict[str, Any]] = []
    for index, row in enumerate(_rows(payload.get("work_packages"), "work_packages", nonempty=True)):
        path = f"work_packages[{index}]"
        if not isinstance(row, dict):
            raise ContractError("INVALID_FIELD", "工作包必须是对象", path)
        try:
            optimistic = float(row.get("optimistic"))
            likely = float(row.get("likely"))
            pessimistic = float(row.get("pessimistic"))
        except (TypeError, ValueError):
            raise ContractError("INVALID_FIELD", "三点估算必须是数字", path) from None
        if not (0 < optimistic <= likely <= pessimistic):
            raise ContractError("INVALID_FIELD", "必须满足 0 < 乐观 ≤ 最可能 ≤ 悲观", path)
        packages.append({
            "name": _text(row.get("name"), f"{path}.name"),
            "deliverable": _text(row.get("deliverable"), f"{path}.deliverable"),
            "optimistic": optimistic,
            "likely": likely,
            "pessimistic": pessimistic,
        })

    questions = [_text(item, f"open_questions[{index}]")
                 for index, item in enumerate(_rows(payload.get("open_questions", []), "open_questions"))]

    return {"options": options, "decisions": decisions, "not_adopted": not_adopted,
            "non_goals": non_goals, "narrative": narrative,
            "work_packages": packages, "open_questions": questions}
