"""收敛引擎：分歧分类、支持度、终止条件、关键意见处理与伪共识检测。

设计依据：文档第二部分 2.3、2.4、3.1—3.4。

这里全部是确定性判断：分类可以被人覆盖，共识不能由模型自行宣布。
比例只作辅助信号——存在未解决的高影响反对时，即使支持率很高也不能判通过。
"""

from __future__ import annotations

from typing import Any

from .blackboard import Blackboard
from .contracts import DIVERGENCE_LABEL

SUPPORT_WEIGHTS = {"agree": 1.0, "conditional": 0.5, "oppose": 0.0, "unreviewed": 0.0}

# 分类提示词是规则兜底，最终以人工覆盖为准
VALUE_HINTS = ("取舍", "优先级", "值得", "偏好", "宁可", "价值判断")
FACT_HINTS = ("证据", "数据", "未验证", "没有依据", "事实", "调研", "证实",
              "访谈", "供给", "用户量")

DISPOSITION_LABEL = {
    "adopted": "采纳",
    "rejected": "拒绝",
    "deferred": "延期验证",
    "pending_evidence": "待补证据",
    "human_required": "待人工裁决",
}

TERMINATION_LABEL = {
    "human_decision": "价值取舍待人工裁决",
    "max_rounds": "达到最大讨论轮数",
    "no_new_issues": "连续两轮无新增实质性异议",
    "insufficient_info": "事实缺口待验证",
    "consensus": "达成共识",
    "conditional": "有条件通过",
}


def classify_objection(board: Blackboard, objection: dict[str, Any]) -> str:
    """把异议归入事实／价值／权衡三类。

    先看措辞，再看被质疑论点本身的依据类型。分类结果允许人工覆盖，
    因为语义判断不能完全交给规则。
    """
    claim = board.claim_by_id(objection["target"])
    text = objection["text"] + objection.get("consequence", "")
    # 先看异议本身的措辞，再看被质疑论点的依据类型
    if any(word in text for word in VALUE_HINTS):
        return "value"
    if any(word in text for word in FACT_HINTS):
        return "fact"
    if claim["evidence"] == "value":
        return "value"
    if claim["evidence"] in ("fact", "assumption"):
        return "fact"
    return "tradeoff"


def divergence_map(board: Blackboard) -> dict[str, str]:
    return {row["id"]: classify_objection(board, row) for row in board.objections}


def option_support(board: Blackboard) -> list[dict[str, Any]]:
    """每个方案的加权支持度。未评审不计分，也不视为支持。"""
    rows: list[dict[str, Any]] = []
    for option in board.options:
        values = [board.stances.get(role, {}).get(option["id"], "unreviewed")
                  for role in board.stances]
        scored = [SUPPORT_WEIGHTS[value] for value in values]
        rows.append({
            "option": option["id"],
            "name": option["name"],
            "ratio": round(sum(scored) / len(scored), 2) if scored else 0.0,
            "agree": values.count("agree"),
            "conditional": values.count("conditional"),
            "oppose": values.count("oppose"),
            "unreviewed": values.count("unreviewed"),
        })
    return rows


def best_support(board: Blackboard) -> float:
    rows = option_support(board)
    return max((row["ratio"] for row in rows), default=0.0)


def evaluate_termination(board: Blackboard, *, max_rounds: int,
                         threshold: float = 0.7,
                         rounds_without_new: int = 0) -> dict[str, Any]:
    """判断讨论是否可以结束，以及为什么结束。

    终止条件（任一满足即停）对应文档 3.3；顺序体现优先级：
    价值取舍必须交人 > 达到轮次上限 > 连续两轮无新增 > 事实缺口 > 达成共识。
    """
    open_rows = board.open_objections()
    blocking = board.unresolved_blocking()
    value_open = [row for row in open_rows if board.divergence.get(row["id"]) == "value"]
    fact_open = [row for row in open_rows if board.divergence.get(row["id"]) == "fact"]
    support = best_support(board)
    # 发散是第 1 轮，但不算讨论轮次；讨论轮次从批判轮开始计
    discussion_round = max(board.round - 1, 1)
    base = {"round": discussion_round, "support": support,
            "blocking": len(blocking), "value_open": len(value_open),
            "fact_open": len(fact_open), "max_rounds": max_rounds}

    if value_open:
        return {**base, "stop": True, "reason": "human_decision",
                "label": f"存在价值取向分歧，需人工裁决（{value_open[0]['id']}）"}
    if blocking and discussion_round >= max_rounds:
        return {**base, "stop": True, "reason": "max_rounds",
                "label": f"达到最大讨论轮数仍有 {len(blocking)} 条阻断异议"}
    if rounds_without_new >= 2:
        return {**base, "stop": True, "reason": "no_new_issues",
                "label": "连续两轮没有新增实质性异议"}
    if blocking:
        return {**base, "stop": False, "reason": "continue",
                "label": f"仍有 {len(blocking)} 条阻断异议待回应"}
    if fact_open:
        return {**base, "stop": True, "reason": "insufficient_info",
                "label": f"存在 {len(fact_open)} 条事实性异议缺少证据"}
    if support >= threshold:
        return {**base, "stop": True, "reason": "consensus",
                "label": f"无阻断项且最高支持度 {support:.0%} 达到阈值"}
    return {**base, "stop": True, "reason": "conditional",
            "label": f"无阻断项，但支持度 {support:.0%} 低于阈值，按有条件通过处理"}


