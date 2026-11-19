"""Proposal 渲染：把黑板状态写成 11 章结构化文档。

章节与必填内容依据文档第五部分 1。第 11 章不可省略：未解决问题、
少数意见和立场变化必须如实写出，不能用“全体一致通过”覆盖。
"""

from __future__ import annotations

from typing import Any

from .blackboard import Blackboard
from .contracts import (CONSENSUS_LABEL, EVIDENCE_LABEL, SEVERITY_LABEL,
                        STANCE_LABEL)
from .estimate import pert


def cell(text: Any) -> str:
    """表格单元格转义：竖线与换行会破坏 Markdown 表格。"""
    value = str(text).replace("|", "／").replace("\n", " ")
    return value.strip()


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "（无）\n"
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(cell(item) for item in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def _bullets(items: list[str], empty: str = "（无）") -> str:
    if not items:
        return empty + "\n"
    return "\n".join(f"- {item}" for item in items) + "\n"


def render_proposal(board: Blackboard, *, run_id: str, created_at: str,
                    agent_name: str) -> str:
    snapshot = board.snapshot()
    estimate = pert(snapshot["work_packages"])

    parts: list[str] = [
        f"# 项目提案：{snapshot['idea'][:40]}",
        "",
        f"> 运行编号：`{run_id}` ｜ 生成时间：{created_at} ｜ 角色实现：{agent_name} ｜ "
        f"黑板版本：v{snapshot['version']}",
        ">",
        "> 本文由多智能体讨论生成，未确认前不构成承诺。涉及事实的结论均标注了依据类型，"
        "标注为「待验证假设」的内容需在开发前核实。",
        "",
        "## 1 背景与问题",
        "",
        snapshot["narrative"]["background"],
        "",
        "**原始想法**",
        "",
        "> " + snapshot["idea"].replace("\n", "\n> "),
        "",
    ]
    if snapshot["constraints"]:
        parts += ["**已知约束**", "", _bullets(snapshot["constraints"]), ""]
    parts += ["**问题陈述**", "",
              "现有做法把流程分散在即时通讯与线下沟通中，缺少统一的记录与验收口径；"
              "本期要回答的是「这条流程是否值得做成产品」，而不是一次性做全。",
              ""]

    parts += ["## 2 目标用户", "", snapshot["narrative"]["users"], ""]
    user_assumptions = [row for row in snapshot["assumptions"]
                        if row["role"] in ("ba", "user")]
    if user_assumptions:
        parts += [_table(["编号", "假设", "验证方式", "状态"],
                         [[row["id"], row["text"], row["verify_by"], row["status"]]
                          for row in user_assumptions])]

    parts += ["## 3 价值主张", "", snapshot["narrative"]["value"], ""]

    parts += ["## 4 项目范围", "", "### 4.1 候选方案对比", ""]
    parts += [_table(
        ["方案", "范围", "周期（天）", "风险", "取舍"],
        [[f"{row['id']} {row['name']}", "；".join(row["scope"]), row["cost_days"],
          row["risk"], row["tradeoffs"]] for row in snapshot["options"]])]
    parts += ["### 4.2 已定范围", ""]
    parts += [_table(["议题", "结论", "理由"],
                     [[row["scope"], row["choice"], row["reason"]]
                      for row in snapshot["decisions"]])]
    parts += ["### 4.3 明确不做（非目标）", ""]
    parts += [_table(["内容", "不做的理由"],
                     [[row["item"], row["reason"]] for row in snapshot["non_goals"]])]

    baseline = next((row for row in snapshot["options"] if row["key"] == "A"), None)
    parts += ["## 5 核心功能", ""]
    if baseline:
        parts += [f"首版以 **{baseline['id']} {baseline['name']}** 为基线，功能清单如下：", "",
                  _bullets([f"P0 {item}" for item in baseline["scope"]]), ""]
    extras = [row for row in snapshot["options"] if row["key"] != "A"]
    if extras:
        parts += ["候选增量（需单独确认后再排期）：", ""]
        parts += [_table(["方案", "增量功能", "前置条件"],
                         [[f"{row['id']} {row['name']}", "；".join(row["scope"][1:] or row["scope"]),
                           "供给或运营能力就绪"] for row in extras])]

    tech_claims = [row for row in snapshot["claims"] if row["role"] == "tech"]
    parts += ["## 6 技术方案概要", "", snapshot["narrative"]["tech"], ""]
    if tech_claims:
        parts += [_table(["论点", "理由", "依据"],
                         [[f"{row['id']} {row['text']}", row["reason"],
                           EVIDENCE_LABEL[row["evidence"]]] for row in tech_claims])]

    parts += ["## 7 商业／运营思路", "", snapshot["narrative"]["ops"], "",
              "> 本章全部为待验证假设，首版不据此投入资源。", ""]

    parts += ["## 8 成功指标", "", snapshot["narrative"]["metrics"], ""]
    metric_decisions = [row for row in snapshot["decisions"] if "指标" in row["scope"]]
    if metric_decisions:
        parts.append(_bullets([f"{row['choice']}（{row['reason']}）" for row in metric_decisions]))

    parts += ["## 9 风险与关键假设", "", "### 9.1 主要风险", ""]
    risk_claims = [row for row in snapshot["claims"]
                   if row["role"] == "risk" or row["evidence"] == "assumption"]
    parts += [_table(["风险", "说明", "依据"],
                     [[f"{row['id']} {row['text']}", row["reason"],
                       EVIDENCE_LABEL[row["evidence"]]] for row in risk_claims])]
    parts += ["### 9.2 关键假设", ""]
    parts += [_table(["编号", "假设", "验证方式", "状态", "提出者"],
                     [[row["id"], row["text"], row["verify_by"], row["status"], row["role"]]
                      for row in snapshot["assumptions"]])]

    parts += ["## 10 里程碑与估算", "",
              "采用三点估算（PERT）：E =（O + 4M + P）/6，总标准差取各方差和的平方根。", ""]
    parts += [_table(["工作包", "交付物", "O", "M", "P", "期望 E", "标准差"],
                     [[row["name"], row["deliverable"], row["optimistic"], row["likely"],
                       row["pessimistic"], row["expected"], row["deviation"]]
                      for row in estimate["rows"]])]
    parts += [
        f"- 总期望工期：**{estimate['expected_total']} 人天**，总标准差 {estimate['deviation_total']} 人天",
        f"- 对外承诺（E + 1σ）：**{estimate['commit_days']} 人天**",
        f"- 里程碑前预留缓冲：{estimate['buffer_low']}—{estimate['buffer_high']} 人天",
        "- 估算基于当前范围；范围变更须重新估算并保留旧基线。",
        "",
        "**里程碑**",
        "",
        _table(["阶段", "包含工作包", "完成标志"],
               [["M1 可演示", "、".join(row["name"] for row in estimate["rows"][:3]),
                 "核心流程可跑通并留痕"],
                ["M2 可验收", "、".join(row["name"] for row in estimate["rows"][3:]),
                 "验收项通过，遗留问题登记"]]),
    ]

    # 第 11 章：不可省略
    open_questions = [row for row in snapshot["open_questions"] if not row["resolution"]]
    resolved_questions = [row for row in snapshot["open_questions"] if row["resolution"]]
    open_objections = [row for row in snapshot["objections"] if row["status"] == "open"]
    resolved_objections = [row for row in snapshot["objections"] if row["status"] != "open"]
    stance_changes = snapshot.get("stance_changes", [])

    parts += ["## 11 未决问题与分歧记录", "",
              f"**共识状态：{CONSENSUS_LABEL[snapshot['consensus']['state']]}**"
              f"（{snapshot['consensus']['reason']}）", ""]
    parts += ["### 11.1 未解决问题", ""]
    parts += [_table(["编号", "问题", "提出者", "是否阻断", "处理人"],
                     [[row["id"], row["text"], row["source"],
                       "是" if row["blocking"] else "否", row["owner"]]
                      for row in open_questions])]
    if resolved_questions:
        parts += ["### 11.2 已有人工答复", ""]
        parts += [_table(["编号", "问题", "答复"],
                         [[row["id"], row["text"], row["resolution"]]
                          for row in resolved_questions])]
    parts += ["### 11.3 未采纳意见与未解决异议", ""]
    parts += [_table(["编号", "提出者", "针对", "异议", "后果", "级别"],
                     [[row["id"], row["role"], row["target"], row["text"],
                       row["consequence"], SEVERITY_LABEL[row["severity"]]]
                      for row in open_objections])]
    if resolved_objections:
        parts += ["### 11.4 已处理的异议", ""]
        parts += [_table(["编号", "异议", "处理结果"],
                         [[row["id"], row["text"], row.get("resolution", "已关闭")]
                          for row in resolved_objections])]
    parts += ["### 11.5 立场汇总", ""]
    parts += [_table(["方案", *[role for role in snapshot["stances"]], "统计"],
                     _stance_rows(snapshot))]
    if stance_changes:
        parts += ["### 11.6 立场变化", ""]
        parts.append(_bullets([f"{row['role']}：{STANCE_LABEL[row['from']]} → "
                               f"{STANCE_LABEL[row['to']]}（{row['why']}）"
                               for row in stance_changes]))
    parts += ["> 本章保留少数意见与未解决异议，不因未达成共识而删除。"
              "阻断异议解除前，方案不应标记为通过。", ""]

    parts += ["---", "", "## 附录 A 论点索引", ""]
    parts += [_table(["编号", "角色", "主张", "依据", "依赖假设"],
                     [[row["id"], row["role"], row["text"],
                       EVIDENCE_LABEL[row["evidence"]], "、".join(row["assumes"]) or "—"]
                      for row in snapshot["claims"]])]
    parts += ["## 附录 B 事件流", "",
              "完整事件见同目录 `events.jsonl`，一行一个事件，`seq` 连续递增。", ""]

    return "\n".join(parts).rstrip() + "\n"


def _stance_rows(snapshot: dict[str, Any]) -> list[list[str]]:
    roles = list(snapshot["stances"].keys())
    rows: list[list[str]] = []
    for option in snapshot["options"]:
        values = [STANCE_LABEL[snapshot["stances"].get(role, {}).get(option["id"], "unreviewed")]
                  if role in snapshot["stances"] else "未评审" for role in roles]
        agree = sum(1 for item in values if item == "赞同")
        rows.append([f"{option['id']} {option['name']}", *values,
                     f"赞同 {agree}／{len(values)}"])
    return rows
