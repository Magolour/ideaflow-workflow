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

from .artifacts import deliver
from .contracts import ContractError
from .demo import run_case
from .drafts import GROUPS, SATISFIES_LABEL, Draft, run_groups
from .pipeline import slugify
from .snapshot import ITEM_TYPE_LABEL, PRIORITY_LABEL, Snapshot, build, load, save
from .storage import read_json


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


def cmd_race(args: argparse.Namespace) -> int:
    snapshot = load(args.dir)
    if snapshot.status != "confirmed":
        if not args.confirm:
            print(f"快照 {snapshot.snapshot_id} v{snapshot.version} 尚未确认；"
                  f"加 --confirm 姓名 确认后再出方案。", file=sys.stderr)
            return 2
        snapshot = snapshot.confirm(args.confirm)
        save(snapshot, args.dir)
    drafts = run_groups(snapshot)
    if args.json:
        print(json.dumps({group: draft.to_json() for group, draft in drafts.items()},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"两组草案 · 快照 {snapshot.snapshot_id} v{snapshot.version} · "
          f"hash {snapshot.hash[:12]}…")
    print("两组读同一份快照，各自独立出方案，互相不可见。\n")
    for group in ("A", "B"):
        _print_draft(drafts[group])

    manifest = deliver(args.dir, snapshot, drafts)
    print("已交付：")
    for name in manifest["files"]:
        print(f"  {name}")
    print(f"引用边 {manifest['citations']['edges']} 条 · "
          f"清单 {Path(args.dir) / 'manifest.json'}")
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
    race.add_argument("--json", action="store_true", help="输出 JSON")
    race.set_defaults(func=cmd_race)

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
