"""检查一次需求运行的规格质量，面向真实模型产物。

用法：
    python eval/check_spec_quality.py modules/requirements/output/real-check2

检查六项：文件契约、功能到需求的映射、审计覆盖率分母、验收条件、估时、未决项噪声。
FAIL 表示本周验收口径不成立；WARN 表示质量风险。
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "contracts" / "v2"


def latest_run(path: Path) -> Path:
    if (path / "spec.json").exists() or (path / "spec.v1.json").exists():
        return path
    children = sorted(child for child in path.iterdir() if child.is_dir()) if path.exists() else []
    if not children:
        raise SystemExit(f"没有找到运行目录：{path}")
    return children[-1]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def inline(node, by_file):
    if isinstance(node, dict):
        if "$ref" in node:
            name, _, fragment = node["$ref"].partition("#")
            target = by_file[name]
            for part in [p for p in fragment.split("/") if p]:
                target = target[part]
            return inline(target, by_file)
        return {key: inline(value, by_file) for key, value in node.items()}
    if isinstance(node, list):
        return [inline(item, by_file) for item in node]
    return node


def check_contract(spec: dict) -> None:
    files = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in V2.glob("*.json")}
    validator = Draft202012Validator(inline(files["spec.schema.json"], files))
    errors = sorted(validator.iter_errors(spec), key=str)
    if errors:
        first = errors[0]
        report("文件契约", "FAIL", f"{len(errors)} 处：{list(first.absolute_path)} {first.message}")
    else:
        report("文件契约", "PASS", "spec 磁盘产物符合 contracts/v2")


def check_mapping(spec: dict) -> None:
    modules = spec.get("modules") or []
    empty = [m["id"] for m in modules if not m.get("requirement_ids")]
    if modules and len(empty) == len(modules):
        report("功能到需求映射", "FAIL", f"{len(empty)}/{len(modules)} 个模块没有需求引用")
    elif empty:
        report("功能到需求映射", "WARN", f"{len(empty)} 个模块没有需求引用：{empty[:5]}")
    else:
        report("功能到需求映射", "PASS", "所有模块都有需求引用")


def check_audit(spec: dict, audit: dict | None, features: list[dict]) -> None:
    if not audit:
        report("审计覆盖率", "WARN", "没有 audit 记录")
        return
    coverage = (audit.get("checks") or {}).get("coverage") or {}
    total = coverage.get("requirements")
    with_module = coverage.get("with_module")
    if total == 0 and features:
        report("审计覆盖率", "FAIL",
               f"分母为 0，但功能有 {len(features)} 条，审计等于真空通过")
    elif total and with_module != total:
        report("审计覆盖率", "FAIL", f"覆盖 {with_module}/{total}")
    else:
        report("审计覆盖率", "PASS", f"覆盖 {with_module}/{total}")


def check_acceptance(spec: dict) -> None:
    rows = spec.get("acceptance") or []
    if not rows:
        report("验收条件", "FAIL", "没有验收条目")
        return
    empty_ids = [row for row in rows if not row.get("requirement_id")]
    duplicates = [text for text, count in Counter(row.get("criterion") for row in rows).items()
                  if count > 1]
    if empty_ids:
        report("验收条件", "FAIL", f"{len(empty_ids)} 条没有 requirement_id")
    elif duplicates:
        report("验收条件", "WARN", f"{len(duplicates)} 条重复文案：{duplicates[:2]}")
    else:
        report("验收条件", "PASS", f"{len(rows)} 条均有归属且文案不重复")


def check_estimates(spec: dict) -> None:
    values = [m.get("estimate_days") for m in spec.get("modules") or []]
    if not values:
        report("估时", "WARN", "没有估时字段")
        return
    if len(set(values)) == 1 and len(values) > 1:
        report("估时", "WARN", f"所有模块估时相同（{values[0]}），疑似默认值")
    else:
        report("估时", "PASS", f"{len(set(values))} 种取值")


def check_open_items(spec: dict) -> None:
    rows = spec.get("open_items") or []
    if not rows:
        report("未决项", "PASS", "没有未决项")
        return
    questions = [row.get("question") or "" for row in rows]
    if len(rows) >= 5 and len(set(questions)) < len(rows) * 0.5:
        report("未决项", "WARN", f"{len(rows)} 条中大量重复，建议合并")
    elif len(rows) > 10:
        report("未决项", "WARN", f"{len(rows)} 条过多，建议按主题合并到 10 条以内")
    else:
        report("未决项", "PASS", f"{len(rows)} 条")


RESULTS = []


def report(name: str, level: str, detail: str) -> None:
    RESULTS.append(level)
    print(f"{level:<4} {name}：{detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description="检查需求运行规格质量")
    parser.add_argument("run", type=Path)
    args = parser.parse_args()

    root = latest_run(args.run)
    spec = load(root / "spec.v1.json") or load(root / "spec.json")
    if not spec:
        raise SystemExit(f"{root} 下没有 spec 产物")
    audit = load(root / "audit.v1.json") or load(root / "audit.json")
    features = (load(root / "features.json") or {}).get("features") or []

    print(f"运行目录：{root}\n")
    check_contract(spec)
    check_mapping(spec)
    check_audit(spec, audit, features)
    check_acceptance(spec)
    check_estimates(spec)
    check_open_items(spec)

    print()
    if "FAIL" in RESULTS:
        print("结论：存在 FAIL 项，本周验收口径不成立。")
        return 1
    if "WARN" in RESULTS:
        print("结论：可用，但有质量风险。")
        return 0
    print("结论：全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
