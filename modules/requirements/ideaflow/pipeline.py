"""阶段编排：想法 → 澄清 → 发散 → 批判 → 回应 → 整合 → 签署 → 收敛 → Proposal。

分工遵循文档第一部分 1.3：阶段路由、轮次循环与终止判断由代码控制，
模型只负责生成阶段内容；任何模型输出都要经过契约校验才能写入黑板。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .agents import Agent, MockAgent, isolated_context
from .blackboard import Blackboard, EventLog, now_iso
from .contracts import ContractError, validate_stances, validate_synthesis
from .convergence import (consensus_from, divergence_map, evaluate_termination,
                          integrity_issues, key_opinions, needs_counterexample,
                          option_support)
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
    """给讨论角色看的论点视图：只有编号、角色、文本和依据类型。"""
    return [{"id": row["id"], "role": row["role"], "text": row["text"],
             "evidence": row["evidence"]} for row in board.claims]


def _dedupe_objections(raw: dict[str, Any], board: Blackboard) -> dict[str, Any]:
    """同一论点上的同一句话只记一次，用于判断“是否出现新增异议”。"""
    existing = {(row["target"], row["text"]) for row in board.objections}
    fresh = [row for row in raw.get("objections", [])
             if (row.get("target"), row.get("text")) not in existing]
    return {**raw, "objections": fresh}


def run(idea: str, *, constraints: list[str] | None = None,
        out_dir: str | Path = "runs", agent: Agent | None = None,
        answers: dict[str, str] | None = None,
        resolutions: dict[str, str] | None = None,
        max_rounds: int = 2, threshold: float = 0.7,
        progress: Progress | None = None) -> RunResult:
    if not idea.strip():
        raise ContractError("EMPTY_IDEA", "想法不能为空", "idea")
    if max_rounds < 1:
        raise ContractError("INVALID_FIELD", "max_rounds 至少为 1", "max_rounds")

    agent = agent or MockAgent()
    say = progress or (lambda message: None)
    warnings: list[dict[str, Any]] = []

    def warn(stage: str, role: Any, error: ContractError) -> None:
        warnings.append({"stage": stage, "role": role.id,
                         "code": error.code, "message": error.message})
        detail = f"{error.code} {error.message}".strip()
        say(f"  ! {role.name} 在 {stage} 阶段失败，已跳过：{detail}")

    def safe_respond(stage: str, role: Any, context: dict[str, Any]) -> dict[str, Any] | None:
        """单个角色失败不终止整条讨论；记警告后跳过该角色。"""
        try:
            return agent.respond(stage, role, context)
        except ContractError as error:
            warn(stage, role, error)
            return None

    def safe_output(stage: str, role: Any, raw: dict[str, Any] | None) -> dict[str, Any] | None:
        """模型输出不合契约时同样按角色隔离，不写入黑板。"""
        if raw is None:
            return None
        try:
            return board.add_role_output(raw, role_id=role.id, stage=stage)
        except ContractError as error:
            warn(stage, role, error)
            return None
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
    if hasattr(agent, "bind_run"):
        agent.bind_run(target)

    board = Blackboard(idea, constraints)
    events = EventLog()
    events.emit("system", "run_created",
                f"实例创建，角色实现 {agent.name}，约束 {len(board.constraints)} 条",
                object_type="run", object_id=run_id, version=board.version)

    # ---------- 阶段 1：首轮澄清 ----------
    board.stage = "clarify"
    say("阶段 1/7 首轮澄清：需求分析角色提出只能由人回答的阻断问题")
    clarify = safe_respond("clarify", role_card("ba"),
                           isolated_context(idea, board.constraints))
    before = len(board.open_questions)
    blocking = []
    if clarify is not None:
        clarify_output = safe_output("clarify", role_card("ba"), clarify)
        if clarify_output is not None:
            blocking = board.open_questions[before:]
            for row in blocking:
                row["blocking"] = True
            events.emit("ba", "clarify_questions", f"提出 {len(blocking)} 个阻断问题",
                        object_type="blackboard", object_id=run_id, version=board.version)

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
    say("阶段 2/7 独立发散：6 个角色并行、互不可见")
    for role in DISCUSSION_ROLES:
        # 每次都给一份全新上下文，只含原始输入，杜绝互相污染
        raw = safe_respond("diverge", role,
                           deepcopy(isolated_context(idea, board.constraints)))
        if raw is None:
            continue
        output = safe_output("diverge", role, raw)
        if output is None:
            continue
        events.emit(role.id, "claims_written",
                    f"{role.name}：{len(output['claim_ids'])} 条主张、"
                    f"{len(output['assumption_ids'])} 条假设",
                    object_type="claim", object_id=",".join(output["claim_ids"]) or "—",
                    version=board.version)
        say(f"  · {role.name}：{len(output['claim_ids'])} 条主张")
    board.checkpoint("diverge")

    # ---------- 阶段 3—4：批判与回应（最多 max_rounds 轮） ----------
    rounds_without_new = 0
    termination: dict[str, Any] = {}
    view = _claims_view(board)
    for round_no in range(1, max_rounds + 1):
        board.stage = "critique"
        board.round = round_no + 1
        say(f"阶段 3/7 第 {round_no} 轮结构化批判：必须引用论点编号并表态")
        new_objections = 0
        for role in DISCUSSION_ROLES:
            raw = safe_respond("critique", role,
                               deepcopy({"idea": idea, "constraints": board.constraints,
                                         "claims": view}))
            if raw is None:
                continue
            raw = _dedupe_objections(raw, board)
            output = safe_output("critique", role, raw)
            if output is None:
                continue
            new_objections += len(output["objections"])
        events.emit("host", "round_critique",
                    f"第 {round_no} 轮新增 {new_objections} 条异议",
                    object_type="run", object_id=run_id, version=board.version)

        board.divergence = divergence_map(board)
        board.stage = "respond"
        pending = board.open_objections()
        for role in DISCUSSION_ROLES:
            mine = [row for row in pending
                    if board.claim_by_id(row["target"])["role"] == role.id]
            if not mine:
                continue
            payload = [{"id": row["id"], "target": row["target"],
                        "target_role": role.id, "text": row["text"],
                        "severity": row["severity"],
                        "divergence": board.divergence.get(row["id"], "")} for row in mine]
            raw = safe_respond("respond", role,
                               deepcopy({"idea": idea, "objections": payload,
                                         "round": round_no}))
            if raw is None:
                continue
            signed = safe_output("respond", role, raw)
            if signed is None:
                continue
            board.apply_responses(role.id, signed["responses"])
            events.emit(role.id, "objections_answered",
                        f"{role.name} 回应 {len(signed['responses'])} 条异议",
                        object_type="objection", object_id=run_id, version=board.version)

        termination = evaluate_termination(board, max_rounds=max_rounds,
                                           threshold=threshold,
                                           rounds_without_new=rounds_without_new)
        events.emit("host", "termination_checked", termination["label"],
                    object_type="run", object_id=run_id, version=board.version)
        say(f"  · 终止判断：{termination['label']}")
        if termination["stop"]:
            break
        rounds_without_new = 0 if new_objections else rounds_without_new + 1
    board.checkpoint("discussion")

    # ---------- 阶段 5：整合 ----------
    board.stage = "synthesize"
    say("阶段 4/7 方案整合：保留来源与少数意见，生成候选方案与估算")
    raw = agent.respond("synthesize", SYNTHESIZER,
                        deepcopy({"idea": idea, "constraints": board.constraints,
                                  "claims": view}))
    synthesis = validate_synthesis(raw, known_claims={row["id"] for row in board.claims})
    for row in synthesis["options"]:
        board.add_option(**row)
    for row in synthesis["decisions"]:
        board.add_decision(**row, decider="AI 建议")
    board.not_adopted = synthesis["not_adopted"]
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

    # ---------- 阶段 6：立场签署 ----------
    board.stage = "sign"
    say("阶段 5/7 立场签署：赞同／有条件赞同／反对／未评审")
    option_view = [{"id": row["id"], "key": row["key"], "name": row["name"]}
                   for row in board.options]
    for role in DISCUSSION_ROLES:
        raw = safe_respond("sign", role, deepcopy({"idea": idea, "options": option_view}))
        if raw is None:
            continue
        try:
            signed = validate_stances(raw, role_id=role.id,
                                      known_options={row["id"] for row in board.options})
        except ContractError as error:
            warn("sign", role, error)
            continue
        for item in signed["stances"]:
            board.record_stance(role.id, item["option"], item["stance"],
                                reason=item["reason"], condition=item["condition"])
        events.emit(role.id, "stances_signed",
                    f"{role.name} 签署 {len(signed['stances'])} 个方案",
                    object_type="stance", object_id=run_id, version=board.version)

    # ---------- 阶段 7：伪共识检查与整合复核 ----------
    board.stage = "guard"
    if needs_counterexample(board):
        say("阶段 6/7 伪共识检查：全部立场一致，要求质疑者提出反例")
        raw = safe_respond("counterexample", role_card("skeptic"),
                           deepcopy({"idea": idea, "options": option_view}))
        if raw is None:
            board.set_pseudo_check({"triggered": False, "raised": [],
                                    "note": "质疑角色生成反例失败，本次未触发复核"})
        else:
            output = safe_output("guard", role_card("skeptic"), raw)
            if output is None:
                board.set_pseudo_check({"triggered": False, "raised": [],
                                        "note": "质疑角色输出不合格，本次未触发复核"})
            else:
                board.set_pseudo_check({"triggered": True, "raised": output["claim_ids"],
                                    "note": "全票通过，主持人要求质疑者提供反例"})
                events.emit("host", "pseudo_consensus_probe",
                            f"触发反例复核，新增 {len(output['claim_ids'])} 条反面论点",
                            object_type="claim", object_id=",".join(output["claim_ids"]),
                            version=board.version)
    else:
        board.set_pseudo_check({"triggered": False, "raised": [],
                                "note": "存在反对立场，未触发全票复核"})

    adopted = [claim_id for row in board.decisions for claim_id in row.get("based_on", [])]
    not_adopted = [row["claim"] for row in board.not_adopted]
    for role in DISCUSSION_ROLES:
        own = [{"id": row["id"], "text": row["text"], "evidence": row["evidence"]}
               for row in board.claims if row["role"] == role.id]
        if not own:
            continue
        raw = safe_respond("review", role,
                           deepcopy({"claims": own, "adopted": adopted,
                                     "not_adopted": not_adopted}))
        if raw is None:
            continue
        review_output = safe_output("review", role, raw)
        if review_output is None:
            continue
        own_ids = {row["id"] for row in own}
        reviews = [row for row in review_output["reviews"] if row["claim"] in own_ids]
        if not reviews:
            continue
        board.apply_reviews(role.id, reviews)
    for issue in integrity_issues(board):
        board.objections.append({
            "id": f"OBJ-{len(board.objections) + 1:03d}",
            "role": "system", "round": board.round,
            "target": issue["claim"],
            "text": f"整合复核发现主张被{issue['verdict']}：{issue['note']}",
            "consequence": "方案与讨论结论不一致，须修订后再确认",
            "severity": "blocking", "status": "open",
        })
        events.emit("system", "integrity_blocked",
                    f"{issue['claim']} 复核失真，已阻断", object_type="objection",
                    object_id=board.objections[-1]["id"], version=board.version)

    # ---------- 人工裁决（可选） ----------
    for objection_id, decision in (resolutions or {}).items():
        board.resolve_objection(objection_id, decision)
        events.emit("human", "objection_resolved", f"{objection_id} 由人工裁决：{decision}",
                    object_type="objection", object_id=objection_id, version=board.version)

    # ---------- 收敛判定与 Proposal ----------
    board.divergence = divergence_map(board)
    termination = evaluate_termination(board, max_rounds=max_rounds,
                                       threshold=threshold,
                                       rounds_without_new=rounds_without_new)
    board.set_termination(termination)
    consensus = consensus_from(board, termination)
    board.set_consensus(consensus["state"], consensus["reason"])
    board.stage = "proposal"
    events.emit("host", "consensus_decided",
                f"共识状态：{consensus['state']}（{consensus['reason']}）",
                object_type="run", object_id=run_id, version=board.version)
    say(f"阶段 7/7 收敛判定：{consensus['state']} —— {consensus['reason']}")

    proposal = render_proposal(board, run_id=run_id, created_at=created_at,
                               agent_name=agent.name)
    proposal_path = target / "proposal.md"
    proposal_path.write_text(proposal, encoding="utf-8")
    (target / "blackboard.json").write_text(
        json.dumps(board.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "events.jsonl").write_text(events.to_jsonl() + "\n", encoding="utf-8")

    opinions = key_opinions(board)
    opinion_counts = dict(Counter(row["status"] for row in opinions))
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
            "rounds": termination.get("round", board.round - 1),
        },
        "termination": {"reason": termination["reason"], "label": termination["label"],
                        "support": termination["support"]},
        "support": option_support(board),
        "consensus": board.snapshot()["consensus"],
        "opinions": opinion_counts,
        "warnings": warnings,
    }
    (target / "run.json").write_text(
        json.dumps({**report, "idea": idea, "constraints": board.constraints,
                    "created_at": created_at, "agent": agent.name},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return RunResult(run_id=run_id, out_dir=target, board=board, events=events,
                     proposal_path=proposal_path, report=report)
