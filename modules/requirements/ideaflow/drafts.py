"""A/B 两组草案：同一份快照，两套目标函数，互相不可见。

- A 组「交付优先」：用最短路径跑通核心流程，先验证需求是否真实存在；
- B 组「体验优先」：把关键路径压到 3 步内，体验项一次做到位。

隔离方式：每组只拿到 `group_context()` 生成的独立副本，里面只有快照条目和
本组目标，没有另一组的任何内容；生成过程不读写共享的可变对象。
"""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from .constraints import deadline_days, forbidden_subjects, headcount, shares_topic
from .contracts import ContractError
from .estimate import pert
from .roles import Role
from .snapshot import Snapshot
from .storage import content_hash, now_iso

SATISFIES = ("full", "partial", "declined")
SATISFIES_LABEL = {"full": "完全满足", "partial": "部分满足", "declined": "不满足"}
NEGATIONS = ("不做", "不支持", "禁止", "不引入", "不接", "改为线下", "不使用")


@dataclass(frozen=True)
class GroupProfile:
    id: str
    name: str
    objective: str
    posture: str
    overhead: tuple[float, float, float]


GROUPS: dict[str, GroupProfile] = {
    "A": GroupProfile("A", "交付优先",
                      "用最短路径跑通核心流程，先验证需求是否真实存在",
                      "minimal", (2.0, 3.0, 5.0)),
    "B": GroupProfile("B", "体验优先",
                      "把关键路径压到 3 步内，体验项一次做到位",
                      "complete", (4.0, 6.0, 9.0)),
}

# 同一需求上两组的典型取舍；没有命中时退化为通用表述
PLAYBOOK: tuple[dict[str, Any], ...] = (
    {
        "keys": ("支付", "付款", "结算"),
        "a": ("首版不做站内支付，改为线下当面交易并记录成交状态", 1.0, "declined",
              "支付涉及资质与资金安全，首版不承担"),
        "b": ("站内支付 + 担保交易，覆盖下单、付款、放款全流程", 7.0, "full",
              "支付闭环直接决定成交率与复购"),
        "risk_a": "线下交易缺少凭证，纠纷时难以界定责任",
        "risk_b": "需要支付资质与对账机制，合规检查可能拖长工期",
    },
    {
        "keys": ("搜索", "检索"),
        "a": ("按书名关键词搜索，结果按相关度排序", 2.0, "full",
              "搜索只需覆盖找书这一件事"),
        "b": ("关键词 + 标签 + 价格排序 + 搜索建议 + 空结果推荐", 4.5, "full",
              "找书效率直接决定学生是否留下"),
        "risk_a": "只支持精确书名，模糊需求可能找不到",
        "risk_b": "排序与建议需要数据积累，冷启动效果有限",
    },
    {
        "keys": ("发布", "上传", "登记"),
        "a": ("单个表单发布，必填书名、价格与联系方式", 2.0, "full",
              "先保证能发出第一条书讯"),
        "b": ("草稿箱、批量导入、图片压缩与预览", 4.0, "full",
              "发布门槛越低，供给越容易起来"),
        "risk_a": "一次只能发一本，供给增长慢",
        "risk_b": "批量导入需要模板与校验，工期不可控",
    },
    {
        "keys": ("收藏", "关注", "订阅"),
        "a": ("列表页收藏按钮，收藏夹仅列表展示", 1.0, "full",
              "收藏只是辅助动作，不做额外管理"),
        "b": ("收藏夹分组、降价提醒、到货通知", 5.0, "full",
              "提醒是让学生回访的关键触点"),
        "risk_a": "没有提醒，收藏后容易遗忘",
        "risk_b": "提醒依赖后台任务与消息通道",
    },
    {
        "keys": ("自提", "面交", "线下"),
        "a": ("线下当面交易，App 内只记录约见时间与地点", 1.0, "full",
              "线下成交不需要额外系统能力"),
        "b": ("线下面交 + 校内自提点地图与预约时段", 3.5, "full",
              "自提点能显著降低爽约率"),
        "risk_a": "约见信息靠聊天工具传递，容易丢失",
        "risk_b": "自提点需要线下合作，首版难以保证",
    },
    {
        "keys": ("评价", "信用", "评分"),
        "a": ("交易完成后可打一次 1—5 星，不做申诉流程", 1.5, "partial",
              "评分先解决有没有的问题"),
        "b": ("双向评价 + 信用分 + 申诉与仲裁流程", 6.0, "full",
              "信用体系是二手交易长期成立的前提"),
        "risk_a": "没有申诉，恶意差评无法处理",
        "risk_b": "仲裁需要人工介入，运营成本高",
    },
)

