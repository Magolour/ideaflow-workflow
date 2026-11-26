"""规格导出：同一版本产出 JSON 与 Markdown，两者指向同一个内容哈希。

Markdown 给人读，JSON 给下游消费；两者都带 `spec_id + version + hash`，
`verify_export` 可以随时核对它们是否仍是同一版。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import ContractError
from .drafts import SATISFIES_LABEL
from .snapshot import Snapshot
from .spec import Spec
from .storage import content_hash, read_json, write_json, write_text


def _cell(value: Any) -> str:
    return str(value).replace("|", "／").replace("\n", " ").strip()


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "（无）\n"
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(_cell(item) for item in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def _title(snapshot: Snapshot) -> str:
    for line in snapshot.raw_text.splitlines():
        text = line.strip().lstrip("#").strip()
        if text and not text.startswith("约束") and len(text) > 3:
            return text[:40]
    return "设计规格"


def render_spec_markdown(spec: Spec, snapshot: Snapshot) -> str:
    """生成规格正文。内容只来自规格与快照，不含时间戳，保证可复现。"""
    decision_by_req = {rid: row for row in spec.decisions
                       for rid in (row.get("requirement_ids") or [])}
    module_by_req = {rid: row for row in spec.modules
                     for rid in (row.get("requirement_ids") or [])}
    acceptance_by_req = {row["requirement_id"]: row for row in spec.acceptance}

    parts: list[str] = []
    parts += [f"# 设计规格：{_title(snapshot)}", ""]
    parts += [f"> 规格 `{spec.spec_id}` ｜ 版本 {spec.version} ｜ "
              f"内容哈希 `{spec.hash[:16]}…` ｜ 基线草案 {spec.base_draft}",
              ">",
              f"> 需求快照 `{spec.snapshot_id}` v{spec.snapshot_version}"
              f"（`{spec.snapshot_hash[:16]}…`）",
              ">",
              f"> 同版机读文件：`spec.v{spec.version}.json`"]
    if spec.escalation:
        parts += [">", f"> ⚠ 存在未决人工问题 `{spec.escalation['id']}`，"
                       f"确认前不能作为开发依据。"]
    if spec.review:
        review = spec.review
        counts = review["audit"]["counts"]
        label = {"approved": "通过", "awaiting_human": "待人工决策", "blocked": "有阻断"}[review["status"]]
        parts += [">", f"> 规格审查：{label} ｜ 阻断 {counts['blocking']} 条 ｜ "
                       f"审查记录 `audit.v{spec.version}.json`"]
    parts += [""]

    parts += ["## 1 概述", "", spec.architecture, ""]
    parts += [f"本规格以 {spec.base_draft} 为基线，整合两份草案后共 "
              f"{len(spec.decisions)} 条决策、{len(spec.modules)} 个模块；"
              f"未采纳 {len(spec.not_adopted)} 项差异，保留 "
              f"{len(spec.open_items)} 个待决问题。", ""]

    parts += ["## 2 需求追溯", ""]
    rows = []
    for requirement in snapshot.requirements:
        rid = requirement["id"]
        decision = decision_by_req.get(rid, {})
        module = module_by_req.get(rid, {})
        acceptance = acceptance_by_req.get(rid, {})
        rows.append([
            rid, requirement["text"],
            decision.get("id", "—"), module.get("id", "—"),
            acceptance.get("id", "—"),
            SATISFIES_LABEL.get(decision.get("satisfies", ""), "—"),
        ])
    parts += [_table(["需求", "内容", "决策", "模块", "验收", "满足程度"], rows), ""]

    parts += ["## 3 设计决策", ""]
    for decision in spec.decisions:
        requirement_id = (decision["requirement_ids"][0]
                          if decision.get("requirement_ids") else "未映射需求")
        parts += [f"### {decision['id']} · {requirement_id}", ""]
        parts += [f"- **选择**：{decision['choice']}",
                  f"- **理由**：{decision['reason']}",
                  f"- **来源**：{'、'.join(decision['draft_ids'])}"
                  + (f" ｜ 主张 {'、'.join(decision['claim_ids'])}"
                     if decision["claim_ids"] else ""),
                  f"- **满足程度**：{SATISFIES_LABEL[decision['satisfies']]}" +
                  ("（待人工确认）" if decision["needs_human"] else ""), ""]

    parts += ["## 4 模块与接口", ""]
    for module in spec.modules:
        parts += [f"### {module['id']} {module['name']}", "",
                  f"对应需求：{'、'.join(module['requirement_ids'])} ｜ "
                  f"决策：{'、'.join(module['decision_ids'])} ｜ "
                  f"估算 {module['estimate_days']} 天", "",
                  f"职责：{module['responsibility']}", ""]
        parts += [_table(["接口", "调用方", "入参", "出参"],
                         [[row["name"], row["caller"], row["input"], row["output"]]
                          for row in module["interfaces"]])]

    parts += ["## 5 验收条件", ""]
    parts += [_table(["编号", "需求", "验收条件", "验证方式"],
                     [[row["id"], row["requirement_id"], row["criterion"], row["verify"]]
                      for row in spec.acceptance])]

    parts += ["## 6 明确不做", ""]
    parts += [_table(["需求", "被否方案", "理由"],
                     [[row["item"], row.get("choice", "—"), row["reason"]]
                      for row in spec.non_goals])]

    parts += ["## 7 待决问题", ""]
    parts += [_table(["编号", "问题", "影响", "是否阻断", "候选"],
                     [[row["id"], row["question"], "、".join(row["impact"]),
                       "阻断" if row["blocking"] else "可延后",
                       "；".join(f"{item['draft']}：{item['choice']}"
                                 for item in row["candidates"])]
                      for row in spec.open_items])]

    parts += ["## 8 估算", ""]
    parts += [f"- 期望 {spec.effort['expected_total']} 天，"
              f"标准差 {spec.effort['deviation_total']} 天",
              f"- 对外承诺 {spec.effort['commit_days']} 天（期望 + 1σ）", ""]
    parts += [_table(["工作包", "期望", "标准差"],
                     [[row["name"], row["expected"], row["deviation"]]
                      for row in spec.effort["rows"]])]

    if spec.escalation:
        question = spec.escalation
        parts += ["## 9 需人工决策", ""]
        parts += [f"### {question['id']} {question['question']}", ""]
        parts += ["**背景**", ""]
        parts += [f"- {line}" for line in question["background"]]
        parts += [""]
        parts += [_table(["选项", "方案", "代价（天）", "说明"],
                         [[row["key"], row["text"], row["cost_days"], row["consequence"]]
                          for row in question["options"]])]
        parts += [f"- **影响范围**：需求 {'、'.join(question['affected']['requirements']) or '—'}"
                  f" ｜ 约束 {'、'.join(question['affected']['constraints']) or '—'}",
                  f"- **不决策的后果**：{question['if_undecided']}",
                  f"- **建议**：{question['recommended']}（仅建议，不代替决定）", ""]

    parts += ["## 附录 版本信息", ""]
    parts += [_table(["项目", "值"], [
        ["规格", f"{spec.spec_id} v{spec.version}"],
        ["内容哈希", f"`{spec.hash}`"],
        ["需求快照", f"{spec.snapshot_id} v{spec.snapshot_version} `{spec.snapshot_hash}`"],
        ["同版文件", f"`spec.v{spec.version}.json` ｜ `spec.v{spec.version}.md`"],
        ["评审记录", f"`{spec.review_ref['file']}`"],
    ])]
    return "\n".join(parts).rstrip() + "\n"


def export_spec(spec: Spec, snapshot: Snapshot, directory: str | Path,
                *, publish: bool = True) -> dict[str, Any]:
    """写出同版 JSON 与 Markdown，并返回可核对的摘要。

    `spec.v{n}.json` / `spec.json` 只保留 spec 契约内的字段；导出元数据
    （artifacts）单独写到 `spec.bundle.json`，避免污染下游消费的规格文件。
    """
    root = Path(directory)
    json_name = f"spec.v{spec.version}.json"
    md_name = f"spec.v{spec.version}.md"
    bundle_name = "spec.bundle.json"
    markdown = render_spec_markdown(spec, snapshot)

    pure = spec.to_json()
    bundle = {**pure,
              "artifacts": {"json": json_name, "markdown": md_name,
                            "bundle": bundle_name, "content_hash": spec.hash}}
    write_json(root / json_name, pure)
    write_text(root / md_name, markdown)
    if publish:
        write_text(root / "spec.md", markdown)
        write_json(root / bundle_name, bundle)
        write_json(root / "spec.json", pure)
    return {
        "spec_id": spec.spec_id,
        "version": spec.version,
        "content_hash": spec.hash,
        "json": json_name,
        "markdown": md_name,
        "bundle": bundle_name,
        "json_sha256": content_hash(pure),
        "markdown_sha256": content_hash(markdown),
        "markdown_text": markdown,
    }


def verify_export(directory: str | Path, version: int) -> dict[str, Any]:
    """核对 JSON 与 Markdown 是否同版：版本、哈希、互相引用。"""
    root = Path(directory)
    json_path = root / f"spec.v{version}.json"
    md_path = root / f"spec.v{version}.md"
    if not json_path.exists() or not md_path.exists():
        raise ContractError("EXPORT_NOT_FOUND", "找不到同版导出文件", "export")

    payload = read_json(json_path)
    bundle_path = root / "spec.bundle.json"
    bundle = read_json(bundle_path) if bundle_path.exists() else payload
    markdown = md_path.read_text(encoding="utf-8")
    checks: dict[str, bool] = {}
    try:
        spec = Spec.from_json(payload)
        checks["json_integrity"] = True
    except ContractError:
        spec = None
        checks["json_integrity"] = False

    digest = payload.get("hash", "")
    artifacts = bundle.get("artifacts", {})
    checks["version_matches"] = payload.get("version") == version
    checks["markdown_matches_version"] = f"版本 {version} ｜" in markdown
    checks["artifacts_link"] = (artifacts.get("markdown") == md_path.name
                                and artifacts.get("json") == json_path.name
                                and (bundle_path.exists() or bool(artifacts)))
    checks["hash_in_markdown"] = bool(digest) and f"`{digest}`" in markdown
    checks["markdown_links_json"] = f"`spec.v{version}.json`" in markdown
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "version": version,
        "content_hash": digest,
        "json": json_path.name,
        "markdown": md_path.name,
        "bundle": bundle_path.name if bundle_path.exists() else "",
        "spec": spec,
    }
