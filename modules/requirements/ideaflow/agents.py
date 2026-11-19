"""角色 Agent：统一接口与离线实现。

真实模型适配器在后续部分接入，接口保持不变：
``agent.respond(stage, role, context) -> dict``。
这样离线演示、单元测试和真实调用走同一条流程。
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Protocol

from .roles import DISCUSSION_ROLES, Role


class Agent(Protocol):
    name: str

    def respond(self, stage: str, role: Role, context: dict[str, Any]) -> dict[str, Any]:
        ...


def brief(idea: str, limit: int = 32) -> str:
    """取想法摘要，用于模板中引用原始输入。"""
    text = re.sub(r"\s+", "", idea).strip("。！？!?，,")
    return text if len(text) <= limit else text[:limit] + "…"


def _claim_of(claims: list[dict[str, Any]], role_id: str, index: int = 0) -> dict[str, Any] | None:
    rows = [row for row in claims if row.get("role") == role_id]
    if not rows:
        return None
    return rows[min(index, len(rows) - 1)]


class MockAgent:
    """离线角色模拟：内容由模板与关键词生成，行为与真实模型一致地受契约约束。

    它不是“假装很聪明”的模型，而是让流程、校验、编号、状态机可以在没有
    API Key 的环境里完整跑通，并作为真实模型的回归基线。
    """

    name = "mock"

    def respond(self, stage: str, role: Role, context: dict[str, Any]) -> dict[str, Any]:
        if stage == "clarify":
            return self._clarify(role)
        if stage == "diverge":
            return self._diverge(role, context)
        if stage == "critique":
            return self._critique(role, context)
        if stage == "sign":
            return self._sign(role, context)
        if stage == "synthesize":
            return self._synthesize(context)
        raise ValueError(f"MockAgent 不支持阶段：{stage}")

    # ---------- 首轮澄清 ----------

    @staticmethod
    def _clarify(role: Role) -> dict[str, Any]:
        """只提“没有答复就无法继续”的问题，每个想法最多问一轮。"""
        questions = [
            "这个想法最终由谁验收，验收标准由谁定义？",
            "首版必须在什么时间点可用，超期是否仍要上线？",
        ]
        return {"role": role.id, "claims": [], "assumptions": [],
                "objections": [], "questions": questions}

    # ---------- 独立发散 ----------

    def _diverge(self, role: Role, context: dict[str, Any]) -> dict[str, Any]:
        text = brief(context["idea"])
        builders = {
            "ba": self._ba,
            "user": self._user,
            "tech": self._tech,
            "innovation": self._innovation,
            "risk": self._risk,
            "skeptic": self._skeptic,
        }
        return builders[role.id](text)

    @staticmethod
    def _ba(text: str) -> dict[str, Any]:
        return {
            "role": "ba",
            "claims": [
                {"text": f"问题定义应收窄为「{text}」这一条主线，首版不覆盖衍生场景",
                 "reason": "原始描述只给出目标，没有提出运营、商业化或跨组织的需求",
                 "evidence": "fact", "assumes": [0]},
                {"text": "至少需要一个可量化的成功指标，否则无法判断首版是否有效",
                 "reason": "当前描述是功能导向，缺少可验证的验收目标",
                 "evidence": "rule", "assumes": []},
            ],
            "assumptions": [
                {"text": "目标用户集中在同一组织或社群内，需求频次稳定",
                 "verify_by": "访谈 5 名目标用户，记录最近一次同类行为"},
            ],
            "questions": ["由谁负责最终确认需求范围与验收目标？"],
        }

    @staticmethod
    def _user(text: str) -> dict[str, Any]:
        return {
            "role": "user",
            "claims": [
                {"text": "首次使用的关键路径应控制在 3 步以内，否则多数用户会在中途放弃",
                 "reason": "同类工具的首屏路径普遍不超过 3 步，超过后完成率明显下降",
                 "evidence": "rule", "assumes": [0]},
                {"text": "用户的真实动机是省时间而不是功能多，功能列表越长迁移成本越高",
                 "reason": "迁移成本来自学习新流程，而不是缺少功能",
                 "evidence": "value", "assumes": []},
            ],
            "assumptions": [
                {"text": "用户愿意在手机端完成完整流程，不需要桌面端",
                 "verify_by": "5 人可用性测试，记录完成率与放弃点"},
            ],
            "questions": ["用户现在用什么方式解决同一个问题？"],
        }

    @staticmethod
    def _tech(text: str) -> dict[str, Any]:
        return {
            "role": "tech",
            "claims": [
                {"text": "首版用单体服务加关系型数据库即可，先不引入消息队列和微服务",
                 "reason": "没有高并发与异步任务的证据，提前拆分只会增加联调成本",
                 "evidence": "rule", "assumes": [0]},
                {"text": "所有外部依赖都应可替换，避免被单一平台接口锁死",
                 "reason": "平台审核与接口变更是最常见的上线阻塞",
                 "evidence": "rule", "assumes": []},
            ],
            "assumptions": [
                {"text": "首版日活低于 1000，单体服务足以支撑",
                 "verify_by": "按目标组织规模做容量估算"},
            ],
            "questions": ["是否必须接入某个指定平台或已有账号体系？"],
        }

    @staticmethod
    def _innovation(text: str) -> dict[str, Any]:
        return {
            "role": "innovation",
            "claims": [
                {"text": "以组织或标签为维度的定向匹配，比全站搜索更有差异",
                 "reason": "垂直场景的转化取决于供给密度，而不是功能数量",
                 "evidence": "value", "assumes": [0]},
                {"text": "先做一个可以被截图传播的单点功能，比一次性做全流程更容易获得首批用户",
                 "reason": "冷启动阶段的口碑来自一个具体可感知的能力",
                 "evidence": "value", "assumes": []},
            ],
            "assumptions": [
                {"text": "同一组织内的供给密度足够形成匹配",
                 "verify_by": "统计现有渠道的日成交量"},
            ],
            "questions": ["有没有一个别人做过但体验很差的环节可以切入？"],
        }

    @staticmethod
    def _risk(text: str) -> dict[str, Any]:
        return {
            "role": "risk",
            "claims": [
                {"text": "内容审核与线下纠纷是首版最大的非技术风险，需要免责声明与举报入口",
                 "reason": "涉及用户发布与线下交付的产品，投诉集中在信息真实性与责任划分",
                 "evidence": "assumption", "assumes": [0]},
                {"text": "首版若承诺具体交付时效，会放大不可控的履约风险",
                 "reason": "履约依赖第三方，超出团队控制范围",
                 "evidence": "rule", "assumes": []},
            ],
            "assumptions": [
                {"text": "用户生成内容需要审核，否则会出现违规与纠纷",
                 "verify_by": "查阅平台内容规范与历史投诉记录"},
            ],
            "questions": ["出现虚假信息或纠纷时由谁处理，处理时限是多少？"],
        }

    @staticmethod
    def _skeptic(text: str) -> dict[str, Any]:
        return {
            "role": "skeptic",
            "claims": [
                {"text": f"若目标用户已在现成渠道完成同类交易，平台上线首月很难获得有效供给，功能完整也无法留存",
                 "reason": "反例：班级群里已有稳定的二手交易习惯，发一条消息的成本远低于注册与发布",
                 "evidence": "rule", "assumes": [0]},
                {"text": "当前讨论没有界定「不做什么」，范围会随着讨论不断膨胀",
                 "reason": "缺少非目标约束时，每个角色都会把自己的关注点加入首版",
                 "evidence": "rule", "assumes": []},
            ],
            "assumptions": [
                {"text": "现有替代渠道的摩擦成本足以让用户愿意迁移",
                 "verify_by": "对比现有渠道完成一次交易的步骤数与耗时"},
            ],
            "questions": ["第一批供给从哪里来，由谁负责拉新？"],
        }

    # ---------- 结构化批判 ----------

    def _critique(self, role: Role, context: dict[str, Any]) -> dict[str, Any]:
        claims = context["claims"]
        plans = {
            "ba": ("innovation", 0, "major",
                   "定向匹配依赖供给密度，首版用户量不足以支撑这个差异化前提",
                   "差异化无法在第一版被验证，投入的运营成本没有回收路径"),
            "user": ("tech", 0, "minor",
                   "单体架构不阻碍体验，但 3 步路径需要前端交互提前定稿",
                   "否则技术实现完成后还要返工交互，延长交付周期"),
            "tech": ("user", 0, "major",
                   "3 步是体验目标，不是可验收指标，无法判断是否达成",
                   "验收阶段会因为没有量化口径产生争议"),
            "innovation": ("ba", 0, "major",
                   "范围收窄过头会把可感知的差异化一起砍掉，回到同质化工具",
                   "首版没有记忆点，获客只能依赖补贴"),
            "risk": ("tech", 1, "major",
                   "依赖可替换意味着要多写一层适配，首版工作量被低估",
                   "联调时间挤压验收时间，风险转移到交付阶段"),
            "skeptic": ("ba", 0, "blocking",
                   "范围收窄没有配套的非目标清单，无法阻止后续需求膨胀",
                   "每轮讨论都可以合法地新增范围，最终无法按期交付"),
        }
        owner, index, severity, text, consequence = plans[role.id]
        target = _claim_of(claims, owner, index)
        objections = []
        if target:
            objections.append({"target": target["id"], "text": text,
                               "consequence": consequence, "severity": severity})
        if role.id == "skeptic" and len(claims) > 2:
            second = _claim_of(claims, "innovation", 1)
            if second:
                objections.append({
                    "target": second["id"],
                    "text": "单点传播功能没有说明传播对象与动机，可能只是内部自嗨",
                    "consequence": "获客假设不成立时，首版无法验证真实需求",
                    "severity": "major",
                })
        questions = []
        if role.id == "skeptic":
            questions = ["阻断异议解除前，是否允许先按 A 方案开发？"]
        output: dict[str, Any] = {"role": role.id, "claims": [], "assumptions": [],
                                  "objections": objections, "questions": questions}
        if role.id == "user":
            output["position_changed"] = {
                "from": "agree", "to": "conditional",
                "why": "在未量化路径步骤之前，不能确认体验目标可以达成",
            }
        return output

    # ---------- 方案签署 ----------

    def _sign(self, role: Role, context: dict[str, Any]) -> dict[str, Any]:
        options = {row["key"]: row["id"] for row in context["options"]}
        table = {
            "ba": [("A", "agree", "范围可控，能最早验证需求真实性", ""),
                   ("B", "conditional", "接受体验优化，但需先确认审核责任归属", "审核责任人写入确认书"),
                   ("C", "oppose", "运营投入超出首版可承受范围", "")],
            "user": [("A", "conditional", "核心路径可用，但需量化 3 步目标", "把 3 步写成验收项"),
                     ("B", "agree", "体验优先符合使用预期", ""),
                     ("C", "unreviewed", "尚未看到运营方案细节", "")],
            "tech": [("A", "agree", "技术风险最低", ""),
                     ("B", "oppose", "实时通信会增加联调与运维成本", ""),
                     ("C", "conditional", "定向匹配需要额外索引设计", "索引方案先评审")],
            "innovation": [("A", "oppose", "没有差异化，难以获得首批用户", ""),
                           ("B", "agree", "可通过体验形成口碑", ""),
                           ("C", "agree", "供给密度是核心变量", "")],
            "risk": [("A", "conditional", "需补免责声明与举报入口", "上线前完成合规检查"),
                     ("B", "oppose", "纠纷面扩大且缺少处理人力", ""),
                     ("C", "conditional", "运营激励可能带来刷量", "设置反刷量规则")],
            "skeptic": [("A", "conditional", "只在供给问题有答案后成立", "首批供给来源明确"),
                        ("B", "oppose", "体验优化无法解决冷启动", ""),
                        ("C", "oppose", "运营优先把最大不确定性放在首版", "")],
        }
        stances = []
        for key, stance, reason, condition in table[role.id]:
            if key not in options:
                continue
            stances.append({"option": options[key], "stance": stance,
                            "reason": reason, "condition": condition})
        return {"role": role.id, "stances": stances}

    # ---------- 整合 ----------

    def _synthesize(self, context: dict[str, Any]) -> dict[str, Any]:
        text = brief(context["idea"])
        claims = context["claims"]
        pick = lambda role, index=0: (
            _claim_of(claims, role, index)["id"] if _claim_of(claims, role, index) else None)

        options = [
            {"key": "A", "name": "最小可交付闭环",
             "scope": ["想法输入与结构化整理", "核心流程走通并产出可查看的结果", "基础记录与导出"],
             "cost_days": "8-12", "risk": "低",
             "tradeoffs": "功能最少，但能最快拿到真实反馈",
             "based_on": [item for item in (pick("ba"), pick("tech")) if item]},
            {"key": "B", "name": "体验优先",
             "scope": ["A 的全部范围", "关键路径压缩到 3 步以内", "空状态与异常路径处理"],
             "cost_days": "15-20", "risk": "中",
             "tradeoffs": "留存更好，但首批供给问题仍未解决",
             "based_on": [item for item in (pick("user"), pick("user", 1)) if item]},
            {"key": "C", "name": "运营优先",
             "scope": ["A 的全部范围", "面向组织或标签的定向匹配", "邀请与激励流程"],
             "cost_days": "18-25", "risk": "中高",
             "tradeoffs": "需要持续运营投入，冷启动不确定性最大",
             "based_on": [item for item in (pick("innovation"), pick("innovation", 1)) if item]},
        ]
        decisions = [
            {"scope": "首版范围", "choice": "以 A 为基线，B 中已量化的体验项择优并入",
             "reason": "阻断异议集中在供给与范围，A 能最快验证需求是否真实存在"},
            {"scope": "成功指标", "choice": "首周完成核心流程的用户数 ≥ 20，次日回访率 ≥ 30%",
             "reason": "两项都能从现有数据直接统计，不依赖额外埋点体系"},
        ]
        non_goals = [
            {"item": "在线支付与履约", "reason": "涉及第三方资质与资金安全，首版不承担"},
            {"item": "跨组织推广与商业化", "reason": "供给密度未验证前，推广只会放大获客成本"},
            {"item": "复杂权限与多租户", "reason": "首版面向单一组织，权限模型可后置"},
        ]
        narrative = {
            "background": f"用户提出「{text}」。目前只有目标描述，缺少范围边界、验收口径与风险约束，"
                          "需要先把它整理成可讨论、可确认的方案。",
            "users": "同一组织或社群内的成员，需求频次稳定，使用场景集中在手机端。",
            "value": "把分散在即时通讯与线下沟通里的流程，收敛为一条可检索、可追溯的路径。",
            "tech": "单体服务加关系型数据库，前端单页；外部依赖保持可替换，避免被单一平台锁死。",
            "ops": "先依靠组织内种子用户冷启动，暂无商业化安排，相关假设待验证。",
            "metrics": "首周完成核心流程的用户数、次日回访率、单次流程耗时。",
        }
        packages = [
            {"name": "需求与契约定稿", "deliverable": "需求规格与接口契约", "optimistic": 2, "likely": 3, "pessimistic": 5},
            {"name": "核心流程实现", "deliverable": "可运行的核心链路", "optimistic": 5, "likely": 8, "pessimistic": 14},
            {"name": "基础数据与记录", "deliverable": "数据模型与存储", "optimistic": 2, "likely": 4, "pessimistic": 6},
            {"name": "界面与异常处理", "deliverable": "可用界面与错误提示", "optimistic": 3, "likely": 5, "pessimistic": 8},
            {"name": "联调与验收", "deliverable": "验收报告与遗留问题清单", "optimistic": 2, "likely": 4, "pessimistic": 7},
        ]
        questions = ["首批种子用户由谁负责，目标是多少？", "是否需要接入既有账号体系？"]
        return {"options": options, "decisions": decisions, "non_goals": non_goals,
                "narrative": narrative, "work_packages": packages, "open_questions": questions}


def isolated_context(idea: str, constraints: list[str]) -> dict[str, Any]:
    """独立发散阶段的上下文：只有输入，没有任何其他角色的产出。"""
    return {"idea": idea, "constraints": deepcopy(constraints), "claims": []}


__all__ = ["Agent", "MockAgent", "DISCUSSION_ROLES", "isolated_context", "brief"]