GENERIC = {
    "a": ("以最小实现满足：{text}", 2.0, "full", "先覆盖功能，体验项留到后续版本"),
    "b": ("完整体验实现：{text}，补齐引导、空状态与异常提示", 3.5, "full",
          "完整体验直接影响留存"),
}


def group_context(snapshot: Snapshot, profile: GroupProfile) -> dict[str, Any]:
    """给某一组的独立上下文：只含快照与本组目标，不含其他组的信息。"""
    return {
        "group": profile.id,
        "objective": profile.objective,
        "posture": profile.posture,
        "rules": [
            "只能引用快照中存在的条目编号",
            "每条明确要求都必须给出本组的选择",
            "与约束冲突时必须写明冲突，不能静默忽略",
        ],
        "snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "version": snapshot.version,
            "hash": snapshot.hash,
            "items": [
                {key: item[key] for key in
                 ("id", "type", "text", "priority", "acceptance")}
                for item in snapshot.items
            ],
        },
    }


def _match_feature(text: str) -> dict[str, Any] | None:
    for feature in PLAYBOOK:
        if any(key in text for key in feature["keys"]):
            return feature
    return None


def _requirement_decision(group: str, item: dict[str, Any], index: int) -> dict[str, Any]:
    feature = _match_feature(item["text"])
    if feature:
        choice, cost, satisfies, reason = feature[group.lower()]
    else:
        template = GENERIC[group.lower()]
        choice = template[0].format(text=item["text"])
        cost, satisfies, reason = template[1], template[2], template[3]

    risk = feature[f"risk_{group.lower()}"] if feature else (
        "范围压得过紧可能漏掉必要细节" if group == "A" else "体验项投入过多会挤压交付时间")
    return {
        "id": f"{group}-DEC-{index:02d}",
        "requirement_ids": [item["id"]],
        "scope": item["text"],
        "choice": choice,
        "reason": reason,
        "cost_days": float(cost),
        "satisfies": satisfies,
        "risk": risk,
    }


