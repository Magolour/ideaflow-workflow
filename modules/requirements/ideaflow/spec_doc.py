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

    summary = next((row["text"] for row in snapshot.requirements), _title(snapshot))
    parts += ["## 1 背景与问题定义", ""]
    parts += [f"输入想法：{summary}", "",
              "原始描述只给出目标，缺少可直接开发的边界、验收与取舍；"
              "本规格把讨论结论固化为可执行的设计基线。", ""]

    parts += ["## 2 目标用户与使用场景", ""]
    users = [f"- {row['text']}" for row in snapshot.requirements[:4]]
    parts += users or ["- 使用原始需求描述中的直接用户。"]
    parts += ["", "场景：用户从触发需求到完成目标的主路径，对应第 4 章需求与第 10 章验收。", ""]

    parts += ["## 3 项目目标与非目标", ""]
    parts += [f"- 目标：交付 {len(spec.modules)} 个模块，覆盖 {len(snapshot.requirements)} 条明确需求。"]
    parts += [f"- 非目标：{row['item']}（{row['reason']}）" for row in spec.non_goals] or ["- 非目标：未记录。"]
    parts += [""]

    parts += ["## 4 功能需求与优先级", ""]
    rows = []
    for requirement in snapshot.requirements:
        rid = requirement["id"]
        decision = decision_by_req.get(rid, {})
        module = module_by_req.get(rid, {})
        acceptance = acceptance_by_req.get(rid, {})
        rows.append([
            rid, requirement["text"], requirement.get("priority", "—"),
            decision.get("id", "—"), module.get("id", "—"),
            acceptance.get("id", "—"),
            SATISFIES_LABEL.get(decision.get("satisfies", ""), "—"),
        ])
    parts += [_table(["需求", "内容", "优先级", "决策", "模块", "验收", "满足程度"], rows), ""]

    parts += ["## 5 业务流程与业务规则", ""]
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
    parts += ["业务规则：每条决策的选择即为该需求的实现规则；"
              "未采纳方案见第 3 章非目标，未决问题见第 11 章。", ""]

    parts += ["## 6 总体系统架构", ""]
    parts += [spec.architecture, ""]

    parts += ["## 7 功能模块及职责", ""]
    parts += [_table(["模块", "职责", "需求", "估算"],
                     [[row["id"], row["responsibility"],
                       "、".join(row["requirement_ids"]) or "未映射",
                       row.get("estimate_days") if row.get("estimate_days") is not None else "待补"]
                      for row in spec.modules]), ""]

    parts += ["## 8 模块接口、输入输出与数据结构", ""]
    for module in spec.modules:
        estimate = module.get("estimate_days")
        estimate_text = (f"估算 {estimate} 天" if isinstance(estimate, (int, float))
                         else "估算待补")
        parts += [f"### {module['id']} {module['name']}", "",
                  f"对应需求：{'、'.join(module['requirement_ids'])} ｜ "
                  f"决策：{'、'.join(module['decision_ids'])} ｜ "
                  f"{estimate_text}", "",
                  f"职责：{module['responsibility']}", ""]
        parts += [_table(["接口", "调用方", "入参", "出参"],
                         [[row["name"], row["caller"], row["input"], row["output"]]
                          for row in module["interfaces"]])]
    parts += ["数据结构：模块间只传结构化对象；接口统一返回 result | error，"
              "错误对象包含 code / message / path。", ""]

    parts += ["## 9 非功能需求与技术约束", ""]
    nfr = [f"- {row['text']}" for row in snapshot.constraints]
    parts += nfr or ["- 未声明额外非功能约束。"]
    parts += ["- 单次运行不写入未知估算，缺失值保留为 null 并进入待确认。", ""]

    parts += ["## 10 可验证的验收标准", ""]
    parts += [_table(["编号", "需求", "验收条件", "验证方式"],
                     [[row["id"], row["requirement_id"], row["criterion"], row["verify"]]
                      for row in spec.acceptance])]

    parts += ["## 11 风险、估算与未决问题", ""]
    parts += ["### 11.1 风险与未决问题", ""]
    parts += [_table(["编号", "问题", "影响", "是否阻断", "候选"],
                     [[row["id"], row["question"], "、".join(row["impact"]),
                       "阻断" if row["blocking"] else "可延后",
                       "；".join(f"{item['draft']}：{item['choice']}"
                                 for item in row["candidates"])]
                      for row in spec.open_items])]
    parts += ["### 11.2 估算", ""]
    parts += [f"- 期望 {spec.effort['expected_total']} 天，"
              f"标准差 {spec.effort['deviation_total']} 天",
              f"- 对外承诺 {spec.effort['commit_days']} 天（期望 + 1σ）", ""]
    parts += [_table(["工作包", "期望", "标准差"],
                     [[row["name"], row["expected"], row["deviation"]]
                      for row in spec.effort["rows"]])]

    if spec.escalation:
        question = spec.escalation
        parts += ["### 11.3 需人工决策", ""]
        parts += [f"#### {question['id']} {question['question']}", ""]
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

    parts += ["## 12 用户需求清单", ""]
    parts += [_table(["编号", "用户需求", "优先级", "类型", "来源"],
                     [[row["id"], row["text"], row.get("priority", "—"),
                       "非功能" if row["type"] == "constraint" else "功能",
                       (row.get("source") or {}).get("section", "—")]
                      for row in snapshot.items if row["type"] in ("requirement", "constraint")]), ""]

    parts += ["## 13 数据结构设计", ""]
    data_rows = []
    for module in spec.modules:
        for interface in module.get("interfaces") or []:
            data_rows.append([module["id"], interface.get("name", "—"),
                              interface.get("input", "—"), interface.get("output", "—")])
    parts += [_table(["模块", "接口/对象", "输入", "输出"], data_rows), ""]
    parts += ["对象约定：跨模块只传结构化对象；错误统一返回 `{code, message, path}`，"
              "前端按 `code` 展示可恢复错误，不解析自然语言。", ""]

    parts += ["## 14 API 接口设计", ""]
    api_rows = []
    for module in spec.modules:
        for interface in module.get("interfaces") or []:
            api_rows.append([interface.get("name", "—"), module["id"],
                             interface.get("caller", "—"), interface.get("input", "—"),
                             interface.get("output", "—")])
    parts += [_table(["接口", "所属模块", "调用方", "入参", "出参"], api_rows), ""]
    parts += ["接口为内部应用层契约；对外 HTTP 接口以运行目录的 `manifest.json` 与"
              "编排器契约为准。", ""]

    parts += ["## 15 核心业务流程", ""]
    by_id = {module["id"]: module for module in spec.modules}
    for module in spec.modules:
        deps = module.get("depends_on") or []
        if deps:
            path = " → ".join([by_id[dep]["name"] for dep in deps if dep in by_id]
                              + [module["name"]])
            parts += [f"- {path}"]
        else:
            parts += [f"- {module['name']}（入口模块）"]
    parts += ["", "以上依赖来自功能间最小依赖推断；没有推断出依赖时保留为空并进入待确认。", ""]

    parts += ["## 16 异常处理", ""]
    exception_rows = []
    for decision in spec.decisions:
        if decision.get("satisfies") != "full":
            exception_rows.append([decision["id"], decision.get("scope", "—"),
                                   SATISFIES_LABEL.get(decision["satisfies"], "—"),
                                   decision.get("choice", "—")])
    parts += [_table(["决策", "范围", "状态", "当前处理"], exception_rows),
              "阻断与待确认项见第 11 章；接口错误统一用 code/message/path 返回，"
              "前端提供重试与返回入口。", ""]

    parts += ["## 17 安全与权限设计", ""]
    security = [row["text"] for row in snapshot.constraints
                if any(word in row["text"] for word in ("安全", "权限", "合规", "隐私", "审核"))]
    if security:
        parts += [f"- {text}" for text in security]
    else:
        parts += ["- 原始输入未声明安全、权限或合规约束；本规格不凭空补充指标，"
                  "相关项保留为待确认。"]
    parts += ["- 模型配置只保存在本次会话内存中；API Key 不写入运行产物。", ""]

    parts += ["## 18 测试与验收计划", ""]
    parts += [_table(["验收编号", "需求", "判据", "验证方式", "状态"],
                     [[row["id"], row["requirement_id"], row["criterion"], row["verify"],
                       "待执行" if spec.status != "confirmed" else "待执行（规格已确认）"]
                      for row in spec.acceptance]), ""]
    parts += ["执行顺序：接口契约测试 → 核心流程走查 → 异常路径 → 非功能指标；"
              "任一阻断项未关闭前不进入开发排期。", ""]

    parts += ["## 附录 A 需求追溯矩阵", ""]
    rows = []
    for requirement in snapshot.requirements:
        rid = requirement["id"]
        decision = decision_by_req.get(rid, {})
        module = module_by_req.get(rid, {})
        acceptance = acceptance_by_req.get(rid, {})
        rows.append([rid, requirement["text"], decision.get("id", "—"),
                     module.get("id", "—"), acceptance.get("id", "—")])
    parts += [_table(["需求", "内容", "决策", "模块", "验收"], rows), ""]

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
