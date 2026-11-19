"""阶段编排：想法 → 澄清 → 独立发散 → 结构化批判 → 整合 → 签署 → Proposal。

分工遵循文档第一部分 1.3：阶段路由、预算与状态由代码控制，模型只负责
生成阶段内容；任何模型输出都要经过契约校验才能写入黑板。
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .agents import Agent, MockAgent, isolated_context
from .blackboard import Blackboard, EventLog, now_iso
from .contracts import ContractError, validate_stances, validate_synthesis
from .proposal import render_proposal
from .roles import DISCUSSION_ROLES, SYNTHESIZER, role_card

Progress = Callable[[str], None]


@dataclass
class RunResult:
    run_id: str
    out_dir: Path
    board: Blackboard
    events: EventLog
    proposal_path: Path
    report: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return self.report


def slugify(text: str, limit: int = 18) -> str:
    """生成文件名用的短标识，保留中文，去掉路径非法字符。"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text, flags=re.UNICODE).strip("-")
    return (cleaned[:limit] or "idea").strip("-")


def _claims_view(board: Blackboard) -> list[dict[str, str]]:
    """给讨论角色看的论点视图：只有编号、角色和文本，不含内部字段。"""
    return [{"id": row["id"], "role": row["role"], "text": row["text"]}
            for row in board.claims]


