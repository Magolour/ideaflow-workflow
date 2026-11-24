"""规格流程编排：快照 → 两组草案 → 评审 → 规格 → 自审查 → 修订 → 升级 → 导出。

Web 工作台和命令行都调用这里，保证两条入口产出同一套文件与同一份结论。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .agents import MockAgent
from .artifacts import deliver
from .audit import audit_spec, save_audit
from .contracts import ContractError
from .escalate import escalate, save_question
from .pipeline import run as run_discussion
from .pipeline import slugify
from .race import run_race, save_race
from .reviews import review_all
from .revisions import save_revisions, scope_revision
from .snapshot import build, save
from .spec import build_spec, save_spec
from .spec_doc import export_spec
from .render_html import render_markdown
from .storage import now_iso, read_json, write_json
from .tradeoffs import build_tradeoffs

CASES_DIR = Path(__file__).resolve().parents[1] / "cases"
CASE_NAMES = ("campus-books", "campus-books-tight", "device-booking")

Progress = Callable[[str], None]


@dataclass
class SpecFlowResult:
    run_id: str
    out_dir: Path
    snapshot: Any
    race: Any
    reviews: dict[str, Any]
    spec: Any
    audits: list[dict[str, Any]]
    question: dict[str, Any] | None
    files: dict[str, str]
    payload: dict[str, Any] = field(default_factory=dict)


def _merge_ai_calls(source: Path, destination: Path) -> None:
    log = source / "ai_calls.jsonl"
    if not log.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as target:
        target.write(log.read_text(encoding="utf-8"))


def load_case(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9-]+", name or ""):
        raise ContractError("INVALID_CASE", f"非法的案例名 {name}", "case")
    path = CASES_DIR / f"{name}.md"
    if not path.exists():
        raise ContractError("CASE_NOT_FOUND", f"找不到案例 {name}", "case")
    return path.read_text(encoding="utf-8")


def run(raw_text: str, constraints: list[str] | None = None, *,
        out_dir: str | Path = "runs", confirm: str = "产品经理",
        case_name: str | None = None, progress: Progress | None = None,
        agent: Any | None = None, discuss: bool = False,
        discuss_rounds: int = 2) -> SpecFlowResult:
    say = progress or (lambda message: None)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = slugify(raw_text, limit=16)
    run_id = f"{stamp}-spec-{slug}"
    target = Path(out_dir) / run_id
    suffix = 2
    while target.exists():
        target = Path(out_dir) / f"{run_id}-{suffix}"
        suffix += 1
    run_id = target.name
    target.mkdir(parents=True, exist_ok=False)
    if agent is not None and hasattr(agent, "bind_run"):
        agent.bind_run(target)

    board = None
    if discuss:
        say("先跑讨论：澄清 → 六角色发散 → 批判/回应 → 整合 → 签署")
        discussion = run_discussion(raw_text, constraints=constraints,
                                    out_dir=target / "discussion", agent=agent,
                                    max_rounds=discuss_rounds, progress=progress)
        board = discussion.board
        _merge_ai_calls(discussion.out_dir, target / "ai_calls.jsonl")
        if agent is not None and hasattr(agent, "bind_run"):
            agent.bind_run(target)
        say(f"讨论完成：{len(board.claims)} 条主张、"
            f"{len(board.unresolved_blocking())} 条未解决阻断")

    snapshot = build(raw_text, constraints=constraints, board=board).confirm(confirm)
    save(snapshot, target)
    counts = snapshot.counts()
    say(f"快照 {snapshot.snapshot_id} v{snapshot.version}："
        f"{counts['requirement']} 条要求、{counts['constraint']} 条约束、"
        f"{counts['open']} 个待澄清")

    draft_agent = None if isinstance(agent, MockAgent) else agent
    race = run_race(snapshot, max_attempts=2, agent=draft_agent)
    if race.failed_groups:
        details = []
        for group in race.failed_groups:
            error = race.groups[group].error or {}
            details.append(f"{group} {error.get('code', '')} {error.get('message', '')}".strip())
        raise ContractError("GROUP_FAILED",
                            "方案组未能完成：" + "；".join(details), "groups")
    save_race(race, target)
    say("两组草案：" + " ｜ ".join(
        f"{group} {draft.commit_days} 天" for group, draft in sorted(race.drafts.items())))

    reviews = review_all(snapshot, race.drafts)
    spec = build_spec(snapshot, race.drafts, reviews)
    write_json(target / "review.json", reviews)
    save_spec(spec, target)
    say(f"评审整合：以 {spec.base_draft} 为基线，{len(spec.decisions)} 条决策、"
        f"{len(spec.modules)} 个模块")

    audit = audit_spec(spec, snapshot)
    audit["spec_version"] = spec.version
    save_audit(audit, target, spec.version)
    export_spec(spec, snapshot, target)
    audits = [audit]
    say(f"自审查 v{spec.version}：" +
        ("通过" if audit["passed"] else f"{audit['counts']['blocking']} 条阻断"))

    if not audit["passed"]:
        try:
            revised, record, _ = scope_revision(spec, snapshot, audit)
        except ContractError as error:
            if error.code != "NO_AUTO_FIX":
                raise
        else:
            save_spec(revised, target)
            save_revisions(revised, target)
            audit = audit_spec(revised, snapshot)
            audit["spec_version"] = revised.version
            save_audit(audit, target, revised.version)
            export_spec(revised, snapshot, target)
            spec = revised
            audits.append(audit)
            say(f"自动修订 {record['id']}：承诺 {record['before']['commit_days']} → "
                f"{record['after']['commit_days']} 天")

    question = None
    if not audit["passed"]:
        try:
            spec, question = escalate(spec, snapshot, audit)
        except ContractError as error:
            if error.code != "NO_ESCALATION":
                raise
        else:
            save_spec(spec, target)
            save_question(question, target)
            export_spec(spec, snapshot, target)
            say(f"人工问题 {question['id']}：{question['question']}")

    files = {
        "json": f"spec.v{spec.version}.json",
        "markdown": f"spec.v{spec.version}.md",
        "question": "human_question.json" if question else "",
        "manifest": "manifest.json",
    }
    markdown = (target / files["markdown"]).read_text(encoding="utf-8")
    log = target / "ai_calls.jsonl"
    ai_calls = 0
    if log.exists():
        ai_calls = len([line for line in log.read_text(encoding="utf-8").splitlines()
                        if line.strip()])
    payload = _payload(run_id, snapshot, race, reviews, spec, audits, question, files,
                       markdown, case_name, ai_calls=ai_calls)
    write_json(target / "flow.json", {
        "run_id": run_id, "flow": "spec", "case": case_name,
        "status": spec.status,
        "ai_calls": ai_calls, "spec": {"id": spec.spec_id, "version": spec.version,
                                        "hash": spec.hash},
        "question": question["id"] if question else None,
        "created_at": now_iso(),
    })

    extras: dict[str, Any] = {"race.json": race.to_json(), "review.json": reviews,
                              "flow.json": read_json(target / "flow.json")}
    for row in audits:
        name = f"audit.v{row['spec_version']}.json"
        if (target / name).exists():
            extras[name] = read_json(target / name)
    if (target / "revisions.json").exists():
        extras["revisions.json"] = read_json(target / "revisions.json")
        extras[f"revision.v{spec.version}.json"] = read_json(
            target / f"revision.v{spec.version}.json")
    if question:
        extras["human_question.json"] = question
    deliver(target, snapshot, race.drafts, extras=extras,
            texts={files["markdown"]: markdown, "spec.md": markdown})

    say(f"导出 {files['markdown']} ｜ 状态 {spec.status}")
    return SpecFlowResult(run_id=run_id, out_dir=target, snapshot=snapshot, race=race,
                          reviews=reviews, spec=spec, audits=audits,
                          question=question, files=files, payload=payload)


def _payload(run_id: str, snapshot: Any, race: Any, reviews: dict[str, Any], spec: Any,
             audits: list[dict[str, Any]], question: dict[str, Any] | None,
             files: dict[str, str], markdown: str,
             case_name: str | None, *, ai_calls: int = 0) -> dict[str, Any]:
    items = [{
        "id": item["id"], "type": item["type"],
        "label": {"requirement": "明确要求", "constraint": "约束", "open": "待澄清"}[item["type"]],
        "text": item["text"], "priority": item["priority"],
        "source": item["source"],
    } for item in snapshot.items]

    drafts = []
    for group in sorted(race.drafts):
        draft = race.drafts[group]
        drafts.append({
            "draft_id": draft.draft_id,
            "group": draft.group,
            "group_name": draft.group_name,
            "objective": draft.objective,
            "summary": draft.summary,
            "commit_days": draft.commit_days,
            "hash": draft.hash,
            "declined": draft.declined,
            "decisions": draft.decisions,
            "claims": draft.claims,
            "constraints_checked": draft.constraints_checked,
            "attempts": race.groups[group].attempts,
        })

    tradeoffs = build_tradeoffs(snapshot, race.drafts)
    review_rows = {key: {
        "scores": value["scores"], "weighted": value["weighted"],
        "criteria": value["criteria"], "disagreements": value["disagreements"],
        "open_items": value["open_items"], "summary": value["summary"],
    } for key, value in reviews["drafts"].items()}

    return {
        "flow": "spec",
        "run_id": run_id,
        "case": case_name,
        "title": _title(snapshot),
        "status": spec.status,
        "ai_calls": ai_calls,
        "status_label": {"draft": "草稿", "awaiting_human": "待人工决策",
                         "confirmed": "已确认"}.get(spec.status, spec.status),
        "snapshot": {"id": snapshot.snapshot_id, "version": snapshot.version,
                     "hash": snapshot.hash, "counts": snapshot.counts(), "items": items},
        "drafts": drafts,
        "tradeoffs": tradeoffs,
        "reviews": {"drafts": review_rows, "verdict": reviews["verdict"],
                    "ranking": reviews["ranking"]},
        "spec": {
            "id": spec.spec_id, "version": spec.version, "hash": spec.hash,
            "status": spec.status,
        "ai_calls": ai_calls, "base_draft": spec.base_draft,
            "architecture": spec.architecture,
            "decisions": spec.decisions, "modules": spec.modules,
            "mapping": spec.mapping, "acceptance": spec.acceptance,
            "open_items": spec.open_items, "not_adopted": spec.not_adopted,
            "non_goals": spec.non_goals, "effort": spec.effort,
        },
        "audits": audits,
        "revisions": spec.revisions,
        "question": question,
        "files": files,
        "markdown": markdown,
        "html": render_markdown(markdown),
    }


def _title(snapshot: Any) -> str:
    for line in snapshot.raw_text.splitlines():
        text = line.strip().lstrip("#").strip()
        if text and len(text) > 3:
            return text[:40]
    return "设计规格"
