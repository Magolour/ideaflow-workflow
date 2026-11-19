"""固定案例：快照 → 两组草案 → 取舍记录，一次跑完并可重复回归。

案例文件 `cases/campus-books.md` 刻意包含一条与约束冲突的需求
（在线付款 vs 首版不做在线支付），用来说明两组方案的分歧不是措辞差异，
而是“放弃需求换工期”与“保留需求但超期”的实际取舍。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import deliver
from .contracts import ContractError
from .drafts import Draft, run_groups
from .snapshot import Snapshot, build
from .tradeoffs import build_tradeoffs

DEFAULT_CASE = Path("cases/campus-books.md")
DEFAULT_OUT = Path("runs/case-campus-books")


@dataclass
class CaseResult:
    snapshot: Snapshot
    drafts: dict[str, Draft]
    tradeoffs: dict[str, Any]
    manifest: dict[str, Any]


def run_case(case_file: str | Path = DEFAULT_CASE, out_dir: str | Path = DEFAULT_OUT,
             *, confirm: str = "案例演示") -> CaseResult:
    path = Path(case_file)
    if not path.exists():
        raise ContractError("CASE_NOT_FOUND", f"找不到案例文件 {path}", "case_file")
    snapshot = build(path.read_text(encoding="utf-8")).confirm(confirm)
    drafts = run_groups(snapshot)
    tradeoffs = build_tradeoffs(snapshot, drafts)
    manifest = deliver(out_dir, snapshot, drafts, extras={"tradeoffs.json": tradeoffs})
    return CaseResult(snapshot=snapshot, drafts=drafts, tradeoffs=tradeoffs,
                      manifest=manifest)
