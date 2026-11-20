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
import json
import sys
import tempfile
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


def check(case_name: str) -> dict:
    from ideaflow.specflow import load_case, run as run_spec  # noqa: WPS433

    by_file = load_schemas()
    with tempfile.TemporaryDirectory() as tmp:
        result = run_spec(load_case(case_name), out_dir=tmp)
    snapshot = result.snapshot.to_json()
    spec = result.spec.to_json()

    errors = []
    for name, payload in (("snapshot.schema.json", snapshot),
                          ("spec.schema.json", spec)):
        errors += [f"{name}: {list(error.absolute_path)} {error.message}"
                   for error in sorted(validator(name, by_file).iter_errors(payload),
                                       key=str)]

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
        "status": spec.get("status"),
        "requirements": len(snapshot["items"]),
        "modules": len(spec["modules"]),
        "errors": errors,
        "real_agents": real_agents(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="需求模块真实调用冒烟")
    parser.add_argument("--case", default="campus-books")
    parser.add_argument("--require-real", action="store_true",
                        help="需求模块仍是 MockAgent 时返回 2（用于验收门禁）")
    args = parser.parse_args()

    try:
        result = check(args.case)
    except Exception as exc:  # 调用失败、案例不存在、契约异常
        print(f"运行失败：{type(exc).__name__}: {exc}")
        return 2

    real = result["real_agents"]
    print(f"案例 {result['case']}：快照 {result['requirements']} 项，"
          f"模块 {result['modules']} 个，规格状态 {result['status']}")
    print("模型实现：" + ("、".join(real) if real else "仅 MockAgent（离线模板，未发起真实调用）"))

    if result["errors"]:
        print("契约校验：失败")
        for error in result["errors"][:12]:
            print("  -", error)
        return 1

    print("契约校验：通过")
    if not real:
        print("提示：需求模块尚未接入真实模型，本次产物来自 MockAgent；"
              "接入后再跑本脚本即为真实调用验证。")
        return 2 if args.require_real else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
