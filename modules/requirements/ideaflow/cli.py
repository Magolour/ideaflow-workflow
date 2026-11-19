"""命令行入口：一句想法生成结构化提案。

    python -m ideaflow.cli "做一个校园二手书交易小程序" --out runs
    python -m ideaflow.cli "…" --answer "Q-001=由产品负责人验收" --json
"""

from __future__ import annotations

import argparse
import json
import sys

from .contracts import ContractError
from .pipeline import run


def _pairs(items: list[str], flag: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"{flag} 需要 键=值 形式，收到：{item}")
        key, _, value = item.partition("=")
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise SystemExit(f"{flag} 的键和值都不能为空：{item}")
        result[key] = value
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ideaflow",
        description="把一句想法变成含讨论记录、候选方案与估算的结构化设计提案（默认离线运行）")
    parser.add_argument("idea", help="一句话想法")
    parser.add_argument("-c", "--constraint", action="append", default=[],
                        help="已知约束，可重复，例如 -c '两周内可用'")
    parser.add_argument("-o", "--out", default="runs", help="输出目录，默认 runs/")
    parser.add_argument("--answer", action="append", default=[], metavar="Q-001=答复",
                        help="回答首轮澄清问题，可重复；未回答的问题按假设继续")
    parser.add_argument("--resolve", action="append", default=[], metavar="OBJ-001=裁决",
                        help="由人裁决某条异议，可重复")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果摘要")
    parser.add_argument("--quiet", action="store_true", help="不打印阶段进度")
    parser.add_argument("--max-rounds", type=int, default=2,
                        help="讨论轮次上限，默认 2")
    parser.add_argument("--threshold", type=float, default=0.7,
                        help="判定共识所需的最低支持度，默认 0.7")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    answers = _pairs(args.answer, "--answer")
    resolutions = _pairs(args.resolve, "--resolve")

    progress = None if args.quiet else (lambda message: print(message, flush=True))
    try:
        result = run(args.idea, constraints=args.constraint, out_dir=args.out,
                     answers=answers, resolutions=resolutions,
                     max_rounds=args.max_rounds, threshold=args.threshold,
                     progress=progress)
    except ContractError as error:
        print(f"生成失败：{error.code} {error.message}", file=sys.stderr)
        if error.path:
            print(f"位置：{error.path}", file=sys.stderr)
        return 2

    report = result.summary()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    counts = report["counts"]
    print()
    print(f"运行完成：{report['out_dir']}")
    print(f"主张 {counts['claims']} 条｜假设 {counts['assumptions']} 条｜"
          f"异议 {counts['objections']} 条（阻断 {counts['blocking']}）｜"
          f"未决问题 {counts['open_questions']} 个")
    print(f"共识状态：{report['consensus']['label']}（{report['consensus']['reason']}）")
    termination = report.get("termination", {})
    if termination:
        print(f"终止条件：{termination['label']}｜支持度 {termination['support']:.0%}")
    print(f"提案文件：{report['proposal']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