def _packages(profile: GroupProfile, decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    packages = [{
        "name": f"实现 {decision['requirement_ids'][0]}",
        "optimistic": round(decision["cost_days"] * 0.8, 1),
        "likely": decision["cost_days"],
        "pessimistic": round(decision["cost_days"] * 1.6, 1),
    } for decision in decisions]
    packages.append({
        "name": "联调、验收与演示准备",
        "optimistic": profile.overhead[0],
        "likely": profile.overhead[1],
        "pessimistic": profile.overhead[2],
    })
    return packages


def _claims(group: str, decisions: list[dict[str, Any]],
            constraints: list[dict[str, Any]], estimate: dict[str, Any]) -> list[dict[str, Any]]:
    first_refs = [decision["requirement_ids"][0] for decision in decisions[:2]]
    focus_refs = [decision["requirement_ids"][0] for decision in decisions
                  if any(key in decision["scope"] for key in ("搜索", "收藏"))]
    payment = next((decision for decision in decisions
                    if any(key in decision["scope"] for key in ("支付", "付款"))), None)
    declined = [decision for decision in decisions if decision["satisfies"] == "declined"]

    if group == "A":
        first = ("先跑通发布与搜索，才能在期限内拿到真实反馈", first_refs, "rule")
        second = ((f"「{declined[0]['scope']}」在首版降级为线下流程，避开资质与资金安全",
                   declined[0]["requirement_ids"], "value") if declined else
                  ("把体验项留到后续版本，先保证主流程可用", [], "value"))
    else:
        first = (("完整的搜索与收藏体验决定学生是否愿意再次使用",
                  focus_refs or first_refs, "assumption"))
        second = ((f"「{payment['scope']}」用站内支付与担保交易提升成交率",
                   payment["requirement_ids"], "assumption") if payment else
                  ("更完整的体验能带来更高的回访率", focus_refs[:1], "assumption"))

    days = deadline_days(constraints[0]["text"]) if constraints else None
    if days:
        if estimate["commit_days"] > days:
            third_text = (f"当前范围承诺 {estimate['commit_days']} 天，"
                          f"超出 {days} 天期限，需要放宽或裁剪")
        else:
            third_text = (f"当前范围承诺 {estimate['commit_days']} 天，"
                          f"在 {days} 天期限内")
    else:
        third_text = f"当前范围承诺 {estimate['commit_days']} 天"
    third: tuple[str, list[str], str] = (third_text, [], "rule")

    claims = []
    for index, (text, refs, evidence) in enumerate((first, second, third), 1):
        claim: dict[str, Any] = {"id": f"{group}-{index:02d}", "text": text,
                                 "evidence": evidence}
        if refs:
            claim["requirement_ids"] = refs
        if index == 3 and constraints:
            claim["constraint_ids"] = [constraints[0]["id"]]
        claims.append(claim)
    return claims


def _constraint_checks(decisions: list[dict[str, Any]], constraints: list[dict[str, Any]],
                       estimate: dict[str, Any]) -> list[dict[str, Any]]:
    checks = []
    for item in constraints:
        text = item["text"]
        note, complies = "按约束执行", True
        days = deadline_days(text)
        if days:
            if estimate["commit_days"] > days:
                complies = False
                note = f"承诺 {estimate['commit_days']} 天，超出 {days} 天期限"
            else:
                note = f"承诺 {estimate['commit_days']} 天，在 {days} 天期限内"
        people = headcount(text)
        if people:
            note = f"{people} 人按串行排期，关键路径不并行"
        subjects = forbidden_subjects(text)
        if subjects:
            hits = [decision for decision in decisions
                    if decision["satisfies"] != "declined"
                    and not any(word in decision["choice"] for word in NEGATIONS)
                    and any(shares_topic(subject, decision["choice"])
                            for subject in subjects)]
            if hits:
                complies = False
                note = f"方案包含被约束排除的内容：{subjects[0]}"
        checks.append({"constraint_id": item["id"], "note": note, "complies": complies})
    return checks


@dataclass
class Draft:
    draft_id: str
    group: str
    group_name: str
    objective: str
    snapshot_id: str
    snapshot_version: int
    snapshot_hash: str
    summary: str
    decisions: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    assumptions: list[dict[str, Any]]
    risks: list[str]
    constraints_checked: list[dict[str, Any]]
    packages: list[dict[str, Any]]
    estimate: dict[str, Any]
    created_at: str = field(default_factory=now_iso)
    attempt: int = 1

    @property
    def hash(self) -> str:
        return content_hash(self._content())

    def _content(self) -> dict[str, Any]:
        return {"group": self.group, "snapshot": [self.snapshot_id, self.snapshot_version,
                                                  self.snapshot_hash],
                "decisions": self.decisions, "claims": self.claims,
                "assumptions": self.assumptions, "risks": self.risks,
                "packages": self.packages}

    @property
    def commit_days(self) -> float:
        return float(self.estimate["commit_days"])

    @property
    def declined(self) -> list[str]:
        return [decision["requirement_ids"][0] for decision in self.decisions
                if decision["satisfies"] == "declined"]

    def decision_for(self, requirement_id: str) -> dict[str, Any] | None:
        for decision in self.decisions:
            if requirement_id in decision["requirement_ids"]:
                return decision
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "group": self.group,
            "group_name": self.group_name,
            "objective": self.objective,
            "snapshot_ref": {"snapshot_id": self.snapshot_id, "version": self.snapshot_version,
                             "hash": self.snapshot_hash},
            "summary": self.summary,
            "hash": self.hash,
            "created_at": self.created_at,
            "attempt": self.attempt,
            "decisions": self.decisions,
            "claims": self.claims,
            "assumptions": self.assumptions,
            "risks": self.risks,
            "constraints_checked": self.constraints_checked,
            "packages": self.packages,
            "estimate": self.estimate,
            "declined_requirements": self.declined,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "Draft":
        try:
            draft = cls(
                draft_id=payload["draft_id"],
                group=payload["group"],
                group_name=payload.get("group_name", payload["group"]),
                objective=payload.get("objective", ""),
                snapshot_id=payload["snapshot_ref"]["snapshot_id"],
                snapshot_version=int(payload["snapshot_ref"]["version"]),
                snapshot_hash=payload["snapshot_ref"]["hash"],
                summary=payload.get("summary", ""),
                decisions=payload["decisions"],
                claims=payload["claims"],
                assumptions=payload.get("assumptions", []),
                risks=payload.get("risks", []),
                constraints_checked=payload.get("constraints_checked", []),
                packages=payload.get("packages", []),
                estimate=payload["estimate"],
                created_at=payload.get("created_at", now_iso()),
                attempt=int(payload.get("attempt", 1)),
            )
        except KeyError as error:
            raise ContractError("INVALID_DRAFT", f"草案缺少字段 {error}", "draft") from None
        if payload.get("hash") and payload["hash"] != draft.hash:
            raise ContractError("HASH_MISMATCH", "草案内容与记录的哈希不一致", "hash")
        return draft



def _group_role(profile: GroupProfile) -> Role:
    return Role(id=f"group-{profile.id}", name=profile.name, focus=profile.objective,
                must_answer=("每条明确要求给出本组选择", "与约束冲突必须写明"),
                forbidden="不得引用快照之外的条目编号")


def _decisions_from_agent(group: str, raw: dict[str, Any],
                          items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scope_of = {item["id"]: item["text"] for item in items}
    rows = raw.get("decisions") or []
    if not isinstance(rows, list) or not rows:
        raise ContractError("EMPTY_DRAFT", "模型草案没有 decisions", "decisions")
    decisions = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ContractError("INVALID_FIELD", "决策必须是对象", f"decisions[{index}]")
        refs = [str(item) for item in (row.get("requirement_ids") or [])]
        if not refs:
            raise ContractError("EMPTY_REF", "决策必须引用需求编号",
                                f"decisions[{index}].requirement_ids")
        decisions.append({
            "id": f"{group}-DEC-{index:02d}",
            "requirement_ids": refs,
            "scope": scope_of.get(refs[0], str(row.get("scope") or "")),
            "choice": str(row.get("choice") or "").strip(),
            "reason": str(row.get("reason") or "").strip(),
            "cost_days": float(row.get("cost_days") or 0),
            "satisfies": row.get("satisfies") or "full",
            "risk": str(row.get("risk") or "").strip(),
        })
    return decisions


def _claims_from_agent(group: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
    rows = raw.get("claims") or []
    claims = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not str(row.get("text") or "").strip():
            continue
        claim = {"id": f"{group}-{index:02d}", "text": str(row["text"]).strip(),
                 "evidence": row.get("evidence") if row.get("evidence") in
                 ("fact", "rule", "value", "assumption") else "rule"}
        refs = [str(item) for item in (row.get("requirement_ids") or [])]
        constraints = [str(item) for item in (row.get("constraint_ids") or [])]
        if refs:
            claim["requirement_ids"] = refs
        if constraints:
            claim["constraint_ids"] = constraints
        claims.append(claim)
    return claims

def build_draft(snapshot: Snapshot, group: str, *, attempt: int = 1,
                context: dict[str, Any] | None = None,
                agent: Any | None = None) -> Draft:
    if group not in GROUPS:
        raise ContractError("UNKNOWN_GROUP", f"未知方案组 {group}", "group")
    profile = GROUPS[group]
    view = context or group_context(snapshot, profile)
    items = view["snapshot"]["items"]
    requirements = [item for item in items if item["type"] == "requirement"]
    constraints = [item for item in items if item["type"] == "constraint"]
    if not requirements:
        raise ContractError("NO_REQUIREMENT", "快照里没有明确要求，无法出方案", "items")

    if agent is not None:
        raw = agent.respond("draft", _group_role(profile), deepcopy(view))
        decisions = _decisions_from_agent(group, raw, requirements)
        packages = _packages(profile, decisions)
        estimate = pert(packages)
        claims = _claims_from_agent(group, raw) or _claims(group, decisions,
                                                           constraints, estimate)
        assumptions = [row for row in (raw.get("assumptions") or [])
                       if isinstance(row, dict) and row.get("text")] or [
            {"text": "模型给出的方案可以在期限内完成", "verify_by": "评审时逐条核对估算"}]
        risks = [str(row).strip() for row in (raw.get("risks") or []) if str(row).strip()]
        risks = risks or [row["risk"] for row in decisions[:2] if row.get("risk")]
    else:
        decisions = [_requirement_decision(group, item, index)
                     for index, item in enumerate(requirements, 1)]
        packages = _packages(profile, decisions)
        estimate = pert(packages)
        claims = _claims(group, decisions, constraints, estimate)
        assumptions = ([{"text": "学生愿意线下当面交易",
                         "verify_by": "访谈 5 名学生，记录最近一次交易方式"}]
                       if group == "A" else
                       [{"text": "学生愿意为更完整的体验多等一到两周",
                         "verify_by": "对比两版原型的完成率与回访率"}])
        risks = ([d["risk"] for d in decisions[:2]] +
                 (["线下交易缺少凭证，纠纷处理成本高"] if draft_declined(decisions) else []))

    checks = _constraint_checks(decisions, constraints, estimate)

    draft = Draft(
        draft_id=f"DRAFT-{group}",
        group=group,
        group_name=profile.name,
        objective=profile.objective,
        snapshot_id=snapshot.snapshot_id,
        snapshot_version=snapshot.version,
        snapshot_hash=snapshot.hash,
        summary=(f"{profile.name}：{len(decisions)} 条需求决策，"
                 f"承诺 {estimate['commit_days']} 天"),
        decisions=decisions,
        claims=claims,
        assumptions=assumptions,
        risks=risks,
        constraints_checked=checks,
        packages=packages,
        estimate=estimate,
        attempt=attempt,
    )
    validate_draft(draft, snapshot)
    return draft


def draft_declined(decisions: list[dict[str, Any]]) -> bool:
    return any(decision["satisfies"] == "declined" for decision in decisions)


def validate_draft(draft: Draft, snapshot: Snapshot) -> None:
    """草案契约：需求必须全覆盖，所有引用必须能回指快照。"""
    if not draft.claims:
        raise ContractError("EMPTY_DRAFT", "草案没有任何主张", "claims")
    covered = {rid for decision in draft.decisions
               for rid in decision["requirement_ids"]}
    missing = snapshot.ids("requirement") - covered
    if missing:
        raise ContractError("DRAFT_COVERAGE",
                            f"草案遗漏需求：{', '.join(sorted(missing))}", "decisions")

    for index, decision in enumerate(draft.decisions):
        path = f"decisions[{index}]"
        snapshot.ensure_known(decision["requirement_ids"], path=f"{path}.requirement_ids")
        if decision["satisfies"] not in SATISFIES:
            raise ContractError("INVALID_ENUM", "满足程度取值不合法", f"{path}.satisfies")
        if float(decision["cost_days"]) <= 0:
            raise ContractError("INVALID_FIELD", "成本必须大于 0", f"{path}.cost_days")

    for index, claim in enumerate(draft.claims):
        path = f"claims[{index}].requirement_ids"
        snapshot.ensure_known(claim.get("requirement_ids", []), path=path)
        snapshot.ensure_known(claim.get("constraint_ids", []), path=path)
    for index, check in enumerate(draft.constraints_checked):
        snapshot.ensure_known([check["constraint_id"]],
                              path=f"constraints_checked[{index}].constraint_id")


def run_groups(snapshot: Snapshot, *, groups: Iterable[str] = ("A", "B"),
               on_context: Any | None = None,
               agent: Any | None = None) -> dict[str, Draft]:
    """依次生成两组草案；每组使用独立上下文，互不可见。"""
    if snapshot.status != "confirmed":
        raise ContractError("SNAPSHOT_NOT_CONFIRMED",
                            "开始出方案前必须先确认快照", "status")
    drafts: dict[str, Draft] = {}
    for group in groups:
        profile = GROUPS[group]
        context = deepcopy(group_context(snapshot, profile))
        if on_context is not None:
            on_context(group, context)
        drafts[group] = build_draft(snapshot, group, context=context, agent=agent)
    return drafts
