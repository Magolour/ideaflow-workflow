"""设计规格：把两份评审后的草案整合成可追溯、可审查的规格对象。

整合不是平均：以加权得分高的草案为基线，只采纳“不违反约束且在剩余预算内”
的差异项；每一条采纳或不采纳都写明理由，未满足的需求转为待决项。
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constraints import deadline_days, forbidden_subjects, shares_topic
from .contracts import ContractError
from .drafts import NEGATIONS, Draft
from .estimate import pert
from .snapshot import Snapshot
from .storage import content_hash, now_iso, read_json, write_json

SPEC_STATUS = ("draft", "awaiting_human", "confirmed", "blocked")
MAX_MODULES = 12

VALUE_ORDER = ("搜索", "支付", "发布", "收藏", "评价", "自提")
FEATURE_ALIASES = {"支付": ("支付", "付款", "结算"), "搜索": ("搜索", "检索"),
                   "发布": ("发布", "上传"), "收藏": ("收藏", "关注")}
FEATURE_KEYWORD_GROUPS: tuple[tuple[str, ...], ...] = (
    ("录入", "发布", "新建", "创建", "登记", "上传"),
    ("检索", "搜索", "查找", "查询", "筛选", "列表"),
    ("详情", "查看", "浏览", "预览"),
    ("提交", "审批", "审核", "通过", "驳回"),
    ("通知", "提醒", "消息", "推送"),
    ("统计", "导出", "报表", "汇总", "记录"),
    ("支付", "付款", "结算", "成交"),
    ("收藏", "关注", "订阅"),
    ("自提", "约见", "线下", "面交"),
    ("签到", "扫码"),
    ("报名", "组队", "团队", "协作"),
    ("时长", "认定"),
    ("活动",),
)

INTERFACES: dict[str, list[dict[str, str]]] = {
    "发布": [{"name": "publishBook", "caller": "发布页",
              "input": "{title, price, contact}", "output": "bookId"}],
    "搜索": [{"name": "searchBooks", "caller": "列表页",
              "input": "{keyword, sort}", "output": "Book[]"}],
    "支付": [{"name": "createTrade", "caller": "成交页",
              "input": "{bookId, method}", "output": "tradeId"}],
    "收藏": [{"name": "toggleFavorite", "caller": "列表页 / 详情页",
              "input": "{bookId}", "output": "{favorited}"}],
    "评价": [{"name": "submitReview", "caller": "成交完成页",
              "input": "{tradeId, score}", "output": "reviewId"}],
    "自提": [{"name": "reservePickup", "caller": "成交页",
              "input": "{tradeId, slot}", "output": "reservationId"}],
}


def feature_of(text: str) -> str | None:
    for key in VALUE_ORDER:
        words = FEATURE_ALIASES.get(key, (key,))
        if any(word in text for word in words):
            return key
    return None


def _module_name(text: str, index: int) -> str:
    """按需求正文生成模块名，不再依赖固定教材词表。"""
    clean = re.sub(r"^(支持|可以|能够|实现|提供|需要)", "", str(text)).strip(" 。；，,.")
    clean = re.sub(r"[，,。；;].*$", "", clean).strip()
    return clean[:12] or f"模块 {index}"


def _interfaces(text: str, index: int) -> list[dict[str, str]]:
    key = feature_of(text)
    if key and key in INTERFACES:
        return [dict(row) for row in INTERFACES[key]]
    return [{"name": f"handle{index:02d}", "caller": "应用层",
             "input": "{payload}", "output": "result | error"}]


def _conflict_constraint(choice: str, snapshot: Snapshot) -> dict[str, Any] | None:
    for item in snapshot.constraints:
        for subject in forbidden_subjects(item["text"]):
            if (shares_topic(subject, choice)
                    and not any(word in choice for word in NEGATIONS)):
                return item
    return None


def acceptance_for(decision: dict[str, Any], index: int) -> dict[str, Any]:
    if decision["satisfies"] == "full":
        criterion = (f"在 5 台测试设备上完成「{decision['scope']}」主流程，"
                     f"成功率 100%，单次操作不超过 3 步")
        verify = "手动走查 + 录屏"
    elif decision["satisfies"] == "declined":
        criterion = "线下成交后 24 小时内可在系统内补录成交记录，补录成功率 100%"
        verify = "手动走查 + 数据抽样"
    else:
        criterion = f"「{decision['scope']}」的 3 个核心路径可用，缺失能力登记为待决项"
        verify = "自动化用例 + 录屏"
    return {"id": f"AC-{index:02d}", "requirement_id": decision["requirement_ids"][0],
            "criterion": criterion, "verify": verify}


@dataclass
class Spec:
    spec_id: str
    version: int
    status: str
    snapshot_id: str
    snapshot_version: int
    snapshot_hash: str
    architecture: str
    base_draft: str
    decisions: list[dict[str, Any]]
    modules: list[dict[str, Any]]
    mapping: list[dict[str, Any]]
    acceptance: list[dict[str, Any]]
    non_goals: list[dict[str, Any]]
    open_items: list[dict[str, Any]]
    not_adopted: list[dict[str, Any]]
    effort: dict[str, Any]
    review_ref: dict[str, str]
    created_at: str = field(default_factory=now_iso)
    revisions: list[dict[str, Any]] = field(default_factory=list)
    escalation: dict[str, Any] | None = None
    review: dict[str, Any] | None = None

    @property
    def hash(self) -> str:
        return content_hash(self._content())

    @property
    def content_fingerprint(self) -> str:
        """忽略版本号的正文指纹，用于判断修订是否真的改了内容。"""
        content = self._content()
        content.pop("version", None)
        return content_hash(content)

    def _content(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "version": self.version,
            "snapshot_ref": {"snapshot_id": self.snapshot_id,
                             "version": self.snapshot_version,
                             "hash": self.snapshot_hash},
            "review_ref": self.review_ref,
            "architecture": self.architecture,
            "base_draft": self.base_draft,
            "decisions": self.decisions,
            "modules": self.modules,
            "mapping": self.mapping,
            "acceptance": self.acceptance,
            "non_goals": self.non_goals,
            "open_items": self.open_items,
            "not_adopted": self.not_adopted,
            "effort": self.effort,
        }

    @property
    def blocking_open_items(self) -> list[dict[str, Any]]:
        return [row for row in self.open_items if row["blocking"] and not row["resolution"]]

    def to_json(self) -> dict[str, Any]:
        payload = {**self._content(), "hash": self.hash, "status": self.status,
                   "created_at": self.created_at, "revisions": self.revisions,
                   "escalation": self.escalation}
        if self.review is not None:
            payload["review"] = self.review
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "Spec":
        try:
            spec = cls(
                spec_id=payload["spec_id"], version=int(payload["version"]),
                status=payload["status"],
                snapshot_id=payload["snapshot_ref"]["snapshot_id"],
                snapshot_version=int(payload["snapshot_ref"]["version"]),
                snapshot_hash=payload["snapshot_ref"]["hash"],
                architecture=payload["architecture"],
                base_draft=payload["base_draft"],
                decisions=payload["decisions"], modules=payload["modules"],
                mapping=payload["mapping"], acceptance=payload["acceptance"],
                non_goals=payload.get("non_goals", []),
                open_items=payload.get("open_items", []),
                not_adopted=payload.get("not_adopted", []),
                effort=payload["effort"], review_ref=payload["review_ref"],
                created_at=payload.get("created_at", now_iso()),
                revisions=list(payload.get("revisions", [])),
                escalation=payload.get("escalation"),
                review=payload.get("review"),
            )
        except KeyError as error:
            raise ContractError("INVALID_SPEC", f"规格缺少字段 {error}", "spec") from None
        if payload.get("hash") and payload["hash"] != spec.hash:
            raise ContractError("HASH_MISMATCH", "规格内容与记录的哈希不一致", "hash")
        return spec


def effort_for(modules: list[dict[str, Any]]) -> dict[str, Any]:
    """按模块估算重算 PERT；修订后必须走这里，避免手改总工期。"""
    packages = []
    missing = 0
    for module in modules:
        estimate = module.get("estimate_days")
        if not isinstance(estimate, (int, float)):
            missing += 1
            continue
        packages.append({"name": module["name"],
                         "optimistic": round(estimate * 0.8, 1),
                         "likely": estimate,
                         "pessimistic": round(estimate * 1.6, 1)})
    if missing:
        packages.append({"name": f"{missing} 个模块估算待补",
                         "optimistic": round(missing * 0.5, 1),
                         "likely": float(missing),
                         "pessimistic": round(missing * 2.0, 1)})
    packages.append({"name": "联调、验收与演示准备", "optimistic": 2.0,
                     "likely": 3.0, "pessimistic": 5.0})
    return pert(packages)


def build_spec(snapshot: Snapshot, drafts: dict[str, Draft],
               reviews: dict[str, Any],
               features: dict[str, Any] | None = None,
               claim_refs: dict[str, list[str]] | None = None) -> Spec:
    if len(drafts) < 2:
        raise ContractError("NEED_TWO_DRAFTS", "整合规格需要两份草案", "drafts")
    by_id = {draft.draft_id: draft for draft in drafts.values()}
    base_id = reviews["verdict"]["base_draft"]
    base = by_id[base_id]
    other = next(draft for draft in drafts.values() if draft.draft_id != base_id)
    if features is not None:
        return _spec_from_features(snapshot, features, reviews, base,
                                   claim_refs or {})
    if len(snapshot.requirements) > MAX_MODULES:
        raise ContractError("TOO_MANY_MODULES",
                            f"单份规格最多 {MAX_MODULES} 个模块", "requirements")

    deadline = next((value for value in
                     (deadline_days(item["text"]) for item in snapshot.constraints)
                     if value), None)
    remaining = (deadline - base.commit_days) if deadline else base.commit_days * 0.2

    not_adopted: list[dict[str, Any]] = []
    eligible: list[tuple[int, float, Any, Any, Any]] = []
    for item in snapshot.requirements:
        left = base.decision_for(item["id"])
        right = other.decision_for(item["id"])
        if left is None or right is None or left["choice"] == right["choice"]:
            continue
        if right["satisfies"] != "full":
            continue
        conflict = _conflict_constraint(right["choice"], snapshot)
        if conflict:
            not_adopted.append({
                "draft": other.draft_id, "decision": right["id"],
                "requirement_ids": [item["id"]], "scope": item["text"],
                "choice": right["choice"],
                "reason": f"与约束 {conflict['id']}「{conflict['text']}」冲突",
            })
            continue
        order = VALUE_ORDER.index(feature_of(item["text"])) if feature_of(item["text"]) else 9
        eligible.append((order, float(right["cost_days"]) - float(left["cost_days"]),
                         item, left, right))

    adopted: dict[str, dict[str, Any]] = {}
    for _, delta, item, left, right in sorted(eligible, key=lambda row: (row[0], row[1])):
        if delta <= remaining:
            adopted[item["id"]] = right
            remaining -= max(0.0, delta)
        else:
            not_adopted.append({
                "draft": other.draft_id, "decision": right["id"],
                "requirement_ids": [item["id"]], "scope": item["text"],
                "choice": right["choice"],
                "reason": (f"超出剩余预算：需要 +{delta:.1f} 天，"
                           f"剩余 {remaining:.1f} 天"),
            })

    decisions = []
    modules = []
    mapping = []
    acceptance = []
    used_names: set[str] = set()
    for index, item in enumerate(snapshot.requirements, 1):
        left = base.decision_for(item["id"])
        right = adopted.get(item["id"])
        if right is not None:
            decision = {
                "id": f"DEC-{index:02d}", "scope": item["text"],
                "choice": right["choice"],
                "reason": (f"采纳 {other.draft_id} 的 {right['id']}：{right['reason']}；"
                           f"差异 +{float(right['cost_days']) - float(left['cost_days']):.1f} 天，"
                           f"在剩余预算内"),
                "requirement_ids": [item["id"]],
                "draft_ids": [other.draft_id], "claim_ids": [
                    claim["id"] for claim in other.claims
                    if item["id"] in claim.get("requirement_ids", [])],
                "origin": "adopted", "satisfies": "full",
                "cost_days": float(right["cost_days"]), "needs_human": False,
            }
        else:
            decision = {
                "id": f"DEC-{index:02d}", "scope": item["text"],
                "choice": left["choice"], "reason": left["reason"],
                "requirement_ids": [item["id"]],
                "draft_ids": [base.draft_id], "claim_ids": [
                    claim["id"] for claim in base.claims
                    if item["id"] in claim.get("requirement_ids", [])],
                "origin": "base", "satisfies": left["satisfies"],
                "cost_days": float(left["cost_days"]),
                "needs_human": left["satisfies"] != "full",
            }
        decisions.append(decision)

        module_name = _unique_module_name(_module_name(item["text"], index), used_names)
        module = {
            "id": f"M-{index:02d}",
            "name": module_name,
            "responsibility": decision["choice"],
            "requirement_ids": [item["id"]],
            "decision_ids": [decision["id"]],
            "interfaces": _interfaces(item["text"], index),
            "estimate_days": decision["cost_days"],
            "deliverable": f"{module_name}交付物",
            "acceptance_items": [acceptance_for(decision, index)["criterion"]],
            "depends_on": [],
            "write_paths": [],
            "suggested_role": "ai+human" if decision["needs_human"] else "ai",
        }
        modules.append(module)
        mapping.append({"requirement_id": item["id"], "module_ids": [module["id"]],
                        "decision_ids": [decision["id"]]})
        acceptance.append(acceptance_for(decision, index))

    open_items: list[dict[str, Any]] = []
    for decision in decisions:
        if decision["satisfies"] == "full":
            continue
        requirement = snapshot.by_id(decision["requirement_ids"][0])
        open_items.append({
            "id": f"OPEN-{len(open_items) + 1:02d}",
            "question": (f"首版是否必须支持「{requirement['text']}」？"
                         f"如果不支持，由谁确认该需求延期？"),
            "impact": [requirement["id"]],
            "blocking": True,
            "candidates": [
                {"draft": base.draft_id, "choice": base.decision_for(requirement["id"])["choice"],
                 "cost_days": float(base.decision_for(requirement["id"])["cost_days"])},
                {"draft": other.draft_id, "choice": other.decision_for(requirement["id"])["choice"],
                 "cost_days": float(other.decision_for(requirement["id"])["cost_days"])},
            ],
            "origin": "merge",
            "resolution": None,
        })
    unmet = {decision["requirement_ids"][0] for decision in decisions
             if decision["satisfies"] != "full"}
    for row in not_adopted:
        if row["requirement_ids"][0] in unmet:
            continue
        requirement = snapshot.by_id(row["requirement_ids"][0])
        open_items.append({
            "id": f"OPEN-{len(open_items) + 1:02d}",
            "question": f"「{requirement['text']}」的体验增强是否排到下一版？",
            "impact": [requirement["id"]],
            "blocking": False,
            "candidates": [{"draft": row["draft"], "choice": row["reason"],
                            "cost_days": 0.0}],
            "origin": "merge",
            "resolution": None,
        })

    effort = effort_for(modules)

    non_goals = [{
        "item": row["scope"], "choice": row["choice"], "reason": row["reason"],
        "requirement_ids": list(row["requirement_ids"]),
    } for row in not_adopted]

    return Spec(
        spec_id="SPEC-001", version=1, status="draft",
        snapshot_id=snapshot.snapshot_id, snapshot_version=snapshot.version,
        snapshot_hash=snapshot.hash,
        architecture="分层：交互层 / 应用层 / 领域层 / 基础层；每个需求对应一个领域模块，"
                     "跨模块只经应用层接口调用。",
        base_draft=base_id,
        decisions=decisions, modules=modules, mapping=mapping, acceptance=acceptance,
        non_goals=non_goals, open_items=open_items, not_adopted=not_adopted,
        effort=effort,
        review_ref={"file": "review.json", "hash": content_hash(reviews)},
    )




def _unique_module_name(name: str, used: set[str]) -> str:
    if name not in used:
        used.add(name)
        return name
    index = 2
    while f"{name}（{index}）" in used:
        index += 1
    unique = f"{name}（{index}）"
    used.add(unique)
    return unique


def _feature_keyword_matches(feature: dict[str, Any],
                             snapshot: Snapshot) -> list[str]:
    """按功能名/描述里的动作词匹配快照需求，避免全部落到第一条。"""
    text = f"{feature.get('name') or ''} {feature.get('description') or ''}".strip()
    if not text:
        return []
    scored: list[tuple[int, int, str]] = []
    for item in snapshot.requirements:
        requirement_text = str(item.get("text") or "")
        if not requirement_text:
            continue
        score = 0
        if (shares_topic(text, requirement_text)
                or shares_topic(requirement_text, text)):
            score += 2
        for aliases in FEATURE_KEYWORD_GROUPS:
            if (any(alias in text for alias in aliases)
                    and any(alias in requirement_text for alias in aliases)):
                score += 3
        if score:
            scored.append((score, len(requirement_text), item["id"]))
    scored.sort(key=lambda row: (-row[0], row[1], row[2]))
    return [row[2] for row in scored[:3]]


def _feature_refs(feature: dict[str, Any], snapshot: Snapshot,
                  claim_refs: dict[str, list[str]]) -> list[str]:
    """功能 → 需求编号：先按功能文本匹配，再用 claim 反查；不拿第一条顶替。"""
    known = snapshot.ids("requirement")
    matched = _feature_keyword_matches(feature, snapshot)
    if matched:
        return matched

    claim_candidates: list[str] = []
    for source in feature.get("source_refs") or []:
        source = str(source)
        if source not in known:
            claim_candidates.extend(claim_refs.get(source, []))
    claim_candidates = [item for item in dict.fromkeys(claim_candidates)
                        if item in known]
    return claim_candidates[:5]


def _feature_criterion(name: str, description: str) -> str:
    """每个功能一条自己的验收口径，不再共用同一句模板尾巴。"""
    text = f"{name} {description}"
    rules = (
        (("检索", "搜索", "查找", "查询", "筛选"),
         f"{name}：按书名或关键词检索，单次检索 ≤ 1 秒，命中结果按相关度排序"),
        (("录入", "发布", "新建", "创建", "登记", "上传"),
         f"{name}：提交后 3 秒内生成记录，必填项缺失时定位到字段并提示"),
        (("详情", "查看", "浏览", "预览"),
         f"{name}：从列表进入详情不超过 2 步，返回后保留筛选条件"),
        (("审批", "审核", "提交", "驳回", "通过"),
         f"{name}：提交后生成待审批记录，状态更新 ≤ 3 秒且可追溯"),
        (("通知", "提醒", "消息", "推送"),
         f"{name}：状态变更后 5 分钟内触达，同一事件只提醒一次"),
        (("统计", "导出", "报表", "汇总"),
         f"{name}：导出字段与列表一致，导出 1000 条 ≤ 5 秒，统计口径可复核"),
        (("签到", "扫码"),
         f"{name}：扫码后 5 秒内返回签到结果，重复扫码只记一次"),
        (("报名", "组队", "团队", "协作"),
         f"{name}：报名提交后状态即时可见，取消/候补状态可追溯"),
        (("时长", "认定"),
         f"{name}：签到记录自动累计时长，人工认定留痕可追溯"),
    )
    for keys, criterion in rules:
        if any(key in text for key in keys):
            return criterion
    return f"{name}：3 个核心路径可用，输入输出与职责描述一致，重复操作结果一致"


def _feature_estimate(feature: dict[str, Any], refs: list[str], base: Draft) -> float | None:
    """只取草案工作包/决策里的真实估算；拿不到就返回 None，不做均摊。"""
    packages = [row for row in (base.packages or []) if isinstance(row, dict)]
    for requirement_id in refs:
        for package in packages:
            if requirement_id in str(package.get("name") or ""):
                value = package.get("likely") or package.get("optimistic")
                if value:
                    return max(0.5, round(float(value), 1))

    for requirement_id in refs:
        decision = base.decision_for(requirement_id)
        if decision and decision.get("cost_days") is not None:
            return max(0.5, round(float(decision["cost_days"]), 1))

    text = f"{feature.get('name') or ''} {feature.get('description') or ''}".strip()
    for package in packages:
        name = str(package.get("name") or "")
        if name and text and (shares_topic(name, text) or shares_topic(text, name)):
            value = package.get("likely") or package.get("optimistic")
            if value:
                return max(0.5, round(float(value), 1))
    return None


DEPENDENCY_RULES: tuple[tuple[tuple[str, ...], tuple[tuple[str, ...], ...]], ...] = (
    (("列表", "检索", "搜索", "查询", "筛选"),
     (("录入", "发布", "新建", "创建", "登记", "上传"),)),
    (("详情", "查看", "浏览", "预览"),
     (("列表", "检索", "搜索"),)),
    (("提交", "审批", "审核"),
     (("详情", "查看"), ("录入", "发布"))),
    (("通知", "提醒", "消息", "推送"),
     (("提交", "审批", "审核"),)),
    (("统计", "导出", "报表", "汇总"),
     (("提交", "审批", "审核"), ("签到", "报名"))),
    (("签到统计",),
     (("签到", "扫码"),)),
    (("扫码", "签到"),
     (("报名", "组队"),)),
    (("时长", "认定"),
     (("签到",),)),
    (("报名管理",),
     (("报名", "组队"),)),
    (("组队", "团队", "协作"),
     (("报名",),)),
    (("报名",),
     (("活动", "发布"),)),
    (("审核", "审批"),
     (("活动", "发布"),)),
)


def _would_cycle(start: str, goal: str,
                 graph: dict[str, list[str]]) -> bool:
    stack = [start]
    seen: set[str] = set()
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, []))
    return False


def _assign_dependencies(modules: list[dict[str, Any]]) -> None:
    """给功能之间补最小依赖，避免编排器把整张图退化成串行链。"""
    graph: dict[str, list[str]] = {module["id"]: [] for module in modules}
    for module in modules:
        text = f"{module.get('name') or ''} {module.get('responsibility') or ''}"
        chosen = None
        for target_keys, dependency_groups in DEPENDENCY_RULES:
            if not any(key in text for key in target_keys):
                continue
            for group in dependency_groups:
                for other in modules:
                    if other["id"] == module["id"]:
                        continue
                    other_text = f"{other.get('name') or ''} {other.get('responsibility') or ''}"
                    if any(key in other_text for key in group):
                        chosen = other["id"]
                        break
                if chosen:
                    break
            if chosen:
                break
        if not chosen:
            continue
        if _would_cycle(chosen, module["id"], graph):
            continue
        module["depends_on"] = [chosen]
        graph[module["id"]].append(chosen)



def _spec_from_features(snapshot: Snapshot, payload: dict[str, Any],
                        reviews: dict[str, Any], base: Draft,
                        claim_refs: dict[str, list[str]]) -> Spec:
    rows = payload.get("features") or []
    if len(rows) > MAX_MODULES:
        raise ContractError("TOO_MANY_MODULES",
                            f"单份规格最多 {MAX_MODULES} 个模块", "features")
    if not rows:
        raise ContractError("EMPTY_FEATURES", "功能清单为空，无法生成规格", "features")

    decisions: list[dict[str, Any]] = []
    modules: list[dict[str, Any]] = []
    mapping: list[dict[str, Any]] = []
    acceptance: list[dict[str, Any]] = []
    open_items: list[dict[str, Any]] = []
    used_names: set[str] = set()
    unresolved: list[dict[str, str]] = []
    inferred: list[dict[str, Any]] = []
    missing_estimates: list[dict[str, str]] = []

    for index, feature in enumerate(rows, 1):
        name = _unique_module_name(str(feature.get("name") or f"功能 {index}").strip(),
                                   used_names)
        description = str(feature.get("description") or name).strip() or name
        refs = _feature_refs(feature, snapshot, claim_refs)
        feature_id = str(feature.get("id") or f"F-{index:02d}")
        if not refs:
            refs = [feature_id]
            unresolved.append({"feature": feature_id, "name": name})
        elif feature.get("inferred"):
            inferred.append({"feature": feature_id, "name": name, "refs": refs})

        estimate = _feature_estimate(feature, refs, base)
        if estimate is None:
            missing_estimates.append({"feature": feature_id, "name": name})
        criterion = _feature_criterion(name, description)
        decision = {
            "id": f"DEC-{index:02d}",
            "scope": description,
            "choice": f"实现{name}：{description}",
            "reason": f"功能清单推断：{name}",
            "requirement_ids": refs,
            "draft_ids": [base.draft_id], "claim_ids": [],
            "origin": "features", "satisfies": "full",
            "cost_days": estimate, "needs_human": False,
        }
        module = {
            "id": f"M-{index:02d}",
            "name": name,
            "responsibility": description,
            "requirement_ids": refs,
            "decision_ids": [decision["id"]],
            "interfaces": [{"name": f"handle{index:02d}", "caller": "应用层",
                            "input": "{payload}", "output": "result | error"}],
            "estimate_days": estimate,
            "deliverable": f"{name}交付物",
            "acceptance_items": [criterion],
            "depends_on": [],
            "write_paths": [],
            "suggested_role": "ai",
        }
        decisions.append(decision)
        modules.append(module)
        for requirement_id in refs:
            mapping.append({"requirement_id": requirement_id,
                            "module_ids": [module["id"]],
                            "decision_ids": [decision["id"]]})
        acceptance.append({"id": "", "requirement_id": refs[0],
                           "criterion": criterion,
                           "verify": "按验收场景逐条演示，记录实际结果"})

    _assign_dependencies(modules)
    for index, row in enumerate(acceptance, 1):
        row["id"] = f"ACC-{index:02d}"

    if unresolved:
        open_items.append({
            "id": f"OPEN-{len(open_items) + 1:02d}",
            "question": ("以下功能暂未对应到明确需求，是否确认纳入首版："
                         + "、".join(row["name"] for row in unresolved)),
            "impact": [row["feature"] for row in unresolved],
            "blocking": False, "candidates": [],
            "origin": "features", "resolution": None,
        })
    if inferred:
        impacts = list(dict.fromkeys(
            ref for row in inferred for ref in row["refs"]))
        open_items.append({
            "id": f"OPEN-{len(open_items) + 1:02d}",
            "question": ("以下功能由讨论推断，是否确认纳入首版："
                         + "、".join(row["name"] for row in inferred)),
            "impact": impacts, "blocking": False, "candidates": [],
            "origin": "features", "resolution": None,
        })
    if missing_estimates:
        open_items.append({
            "id": f"OPEN-{len(open_items) + 1:02d}",
            "question": ("以下功能缺少草案工作包估算，计划评审前需补齐："
                         + "、".join(row["name"] for row in missing_estimates)),
            "impact": [row["feature"] for row in missing_estimates],
            "blocking": False, "candidates": [],
            "origin": "features", "resolution": None,
        })
    if not any(module["depends_on"] for module in modules):
        open_items.append({
            "id": f"OPEN-{len(open_items) + 1:02d}",
            "question": "未能推断功能间依赖，计划图可能退化为串行，是否补充依赖关系？",
            "impact": [module["id"] for module in modules],
            "blocking": False, "candidates": [],
            "origin": "features", "resolution": None,
        })

    return Spec(
        spec_id="SPEC-001", version=1, status="draft",
        snapshot_id=snapshot.snapshot_id, snapshot_version=snapshot.version,
        snapshot_hash=snapshot.hash,
        architecture="按功能清单划分模块：每个功能一个模块，跨模块只经应用层接口调用。",
        base_draft=base.draft_id,
        decisions=decisions, modules=modules, mapping=mapping, acceptance=acceptance,
        non_goals=[], open_items=open_items, not_adopted=[],
        effort=effort_for(modules),
        review_ref={"file": "review.json", "hash": content_hash(reviews)},
    )

def save_spec(spec: Spec, directory: str | Path) -> Path:
    root = Path(directory)
    versioned = write_json(root / f"spec.v{spec.version}.json", spec.to_json())
    write_json(root / "spec.json", spec.to_json())
    return versioned


def load_spec(directory: str | Path) -> Spec:
    path = Path(directory)
    if path.is_dir():
        path = path / "spec.json"
    return Spec.from_json(read_json(path))
