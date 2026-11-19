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

from .contracts import ContractError
from .pipeline import slugify
from .snapshot import ITEM_TYPE_LABEL, PRIORITY_LABEL, Snapshot, build, save


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