def consensus_from(board: Blackboard, termination: dict[str, Any]) -> dict[str, str]:
    """把终止原因映射为共识状态。绝不把“停止讨论”写成“达成共识”。"""
    reason = termination["reason"]
    if reason == "human_decision":
        return {"state": "no_consensus",
                "reason": "存在需要真人决定的价值取舍，Agent 不代为选择"}
    if reason == "max_rounds":
        return {"state": "no_consensus", "reason": termination["label"]}
    if reason == "no_new_issues" and termination["blocking"]:
        return {"state": "no_consensus",
                "reason": "讨论不再产生新信息，但阻断异议仍未解决"}
    if reason == "insufficient_info":
        return {"state": "pending_verification",
                "reason": "关键事实缺少证据，需先验证或访谈后再定"}
    if reason == "conditional":
        return {"state": "conditional",
                "reason": "方向可接受，条件与前提必须写入确认书"}
    if board.pseudo_check and board.pseudo_check.get("triggered") and board.pseudo_check.get("raised"):
        return {"state": "conditional",
                "reason": "全票通过触发反例复核，反例尚未处理"}
    return {"state": "consensus", "reason": "无阻断项，立场齐备且支持度达到阈值"}


def key_opinions(board: Blackboard) -> list[dict[str, Any]]:
    """关键意见处理表（文档 2.4）：每条主张都有人回应、有处理状态和理由。"""
    adopted = {claim_id for row in board.decisions for claim_id in row.get("based_on", [])}
    rejected = {row["claim"] for row in board.not_adopted}
    rows: list[dict[str, Any]] = []
    for claim in board.claims:
        related = [row for row in board.objections if row["target"] == claim["id"]]
        open_rows = [row for row in related if row["status"] == "open"]
        if any(row["severity"] == "blocking" for row in open_rows):
            status, reason = "human_required", "存在未解决的阻断异议"
        elif any(board.divergence.get(row["id"]) == "fact" for row in open_rows):
            status, reason = "pending_evidence", "事实性异议缺少证据"
        elif claim["id"] in rejected:
            status, reason = "rejected", next(row["reason"] for row in board.not_adopted
                                              if row["claim"] == claim["id"])
        elif claim["id"] in adopted:
            status, reason = "adopted", "已写入决策"
        elif open_rows:
            status, reason = "deferred", "分歧未闭合，留在下一轮"
        else:
            status, reason = "deferred", "未进入首版范围"
        rows.append({
            "claim": claim["id"],
            "role": claim["role"],
            "text": claim["text"],
            "evidence": claim["evidence"],
            "responses": [f"{row['response']['role']}·{row['response']['position']}"
                          for row in related if row.get("response")],
            "open": len(open_rows),
            "status": status,
            "label": DISPOSITION_LABEL[status],
            "reason": reason,
            "impacts": [option["id"] for option in board.options
                        if claim["id"] in option.get("based_on", [])],
        })
    return rows


def integrity_issues(board: Blackboard) -> list[dict[str, Any]]:
    """整合复核中的实质性失真：被遗漏或歪曲且已进入方案的主张。

    文档 3.4：原角色没有否决权，但实质性失真按阻断项处理。
    """
    cited = {claim_id for row in board.decisions for claim_id in row.get("based_on", [])}
    for option in board.options:
        cited.update(option.get("based_on", []))
    return [row for row in board.reviews
            if row["verdict"] in ("omitted", "distorted") and row["claim"] in cited]


def needs_counterexample(board: Blackboard) -> bool:
    """伪共识检测：全部立场都是赞同或有条件赞同，且至少两人表过态。"""
    stances = [value for row in board.stances.values() for value in row.values()
               if value != "unreviewed"]
    return len(stances) >= 2 and all(value in ("agree", "conditional") for value in stances)


def evidence_gaps(board: Blackboard) -> list[dict[str, str]]:
    """事实性分歧产生的待调研事项（文档 3.1 的系统输出之一）。"""
    gaps = []
    for row in board.objections:
        if board.divergence.get(row["id"]) != "fact" or row["status"] != "open":
            continue
        gaps.append({
            "objection": row["id"],
            "type": DIVERGENCE_LABEL["fact"],
            "status": "待调研",
            "action": f"补充证据或访谈后再判定：{row['text']}",
        })
    return gaps
