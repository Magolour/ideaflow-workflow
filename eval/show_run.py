"""查看一次需求模块运行的产出，便于反复调提示词。

用法：
    python eval/show_run.py runs/feat-a
    python eval/show_run.py runs/feat-b --baseline runs/feat-a

读取 run 目录里的 features.json、spec.v*.json、snapshot*.json、ai_calls.jsonl，
打印功能清单、模块清单、待确认项与调用统计；--baseline 时对比两次运行。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def latest(pattern: str, root: Path) -> Path | None:
    files = sorted(root.glob(pattern))
    return files[-1] if files else None


def load(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def calls_summary(root: Path) -> dict:
    log = root / "ai_calls.jsonl"
    if not log.exists():
        return {"calls": 0, "tokens": 0, "latency_ms": 0}
    calls = tokens = latency = 0
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        calls += 1
        tokens += (row.get("prompt_tokens") or 0) + (row.get("completion_tokens") or 0)
        latency += row.get("latency_ms") or 0
    return {"calls": calls, "tokens": tokens, "latency_ms": latency}


def find_run(path: Path) -> Path:
    if (path / "flow.json").exists() or (path / "spec.json").exists():
        return path
    children = sorted(child for child in path.iterdir() if child.is_dir()) if path.exists() else []
    if children:
        return children[-1]          # 同一输出目录多次运行时，取最新一次
    return path


def summarize(root: Path) -> dict:
    root = find_run(root)
    features_doc = load(root / "features.json")
    spec_doc = load(latest("spec.v*.json", root)) or load(root / "spec.json")
    snapshot_doc = load(latest("snapshot.v*.json", root))
    return {
        "root": root,
        "features": (features_doc or {}).get("features") or [],
        "modules": (spec_doc or {}).get("modules") or [],
        "open_items": (spec_doc or {}).get("open_items") or [],
        "status": (spec_doc or {}).get("status", "-"),
        "requirements": len((snapshot_doc or {}).get("items") or []),
        "calls": calls_summary(root),
    }


def print_run(data: dict) -> None:
    print(f"运行目录：{data['root']}")
    print(f"快照条目 {data['requirements']} ｜ 功能 {len(data['features'])} ｜ "
          f"模块 {len(data['modules'])} ｜ 待确认 {len(data['open_items'])} ｜ "
          f"状态 {data['status']}")
    calls = data["calls"]
    print(f"模型调用 {calls['calls']} 次 ｜ token {calls['tokens']} ｜ "
          f"累计耗时 {calls['latency_ms'] / 1000:.1f}s")

    print("\n功能清单：")
    if not data["features"]:
        print("  （没有 features.json，说明归纳阶段还没实现或未开启讨论）")
    for row in data["features"]:
        print(f"  {row['id']} {row['name']} ｜ 来源 {','.join(row['source_refs'])}"
              f" ｜ 推断 {row['inferred']}")

    print("\n模块：")
    for row in data["modules"]:
        print(f"  {row['id']} {row['name']} ｜ 需求 {','.join(row['requirement_ids']) or '—'}")

    if data["open_items"]:
        print("\n待确认项：")
        for row in data["open_items"]:
            print(f"  {row['id']} {row['question']}")


def print_diff(current: dict, baseline: dict) -> None:
    def names(rows, key="name"):
        return [row[key] for row in rows]

    before_features, after_features = names(baseline["features"]), names(current["features"])
    before_modules, after_modules = names(baseline["modules"]), names(current["modules"])

    print("\n对比基线：")
    print(f"  功能 {len(before_features)} → {len(after_features)}")
    print(f"  模块 {len(before_modules)} → {len(after_modules)}")
    changed = [name for name in after_features if name not in before_features]
    if changed:
        print("  新增功能：" + "、".join(changed[:10]))
    same = len([name for name in after_features if name in before_features])
    print(f"  与基线同名功能：{same} 个")


def main() -> int:
    parser = argparse.ArgumentParser(description="查看需求模块运行产出")
    parser.add_argument("run", type=Path, help="run 目录")
    parser.add_argument("--baseline", type=Path, help="用于对比的旧 run 目录")
    args = parser.parse_args()

    if not args.run.exists():
        print(f"目录不存在：{args.run}")
        return 2

    current = summarize(args.run)
    print_run(current)
    if args.baseline and args.baseline.exists():
        print_diff(current, summarize(args.baseline))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
