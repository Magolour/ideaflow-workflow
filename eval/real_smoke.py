"""真实调用冒烟：需求模块从一句话跑到设计规格。

回答一个问题：**需求模块的真实产出，能不能过公共契约，引用是否成立。**

用法：
    python eval/real_smoke.py                    # 跑默认案例并校验
    python eval/real_smoke.py --case device-booking
    python eval/real_smoke.py --require-real     # 仍是 Mock Agent 时视为未就绪（退出码 2）

退出码：0 契约通过；1 契约失败；2 未就绪（缺真实 Agent 或调用失败）。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "requirements"))

from jsonschema import Draft202012Validator  # noqa: E402

V2 = ROOT / "contracts" / "v2"


def load_schemas() -> dict:
    return {path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in V2.glob("*.json")}


def inline(node, by_file: dict):
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


def validator(name: str, by_file: dict) -> Draft202012Validator:
    return Draft202012Validator(inline(by_file[name], by_file))


def real_agents() -> list[str]:
    """需求模块里除了 MockAgent 之外，是否已有真实模型实现。"""
    import ideaflow.agents as agents_module
    return [name for name, value in vars(agents_module).items()
            if isinstance(value, type)
            and value.__module__ == agents_module.__name__
            and name != "MockAgent"
            and not getattr(value, "_is_protocol", False)]


def check(case_name: str, agent=None, discuss: bool = False,
          out_dir: str | Path | None = None) -> dict:
    from ideaflow.specflow import load_case, run as run_spec  # noqa: WPS433

    by_file = load_schemas()
    features = None
    if out_dir is None:
        keeper = tempfile.TemporaryDirectory()
    else:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        keeper = contextlib.nullcontext(str(out_dir))
    with keeper as tmp:
        result = run_spec(load_case(case_name), out_dir=tmp, agent=agent,
                          discuss=discuss)
        calls = 0
        log = result.out_dir / "ai_calls.jsonl"
        if log.exists():
            calls = len([line for line in log.read_text(encoding="utf-8").splitlines()
                         if line.strip()])
        features_path = result.out_dir / "features.json"
        if features_path.exists():
            features = json.loads(features_path.read_text(encoding="utf-8"))
    snapshot = result.snapshot.to_json()
    spec = result.spec.to_json()

    errors = []
    for name, payload in (("snapshot.schema.json", snapshot),
                          ("spec.schema.json", spec)):
        errors += [f"{name}: {list(error.absolute_path)} {error.message}"
                   for error in sorted(validator(name, by_file).iter_errors(payload),
                                       key=str)]
    if features is not None:
        errors += [f"features.schema.json: {list(error.absolute_path)} {error.message}"
                   for error in sorted(validator("features.schema.json",
                                                 by_file).iter_errors(features),
                                       key=str)]
        for word in ("问题定义", "关键路径", "全部范围", "结构边界", "剩余预算"):
            if any(word in row.get("name", "") for row in features.get("features", [])):
                errors.append(f"功能名仍是过程约束：{word}")
    elif discuss:
        errors.append("缺少 features.json")

    known = {item["id"] for item in snapshot["items"]}
    for module in spec["modules"]:
        missing = [ref for ref in module.get("requirement_ids") or [] if ref not in known]
        if missing:
            errors.append(f"模块 {module.get('id')} 引用了快照中不存在的条目：{missing}")
    snapshot_ref = spec.get("snapshot_ref") or {}
    if snapshot_ref.get("snapshot_id") != snapshot.get("snapshot_id"):
        errors.append("spec.snapshot_ref 与快照编号不一致")

    return {
        "case": case_name,
        "out_dir": str(result.out_dir),
        "status": spec.get("status"),
        "requirements": len(snapshot["items"]),
        "features": len(features.get("features") or []) if features else 0,
        "modules": len(spec["modules"]),
        "errors": errors,
        "real_agents": real_agents(),
        "agent": getattr(agent, "name", "mock"),
        "ai_calls": calls,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="需求模块真实调用冒烟")
    parser.add_argument("--case", default="campus-books")
    parser.add_argument("--out", type=Path,
                        help="保留本次运行产物的目录；不传则跑完即删")
    parser.add_argument("--require-real", action="store_true",
                        help="必须真实调用模型；缺少配置或没有调用记录时返回 2")
    args = parser.parse_args()

    agent = None
    if args.require_real:
        from ideaflow.agents import LLMAgent  # noqa: WPS433
        from ideaflow.contracts import ContractError  # noqa: WPS433

        try:
            agent = LLMAgent(timeout=float(os.getenv("AI_TIMEOUT", "120")),
                             retries=int(os.getenv("AI_RETRIES", "2")))
        except ContractError as error:
            print(f"未就绪：{error.code} {error.message}")
            return 2

    try:
        result = check(args.case, agent=agent, discuss=args.require_real,
                       out_dir=args.out)
    except Exception as exc:  # 调用失败、案例不存在、契约异常
        traceback.print_exc()
        print(f"运行失败：{type(exc).__name__}: {exc}")
        return 2

    print(f"案例 {result['case']}：快照 {result['requirements']} 项，"
          f"功能 {result['features']} 条，模块 {result['modules']} 个，"
          f"规格状态 {result['status']}")
    print(f"模型：{result['agent']} ｜ 真实调用 {result['ai_calls']} 次"
          f" ｜ 讨论 {'开启' if args.require_real else '关闭'}")
    if args.out:
        print(f"运行目录：{result['out_dir']}")

    if result["errors"]:
        print("契约校验：失败")
        for error in result["errors"][:12]:
            print("  -", error)
        return 1

    print("契约校验：通过")
    if args.require_real and result["ai_calls"] == 0:
        print("未就绪：没有产生真实模型调用记录")
        return 2
    if not args.require_real:
        print("提示：本次使用 MockAgent；加 --require-real 才会发起真实调用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