def run(idea: str, *, constraints: list[str] | None = None,
        out_dir: str | Path = "runs", agent: Agent | None = None,
        answers: dict[str, str] | None = None,
        resolutions: dict[str, str] | None = None,
        progress: Progress | None = None) -> RunResult:
    if not idea.strip():
        raise ContractError("EMPTY_IDEA", "想法不能为空", "idea")

    agent = agent or MockAgent()
    say = progress or (lambda message: None)
    created_at = now_iso()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = f"{stamp}-{slugify(idea)}"
    target = Path(out_dir) / run_id
    suffix = 2
    while target.exists():  # 同一秒内多次运行也不覆盖旧结果
        target = Path(out_dir) / f"{run_id}-{suffix}"
        suffix += 1
    run_id = target.name
    target.mkdir(parents=True, exist_ok=False)

    board = Blackboard(idea, constraints)
    events = EventLog()
    events.emit("system", "run_created",
                f"实例创建，角色实现 {agent.name}，约束 {len(board.constraints)} 条",
                object_type="run", object_id=run_id, version=board.version)

    # ---------- 阶段 1：首轮澄清 ----------
    board.stage = "clarify"
    say("阶段 1/6 首轮澄清：需求分析角色提出只能由人回答的阻断问题")
    clarify = agent.respond("clarify", role_card("ba"), isolated_context(idea, board.constraints))
    before = len(board.open_questions)
    board.add_role_output(clarify, role_id="ba", stage="clarify")
    blocking = board.open_questions[before:]
    for row in blocking:
        row["blocking"] = True
    events.emit("ba", "clarify_questions",
                f"提出 {len(blocking)} 个阻断问题", object_type="blackboard",
                object_id=run_id, version=board.version)

    for row in blocking:
        answer = (answers or {}).get(row["id"], "").strip()
        if answer:
            board.resolve_question(row["id"], answer, owner="human")
            board.add_decision(scope=f"澄清 {row['id']}", choice=answer,
                               reason="人工答复", decider="human")
            events.emit("human", "question_answered", f"{row['id']}：{answer}",
                        object_type="question", object_id=row["id"], version=board.version)
        else:
            board.resolve_question(row["id"], "未知，按假设继续", owner="human:assumed")
            board.add_decision(scope=f"澄清 {row['id']}", choice="未知，按假设继续",
                               reason="未答复，按文档约定标记为假设继续",
                               decider="human:assumed")
            events.emit("human:assumed", "question_skipped",
                        f"{row['id']} 未答复，按假设继续", object_type="question",
                        object_id=row["id"], version=board.version)
    board.checkpoint("clarify")

    # ---------- 阶段 2：独立发散（互不可见） ----------
    board.stage = "diverge"
    board.round = 1
    say("阶段 2/6 独立发散：6 个角色并行、互不可见")
    for role in DISCUSSION_ROLES:
        # 每次都给一份全新上下文，只含原始输入，杜绝互相污染
        raw = agent.respond("diverge", role, deepcopy(isolated_context(idea, board.constraints)))
        output = board.add_role_output(raw, role_id=role.id, stage="diverge")
        events.emit(role.id, "claims_written",
                    f"{role.name}：{len(output['claim_ids'])} 条主张、"
                    f"{len(output['assumption_ids'])} 条假设",
                    object_type="claim", object_id=",".join(output["claim_ids"]) or "—",
                    version=board.version)
        say(f"  · {role.name}：{len(output['claim_ids'])} 条主张")
    board.checkpoint("diverge")

    # ---------- 阶段 3：结构化批判 ----------
    board.stage = "critique"
    board.round = 2
    say("阶段 3/6 结构化批判：必须引用论点编号并表态")
    view = _claims_view(board)
    for role in DISCUSSION_ROLES:
        raw = agent.respond("critique", role,
                            deepcopy({"idea": idea, "constraints": board.constraints,
                                      "claims": view}))
        output = board.add_role_output(raw, role_id=role.id, stage="critique")
        events.emit(role.id, "objections_written",
                    f"{role.name}：{len(output['objections'])} 条异议",
                    object_type="objection", object_id=run_id, version=board.version)
        say(f"  · {role.name}：{len(output['objections'])} 条异议")
    board.checkpoint("critique")

    # ---------- 阶段 4：整合 ----------
    board.stage = "synthesize"
    board.round = 3
    say("阶段 4/6 方案整合：保留来源与少数意见，生成候选方案与估算")
    raw = agent.respond("synthesize", SYNTHESIZER,
                        deepcopy({"idea": idea, "constraints": board.constraints,
                                  "claims": view}))
    synthesis = validate_synthesis(raw, known_claims={row["id"] for row in board.claims})
    for row in synthesis["options"]:
        board.add_option(**row)
    for row in synthesis["decisions"]:
        board.add_decision(**row, decider="AI 建议")
    for row in synthesis["non_goals"]:
        board.add_non_goal(row["item"], row["reason"])
    board.narrative = synthesis["narrative"]
    board.work_packages = synthesis["work_packages"]
    for text in synthesis["open_questions"]:
        board.add_question(text, source="synthesizer")
    events.emit("synthesizer", "options_created",
                f"生成 {len(synthesis['options'])} 个候选方案、"
                f"{len(synthesis['work_packages'])} 个工作包",
                object_type="option", object_id=run_id, version=board.version)

    # ---------- 阶段 5：立场签署 ----------
    board.stage = "sign"
    say("阶段 5/6 立场签署：赞同／有条件赞同／反对／未评审")
    option_view = [{"id": row["id"], "key": row["key"], "name": row["name"]}
                   for row in board.options]
    for role in DISCUSSION_ROLES:
        raw = agent.respond("sign", role,
                            deepcopy({"idea": idea, "options": option_view}))
        signed = validate_stances(raw, role_id=role.id,
                                 known_options={row["id"] for row in board.options})
        for item in signed["stances"]:
            board.record_stance(role.id, item["option"], item["stance"],
                                reason=item["reason"], condition=item["condition"])
        events.emit(role.id, "stances_signed",
                    f"{role.name} 签署 {len(signed['stances'])} 个方案",
                    object_type="stance", object_id=run_id, version=board.version)
    board.checkpoint("sign")

    # ---------- 人工裁决（可选） ----------
    for objection_id, decision in (resolutions or {}).items():
        board.resolve_objection(objection_id, decision)
        events.emit("human", "objection_resolved",
                    f"{objection_id} 由人工裁决：{decision}",
                    object_type="objection", object_id=objection_id,
                    version=board.version)

    # ---------- 阶段 6：收敛判定与 Proposal ----------
    board.stage = "proposal"
    state = board.consensus_state()
    events.emit("host", "consensus_decided",
                f"共识状态：{state}（{board.consensus_reason()}）",
                object_type="run", object_id=run_id, version=board.version)
    say(f"阶段 6/6 收敛判定：{state} —— {board.consensus_reason()}")

    proposal = render_proposal(board, run_id=run_id, created_at=created_at,
                               agent_name=agent.name)
    proposal_path = target / "proposal.md"
    proposal_path.write_text(proposal, encoding="utf-8")
    (target / "blackboard.json").write_text(
        json.dumps(board.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "events.jsonl").write_text(events.to_jsonl() + "\n", encoding="utf-8")

    report = {
        "run_id": run_id,
        "out_dir": str(target),
        "proposal": str(proposal_path),
        "counts": {
            "claims": len(board.claims),
            "assumptions": len(board.assumptions),
            "objections": len(board.objections),
            "blocking": len(board.unresolved_blocking()),
            "options": len(board.options),
            "open_questions": len([row for row in board.open_questions
                                   if not row["resolution"]]),
        },
        "consensus": board.snapshot()["consensus"],
    }
    (target / "run.json").write_text(
        json.dumps({**report, "idea": idea, "constraints": board.constraints,
                    "created_at": created_at, "agent": agent.name},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return RunResult(run_id=run_id, out_dir=target, board=board, events=events,
                     proposal_path=proposal_path, report=report)
