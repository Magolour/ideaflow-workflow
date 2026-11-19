"""规格流程命令行：快照 → 两组草案 → 评审 → 规格 → 自审查 → 导出。

    python -m ideaflow.spec_cli snapshot "做一个校园二手书小程序" -c "两周内出演示版"
    python -m ideaflow.spec_cli snapshot --file idea.md --confirm 张三
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from .artifacts import deliver
from .audit import audit_hash, audit_spec, save_audit
from .constraints import deadline_days
from .contracts import ContractError
from .demo import run_case
from .drafts import SATISFIES_LABEL, Draft
from .escalate import escalate, save_question
from .pipeline import slugify
from .race import load_drafts, load_race, retry_failed, run_race, save_race, unchanged_groups
from .reviews import SEVERITY_LABEL, review_all
from .revisions import save_revisions, scope_revision
from .snapshot import ITEM_TYPE_LABEL, PRIORITY_LABEL, Snapshot, build, load, save
from .spec import build_spec, load_spec, save_spec
from .spec_doc import export_spec, verify_export
from .storage import read_json, write_json


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _ellipsis(text: str, width: int) -> str:
    if _display_width(text) <= width:
        return text
    kept, used = "", 0
    for char in text:
        step = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if used + step > width - 1:
            break
        kept += char
        used += step
    return kept + "…"


def _with_cost(choice: str, cost: float, width: int) -> str:
    """保留成本数字，只截断方案描述，避免出现「（5…」这种断尾。"""
    suffix = f"（{cost} 天）"
    return _ellipsis(choice, max(8, width - _display_width(suffix))) + suffix


def _print_snapshot(snapshot: Snapshot, path: Path) -> None:
    counts = snapshot.counts()
    print(f"快照 {snapshot.snapshot_id} · v{snapshot.version} · "
          f"{'已确认' if snapshot.status == 'confirmed' else '草稿'}")
    print(f"hash {snapshot.hash[:16]}…")
    print(f"明确要求 {counts['requirement']} ｜ 约束 {counts['constraint']} ｜ "
          f"待澄清 {counts['open']}")
    print()

    text_width = min(46, max(_display_width(item["text"]) for item in snapshot.items))
    headers = [("编号", 6), ("类型", 10), ("优先级", 8), ("条目", text_width), ("来源", 0)]
    print("  ".join(_pad(title, width) for title, width in headers))
    for item in snapshot.items:
        source = item["source"]
        where = f"{source['section']}"
        if source["start_line"]:
            where += f" · 第 {source['start_line']} 行"
        print("  ".join([
            _pad(item["id"], 6),
            _pad(ITEM_TYPE_LABEL[item["type"]], 10),
            _pad(PRIORITY_LABEL[item["priority"]], 8),
            _pad(_ellipsis(item["text"], text_width), text_width),
            where,
        ]).rstrip())

    print()
    print(f"产物：{path}")


def cmd_snapshot(args: argparse.Namespace) -> int:
    if args.file == "-":
        raw = sys.stdin.read()
    elif args.file:
        raw = Path(args.file).read_text(encoding="utf-8")
    else:
        raw = args.idea
    snapshot = build(raw, constraints=args.constraint, questions=args.question)
    if args.confirm:
        snapshot = snapshot.confirm(args.confirm)

    if args.out:
        target = Path(args.out)
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = Path("runs") / f"{stamp}-{slugify(args.idea or raw)}"
    path = save(snapshot, target)

    if args.json:
        print(json.dumps(snapshot.to_json(), ensure_ascii=False, indent=2))
    else:
        _print_snapshot(snapshot, path)
    return 0


def _print_draft(draft: Draft) -> None:
    print(f"{draft.group} 组 · {draft.group_name} ｜ {len(draft.decisions)} 条决策 ｜ "
          f"承诺 {draft.commit_days} 天")
    print(f"  目标：{draft.objective}")
    for decision in draft.decisions:
        flag = "" if decision["satisfies"] == "full" else f"（{SATISFIES_LABEL[decision['satisfies']]}）"
        print(f"  {decision['requirement_ids'][0]}  {_ellipsis(decision['scope'], 30)}")
        print(f"        → {decision['choice']}{flag}")
    conflicts = [check for check in draft.constraints_checked if not check["complies"]]
    for check in conflicts:
        print(f"  ! {check['constraint_id']} {check['note']}")
    print()


def _print_attempt(record: dict) -> None:
    if record["state"] == "done":
        print(f"  {record['group']} 第 {record['attempt']} 次：成功"
              f"（{record['duration_ms']} ms，hash {record['hash'][:8]}…）")
    else:
        error = record.get("error") or {}
        print(f"  {record['group']} 第 {record['attempt']} 次：失败 "
              f"{error.get('code')} {error.get('message')}")


def cmd_race(args: argparse.Namespace) -> int:
    snapshot = load(args.dir)
    if snapshot.status != "confirmed":
        if not args.confirm:
            print(f"快照 {snapshot.snapshot_id} v{snapshot.version} 尚未确认；"
                  f"加 --confirm 姓名 确认后再出方案。", file=sys.stderr)
            return 2
        snapshot = snapshot.confirm(args.confirm)
        save(snapshot, args.dir)

    fail_groups = {group: args.fail_times for group in args.fail_group}
    max_attempts = 1 if args.no_retry else args.max_attempts

    if args.json:
        race = run_race(snapshot, fail_groups=fail_groups, max_attempts=max_attempts)
        save_race(race, args.dir)
        payload = {**race.to_json(),
                   "drafts": {group: draft.to_json() for group, draft in race.drafts.items()}}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"两组草案 · 快照 {snapshot.snapshot_id} v{snapshot.version} · "
          f"hash {snapshot.hash[:12]}…")
    print("两组读同一份快照，各自独立出方案，互相不可见。\n")

    race = run_race(
        snapshot, fail_groups=fail_groups, max_attempts=max_attempts,
        on_attempt=lambda current, group, attempt: _print_attempt(current.attempts[-1]))
    print()

    for group in ("A", "B"):
        if group in race.drafts:
            _print_draft(race.drafts[group])
        else:
            state = race.groups[group]
            error = state.error or {}
            print(f"{group} 组 · 失败：{error.get('code')} {error.get('message')}")
            print()

    save_race(race, args.dir)
    manifest = deliver(args.dir, snapshot, race.drafts,
                       extras={"race.json": race.to_json()})
    print("已交付：")
    for name in manifest["files"]:
        print(f"  {name}")
    print(f"引用边 {manifest['citations']['edges']} 条 · "
          f"清单 {Path(args.dir) / 'manifest.json'}")

    if race.failed_groups:
        print()
        print(f"{'、'.join(race.failed_groups)} 组未完成；已成功的组已保留。")
        print(f"单独重试：python -m ideaflow.spec_cli retry --dir {args.dir}")
        return 1
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    root = Path(args.dir)
    before = load_race(root)
    if not before.failed_groups:
        print("没有失败的组，无需重试。")
        return 0

    failed = list(before.failed_groups)
    print(f"重试失败组：{'、'.join(failed)}（已成功的组不重跑）")
    after = retry_failed(root, max_attempts=args.max_attempts)

    new_records = after.attempts[len(before.attempts):]
    for record in new_records:
        _print_attempt(record)

    for group, same in unchanged_groups(before, after).items():
        state = after.groups[group]
        print(f"  {group} 组未重跑：hash {state.draft_hash[:8]}…"
              f"（{'与失败前一致' if same else '发生变化'}）")

    snapshot = load(root)
    manifest = deliver(root, snapshot, after.drafts,
                       extras={"race.json": after.to_json()})
    print(f"交付已更新：{', '.join(manifest['files'])}")

    if after.failed_groups:
        print(f"仍失败：{'、'.join(after.failed_groups)}")
        return 1
    return 0


def cmd_case(args: argparse.Namespace) -> int:
    result = run_case(args.file, args.out, confirm=args.confirm)
    tradeoffs = result.tradeoffs
    if args.json:
        print(json.dumps(tradeoffs, ensure_ascii=False, indent=2))
        return 0

    project = tradeoffs["project"]
    print(f"案例：{args.file}")
    print(f"快照 {result.snapshot.snapshot_id} v{result.snapshot.version} · "
          f"hash {result.snapshot.hash[:12]}…")
    deadline = project["deadline_days"]
    line = f"两组承诺：A {project['a_days']} 天 ｜ B {project['b_days']} 天"
    if deadline:
        line += f" ｜ 期限 {deadline} 天"
    print(line)
    print()

    if not tradeoffs["items"]:
        print("两组方案在同一需求上没有选择差异。")
        return 0

    headers = [("需求", 20), ("A 交付优先", 34), ("B 体验优先", 34), ("差额", 8)]
    print("  ".join(_pad(title, width) for title, width in headers).rstrip())
    for row in tradeoffs["items"]:
        label = _ellipsis(f"{row['requirement_id']} {row['requirement']}", 20)
        left = _with_cost(row["a"]["choice"], row["a"]["cost_days"], 34)
        right = _with_cost(row["b"]["choice"], row["b"]["cost_days"], 34)
        print("  ".join([_pad(label, 20), _pad(left, 34), _pad(right, 34),
                         f"{row['delta_days']:+.1f} 天"]).rstrip())

    counts = tradeoffs["counts"]
    print()
    print(f"取舍 {counts['total']} 条：放弃需求换工期 {counts['requirement_vs_schedule']} 条，"
          f"多投入换体验 {counts['experience_vs_schedule']} 条")
    if deadline:
        print(f"期限对照：A {'在限期内' if project['a_within_deadline'] else '超出期限'} ｜ "
              f"B {'在限期内' if project['b_within_deadline'] else f'超出 {deadline} 天'}")

    headline = next(row for row in tradeoffs["items"] if row["id"] == tradeoffs["headline"])
    print()
    print(f"关键取舍 {headline['id']} · {headline['requirement_id']} {headline['requirement']}")
    print(f"  A：{headline['a']['choice']}"
          f"（{headline['a']['cost_days']} 天，{SATISFIES_LABEL[headline['a']['satisfies']]}）")
    print(f"  B：{headline['b']['choice']}"
          f"（{headline['b']['cost_days']} 天，{SATISFIES_LABEL[headline['b']['satisfies']]}）")
    print(f"  → {headline['summary']}")

    print()
    print("已交付：")
    for name in result.manifest["files"]:
        print(f"  {name}")
    print(f"目录：{Path(args.out)}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    root = Path(args.dir)
    snapshot = load(root)
    race = load_race(root)
    load_drafts(race, root)
    if len(race.drafts) < 2:
        print("需要两份成功的草案才能评审整合；先补齐失败组。", file=sys.stderr)
        return 2

    reviews = review_all(snapshot, race.drafts)
    spec = build_spec(snapshot, race.drafts, reviews)
    write_json(root / "review.json", reviews)
    save_spec(spec, root)

    if args.json:
        print(json.dumps({"reviews": reviews, "spec": spec.to_json()},
                         ensure_ascii=False, indent=2))
        return 0

    print("逐份评审（一次只给一份草案，互不参照）")
    for draft_id, review in reviews["drafts"].items():
        scores = review["scores"]
        print(f"  {draft_id} 价值 {scores['value']} ｜ 可行 {scores['feasibility']} ｜ "
              f"成本 {scores['cost']} → 加权 {review['weighted']}")
        for row in review["disagreements"]:
            impact = "、".join(row["requirement_ids"] or row.get("constraint_ids", []))
            print(f"    {row['id']} [{SEVERITY_LABEL[row['severity']]}] "
                  f"{impact or '全局'}：{row['issue']}")

    print()
    print(f"结论：以 {spec.base_draft} 为基线（{reviews['verdict']['reason']}）")
    print()
    print("整合决策：")
    for decision in spec.decisions:
        flag = "采纳" if decision["origin"] == "adopted" else "基线"
        mark = " ⚠ 待人工" if decision["needs_human"] else ""
        print(f"  {decision['id']} [{flag}] {decision['requirement_ids'][0]} "
              f"{decision['choice']}{mark}")
        if decision["origin"] == "adopted":
            print(f"       理由：{decision['reason']}")
    print()
    blocking = len(spec.blocking_open_items)
    print(f"模块 {len(spec.modules)} 个 ｜ 映射 {len(spec.mapping)} 条 ｜ "
          f"验收 {len(spec.acceptance)} 条 ｜ 待决 {len(spec.open_items)} 项（阻断 {blocking}）")
    print(f"估算：期望 {spec.effort['expected_total']} 天，承诺 {spec.effort['commit_days']} 天")
    for item in spec.open_items:
        print(f"  {item['id']} {'[阻断]' if item['blocking'] else '[可延后]'} {item['question']}")

    manifest = deliver(root, snapshot, race.drafts,
                       extras={"race.json": race.to_json(), "review.json": reviews,
                               f"spec.v{spec.version}.json": spec.to_json()})
    print()
    print(f"已交付：{', '.join(manifest['files'])}, manifest.json")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    root = Path(args.dir)
    snapshot = load(root)
    spec = load_spec(root)
    audit = audit_spec(spec, snapshot)
    save_audit(audit, root, spec.version)

    if args.json:
        print(json.dumps({**audit, "hash": audit_hash(audit)}, ensure_ascii=False, indent=2))
        return 0 if audit["passed"] else 1

    checks = audit["checks"]
    deadline = next((value for value in
                     (deadline_days(item["text"]) for item in snapshot.constraints)
                     if value), None)
    print(f"自审查 {spec.spec_id} v{spec.version} · spec hash {spec.hash[:12]}…")
    print(f"覆盖：{checks['coverage']['with_decision']}/{checks['coverage']['requirements']} "
          f"有决策 ｜ {checks['coverage']['with_module']}/{checks['coverage']['requirements']} "
          f"有模块 ｜ {checks['coverage']['with_acceptance']}/"
          f"{checks['coverage']['requirements']} 有验收")
    line = f"约束：{checks['constraints']['verified']}/{checks['constraints']['total']} 已核对"
    if deadline:
        line += f"（期限 {deadline} 天 vs 承诺 {spec.effort['commit_days']} 天）"
    print(line)
    print(f"验收：{checks['acceptance']['measurable']}/{checks['acceptance']['total']} 可度量")
    print()

    if audit["passed"]:
        print("结论：通过，没有阻断问题。")
    else:
        print(f"结论：未通过 · 阻断 {audit['counts']['blocking']} 条，"
              f"重要 {audit['counts']['major']} 条")
        for issue in audit["issues"]:
            print(f"  {issue['id']} [{SEVERITY_LABEL[issue['severity']]}] "
                  f"{issue['label']}：{issue['message']}")
            if issue["fix_hint"]:
                print(f"       建议：{issue['fix_hint']}")

    race = load_race(root)
    load_drafts(race, root)
    extras: dict[str, Any] = {"race.json": race.to_json(),
                              f"audit.v{spec.version}.json": {**audit,
                                                              "hash": audit_hash(audit)}}
    for name in ("review.json", f"spec.v{spec.version}.json"):
        path = root / name
        if path.exists():
            extras[name] = read_json(path)
    deliver(root, snapshot, race.drafts, extras=extras)

    print()
    print(f"已写入：audit.v{spec.version}.json ｜ 清单已更新")
    return 0 if audit["passed"] else 1


def cmd_export(args: argparse.Namespace) -> int:
    root = Path(args.dir)
    snapshot = load(root)
    spec = load_spec(root)
    result = export_spec(spec, snapshot, root)
    check = verify_export(root, spec.version)

    if args.json:
        print(json.dumps({key: value for key, value in result.items()
                          if key != "markdown_text"} | {"verify": check["checks"]},
                         ensure_ascii=False, indent=2))
        return 0 if check["ok"] else 1

    print(f"导出 {spec.spec_id} v{spec.version}")
    print(f"  {result['json']}  （内容哈希 {result['content_hash'][:12]}…）")
    print(f"  {result['markdown']}  （文件哈希 {result['markdown_sha256'][:12]}…）")
    print()
    if check["ok"]:
        print(f"同版校验：通过 · 版本 {spec.version} · 双向引用一致 · "
              f"内容哈希 {result['content_hash'][:12]}…")
    else:
        failed = [key for key, value in check["checks"].items() if not value]
        print(f"同版校验：未通过 · {', '.join(failed)}")

    if (root / "race.json").exists():
        race = load_race(root)
        load_drafts(race, root)
        extras: dict[str, Any] = {"race.json": race.to_json(),
                                  result["json"]: read_json(root / result["json"]),
                                  "spec.json": read_json(root / "spec.json")}
        for name in ("review.json", f"audit.v{spec.version}.json"):
            path = root / name
            if path.exists():
                extras[name] = read_json(path)
        deliver(root, snapshot, race.drafts, extras=extras,
                texts={result["markdown"]: result["markdown_text"],
                       "spec.md": result["markdown_text"]})
        print("清单已更新：manifest.json")
    return 0 if check["ok"] else 1


def cmd_revise(args: argparse.Namespace) -> int:
    root = Path(args.dir)
    snapshot = load(root)
    spec = load_spec(root)
    audit = audit_spec(spec, snapshot)

    if audit["passed"]:
        print(f"{spec.spec_id} v{spec.version} 没有阻断问题，无需修订。")
        return 0

    try:
        revised, record, _ = scope_revision(spec, snapshot, audit)
    except ContractError as error:
        if error.code == "NO_AUTO_FIX":
            print(f"无法自动修订：{error.message}")
            print("需要人工决策，可用 escalate 生成具体问题。")
            return 2
        raise

    save_spec(revised, root)
    new_audit = audit_spec(revised, snapshot)
    save_audit(new_audit, root, revised.version)
    save_revisions(revised, root)
    result = export_spec(revised, snapshot, root)

    print(f"发现问题（v{spec.version}）：")
    for issue in audit["issues"]:
        if issue["id"] in record["issue_ids"]:
            print(f"  {issue['id']} [{SEVERITY_LABEL[issue['severity']]}] "
                  f"{issue['label']}：{issue['message']}")
    print()
    print(f"修订 {record['id']}：{record['reason']}")
    for change in record["changes"]:
        print(f"  {change['requirement_id']} "
              f"{change['before']['cost_days']} → {change['after']['cost_days']} 天 ｜ "
              f"{change['after']['choice']}")
    print()
    recheck = record["recheck"]
    print(f"复核（v{revised.version}）："
          f"{'通过' if recheck['passed'] else '仍有阻断'} ｜ "
          f"已解决 {len(recheck['resolved'])} 条 ｜ 剩余 {len(recheck['remaining'])} 条")
    for issue in recheck["remaining"]:
        print(f"  {issue['id']} [{SEVERITY_LABEL[issue['severity']]}] {issue['label']}")
    print(f"新承诺 {revised.effort['commit_days']} 天 ｜ 新哈希 {revised.hash[:12]}…")

    if (root / "race.json").exists():
        race = load_race(root)
        load_drafts(race, root)
        extras: dict[str, Any] = {
            "race.json": race.to_json(),
            f"spec.v{revised.version}.json": read_json(root / result["json"]),
            f"audit.v{revised.version}.json": read_json(root / f"audit.v{revised.version}.json"),
            "revisions.json": read_json(root / "revisions.json"),
            f"revision.v{revised.version}.json": read_json(
                root / f"revision.v{revised.version}.json"),
        }
        for name in ("review.json", f"audit.v{spec.version}.json"):
            path = root / name
            if path.exists():
                extras[name] = read_json(path)
        deliver(root, snapshot, race.drafts, extras=extras,
                texts={result["markdown"]: result["markdown_text"],
                       "spec.md": result["markdown_text"]})
        print(f"已更新：spec.v{revised.version} / audit.v{revised.version} / "
              f"revisions.json / 清单")
    return 0 if new_audit["passed"] else 1


def _print_question(question: dict[str, Any]) -> None:
    print(f"需要人工决策 {question['id']} · {question['question']}")
    print("背景：")
    for line in question["background"]:
        print(f"  - {line}")
    print("选项：")
    for option in question["options"]:
        print(f"  {option['key']} {option['text']}（{option['cost_days']} 天）")
        print(f"     代价：{option['consequence']}")
    affected = question["affected"]
    print(f"影响：需求 {'、'.join(affected['requirements']) or '—'} ｜ "
          f"约束 {'、'.join(affected['constraints']) or '—'} ｜ "
          f"问题 {'、'.join(affected['issues'])}")
    print(f"不决策的后果：{question['if_undecided']}")
    print(f"建议：{question['recommended']}（仅建议，不代替决定）")


def cmd_escalate(args: argparse.Namespace) -> int:
    root = Path(args.dir)
    snapshot = load(root)
    spec = load_spec(root)
    audit = audit_spec(spec, snapshot)

    if audit["passed"]:
        print("自审查已通过，没有需要人工决策的阻断问题。")
        return 0
    if spec.escalation:
        print(f"{spec.spec_id} v{spec.version} 已有人工问题，未重复生成。\n")
        _print_question(spec.escalation)
        return 0

    try:
        escalated, question = escalate(spec, snapshot, audit)
    except ContractError as error:
        print(f"未能生成人工问题：{error.message}")
        return 2

    save_spec(escalated, root)
    save_question(question, root)
    result = export_spec(escalated, snapshot, root)

    print()
    _print_question(question)
    print()
    print(f"规格状态：{escalated.status} ｜ 版本 {escalated.version} ｜ "
          f"内容哈希 {escalated.hash[:12]}…（与修订后一致）")
    print(f"已写入：human_question.json ｜ {result['json']} ｜ {result['markdown']}")

    if (root / "race.json").exists():
        race = load_race(root)
        load_drafts(race, root)
        extras: dict[str, Any] = {
            "race.json": race.to_json(),
            "human_question.json": question,
            result["json"]: read_json(root / result["json"]),
            "spec.json": read_json(root / "spec.json"),
        }
        for name in ("review.json", f"audit.v{escalated.version}.json",
                     "revisions.json", f"revision.v{escalated.version}.json"):
            path = root / name
            if path.exists():
                extras[name] = read_json(path)
        deliver(root, snapshot, race.drafts, extras=extras,
                texts={result["markdown"]: result["markdown_text"],
                       "spec.md": result["markdown_text"]})
        print("清单已更新：manifest.json")
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    root = Path(args.dir)
    citations = read_json(root / "citations.json")
    sources = citations["sources"]
    item = sources.get(args.item)
    if item is None:
        print(f"快照里没有条目 {args.item}", file=sys.stderr)
        return 2

    print(f"{args.item} · {ITEM_TYPE_LABEL[item['type']]} · {item['text']}")
    print(f"来源：{item['section']} · 第 {item['start_line']} 行 · {item['excerpt']}")
    print()

    found = False
    for path in sorted((root / "drafts").glob("*.json")):
        draft = read_json(path)
        for decision in draft["decisions"]:
            if args.item in decision["requirement_ids"]:
                found = True
                print(f"  {draft['draft_id']} {decision['id']}（决定）：{decision['choice']}")
        for claim in draft["claims"]:
            refs = claim.get("requirement_ids", []) + claim.get("constraint_ids", [])
            if args.item in refs:
                found = True
                print(f"  {draft['draft_id']} {claim['id']}（主张）：{claim['text']}")
    if not found:
        print("  没有草案引用该条目。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ideaflow-spec",
        description="把想法整理成需求快照，逐步产出可审查的设计规格")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="解析输入，生成带编号和版本的需求快照")
    snap.add_argument("idea", nargs="?", default="", help="原始输入，可直接写多行")
    snap.add_argument("--file", help="从文本/Markdown 文件读取输入")
    snap.add_argument("-c", "--constraint", action="append", default=[], help="补充约束，可重复")
    snap.add_argument("-q", "--question", action="append", default=[], help="补充待澄清项，可重复")
    snap.add_argument("-o", "--out", help="产物目录，默认 runs/<时间>-<摘要>/")
    snap.add_argument("--confirm", metavar="NAME", help="生成后立即确认并记录确认人")
    snap.add_argument("--json", action="store_true", help="输出 JSON")
    snap.set_defaults(func=cmd_snapshot)

    race = sub.add_parser("race", help="两组基于同一快照独立出方案")
    race.add_argument("--dir", required=True, help="快照所在目录")
    race.add_argument("--confirm", metavar="NAME", help="快照尚未确认时，先以该名字确认")
    race.add_argument("--fail-group", action="append", default=[],
                      help="为指定组注入失败，可重复，例如 --fail-group B")
    race.add_argument("--fail-times", type=int, default=1,
                      help="注入失败的次数，默认 1 次")
    race.add_argument("--max-attempts", type=int, default=2,
                      help="每组最多尝试次数，默认 2（失败后自动重试一次）")
    race.add_argument("--no-retry", action="store_true", help="失败后不自动重试")
    race.add_argument("--json", action="store_true", help="输出 JSON")
    race.set_defaults(func=cmd_race)

    retry = sub.add_parser("retry", help="只重跑失败的组，已成功的组不重跑")
    retry.add_argument("--dir", required=True, help="赛马目录")
    retry.add_argument("--max-attempts", type=int, default=1, help="本次重试次数，默认 1")
    retry.set_defaults(func=cmd_retry)

    review = sub.add_parser("review", help="逐份评审 A/B 草案并整合成设计规格")
    review.add_argument("--dir", required=True, help="赛马目录")
    review.add_argument("--json", action="store_true", help="输出 JSON")
    review.set_defaults(func=cmd_review)

    audit = sub.add_parser("audit", help="对规格做覆盖、冲突、验收自审查")
    audit.add_argument("--dir", required=True, help="规格目录")
    audit.add_argument("--json", action="store_true", help="输出 JSON")
    audit.set_defaults(func=cmd_audit)

    export = sub.add_parser("export", help="导出同版规格 JSON 与 Markdown")
    export.add_argument("--dir", required=True, help="规格目录")
    export.add_argument("--json", action="store_true", help="输出 JSON")
    export.set_defaults(func=cmd_export)

    revise = sub.add_parser("revise", help="按自审查问题做范围裁剪并复核")
    revise.add_argument("--dir", required=True, help="规格目录")
    revise.set_defaults(func=cmd_revise)

    escalate = sub.add_parser("escalate", help="为无法自动解决的冲突生成人工问题")
    escalate.add_argument("--dir", required=True, help="规格目录")
    escalate.set_defaults(func=cmd_escalate)

    trace = sub.add_parser("trace", help="查看某个快照条目被哪些草案引用")
    trace.add_argument("--dir", required=True, help="交付目录")
    trace.add_argument("--item", required=True, help="快照条目编号，例如 R-03")
    trace.set_defaults(func=cmd_trace)

    case = sub.add_parser("case", help="跑固定案例，展示 A/B 的实际取舍")
    case.add_argument("--file", default="cases/campus-books.md", help="案例文件")
    case.add_argument("--out", default="runs/case-campus-books", help="产物目录")
    case.add_argument("--confirm", default="案例演示", help="快照确认人")
    case.add_argument("--json", action="store_true", help="输出取舍 JSON")
    case.set_defaults(func=cmd_case)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "snapshot" and not args.idea and not args.file:
        print("请提供输入文本或 --file", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except ContractError as error:
        print(f"失败：{error.code} {error.message}", file=sys.stderr)
        if error.path:
            print(f"位置：{error.path}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"读取失败：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
