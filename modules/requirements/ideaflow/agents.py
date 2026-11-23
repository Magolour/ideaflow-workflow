"""角色 Agent：统一接口与离线实现。

真实模型适配器在后续部分接入，接口保持不变：
``agent.respond(stage, role, context) -> dict``。
这样离线演示、单元测试和真实调用走同一条流程。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Protocol

from .contracts import ContractError, validate_stances, validate_synthesis
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
        if stage == "respond":
            return self._respond(role, context)
        if stage == "review":
            return self._review(role, context)
        if stage == "counterexample":
            return self._counterexample(context)
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

    # ---------- 收敛：回应异议、整合复核、伪共识反例 ----------

    def _respond(self, role: Role, context: dict[str, Any]) -> dict[str, Any]:
        mine = [row for row in context["objections"] if row["target_role"] == role.id]
        responses = []
        for row in mine[:2]:
            text = row["text"]
            if row["severity"] == "blocking":
                position, argument, evidence = "accept", "接受该异议，在方案中补齐对应内容", "rule"
            elif "同质化" in text:
                position, argument, evidence = ("refute",
                                                "收窄范围不等于放弃差异化，差异化能力保留在候选方案 B/C",
                                                "rule")
            elif "供给" in text:
                position, argument, evidence = ("conditional",
                                                "认可供给是前提，但需用数据验证后才能判定",
                                                "assumption")
            elif "单体架构" in text:
                position, argument, evidence = ("refute",
                                                "首版没有高并发证据，单体架构不阻碍体验目标",
                                                "rule")
            elif "单点传播" in text:
                position, argument, evidence = ("refute",
                                                "传播能力已登记为后续版本，不影响首版闭环",
                                                "rule")
            elif "可验收" in text:
                position, argument, evidence = "accept", "接受，改写为可量化的验收项", "rule"
            elif "可替换" in text:
                position, argument, evidence = "accept", "接受，首版增加一层适配并计入工作量", "rule"
            elif row.get("divergence") == "fact":
                position, argument, evidence = ("conditional", "认可问题，但需要先补充证据才能定论",
                                                "assumption")
            else:
                position, argument, evidence = ("refute",
                                                "该异议针对的前提在首版范围外，不构成本期约束",
                                                "rule")
            responses.append({"objection": row["id"], "position": position,
                              "argument": argument, "evidence": evidence})
        return {"role": role.id, "claims": [], "assumptions": [], "objections": [],
                "responses": responses, "questions": []}

    def _review(self, role: Role, context: dict[str, Any]) -> dict[str, Any]:
        adopted = set(context.get("adopted", []))
        not_adopted = set(context.get("not_adopted", []))
        reviews = []
        for claim in context.get("claims", []):
            if claim["id"] in adopted:
                verdict, note = "preserved", "意见已写入决策，未被歪曲"
            elif claim["id"] in not_adopted:
                verdict, note = "deferred", "已登记为未采纳，理由可查"
            else:
                verdict, note = "preserved", "整合稿保留该主张，未发现失真"
            reviews.append({"claim": claim["id"], "verdict": verdict, "note": note})
        return {"role": role.id, "claims": [], "assumptions": [], "objections": [],
                "reviews": reviews, "questions": []}

    def _counterexample(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "skeptic",
            "claims": [{
                "text": "全体一致本身值得怀疑：首版若拿不到首批供给，讨论中的共识无法转化为真实使用",
                "reason": "反例：同类产品在冷启动阶段失败的原因通常是供给不足，而不是方案设计缺陷",
                "evidence": "rule", "assumes": [],
            }],
            "assumptions": [], "objections": [], "questions": [],
        }

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
             "reason": "阻断异议集中在供给与范围，A 能最快验证需求是否真实存在",
             "based_on": [item for item in (pick("ba"), pick("tech")) if item]},
            {"scope": "成功指标", "choice": "首周完成核心流程的用户数 ≥ 20，次日回访率 ≥ 30%",
             "reason": "两项都能从现有数据直接统计，不依赖额外埋点体系",
             "based_on": [item for item in (pick("ba", 1), pick("user")) if item]},
        ]
        not_adopted = [
            {"claim": pick("innovation", 1) or "", "reason": "首版聚焦核心流程，传播能力留到后续版本"},
            {"claim": pick("risk", 1) or "", "reason": "已由非目标与免责声明覆盖，不单独立项"},
        ]
        not_adopted = [row for row in not_adopted if row["claim"]]
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
        return {"options": options, "decisions": decisions, "not_adopted": not_adopted,
                "non_goals": non_goals, "narrative": narrative,
                "work_packages": packages, "open_questions": questions}



# ---------------------------------------------------------------- 真实模型

STAGE_HINTS: dict[str, str] = {
    "clarify": '{"role":"<role id>","questions":["没有答复就无法继续的问题"]}',
    "diverge": '{"role":"<role id>","claims":[{"text":"主张","reason":"依据",'
               '"evidence":"fact|rule|value|assumption","assumes":[0]}],'
               '"assumptions":[{"text":"假设","verify_by":"验证方式"}],'
               '"objections":[],"questions":["待确认问题"]}',
    "critique": '{"role":"<role id>","claims":[],"assumptions":[],'
                '"objections":[{"target":"CLM-001","text":"异议","consequence":"后果",'
                '"severity":"blocking|major|minor"}],"questions":[]}',
    "respond": '{"role":"<role id>","claims":[],"assumptions":[],"objections":[],'
               '"responses":[{"objection":"OBJ-001","position":"accept|refute|conditional",'
               '"argument":"理由","evidence":"fact|rule|value|assumption"}],"questions":[]}',
    "synthesize": '{"options":[{"key":"A","name":"方案名","scope":["范围"],'
                  '"cost_days":"10 天","risk":"风险","tradeoffs":"取舍",'
                  '"based_on":["CLM-001"]}],"decisions":[{"scope":"范围","choice":"选择",'
                  '"reason":"理由","based_on":["CLM-001"]}],"not_adopted":[],'
                  '"non_goals":[{"item":"不做的事","reason":"原因"}],'
                  '"narrative":{"background":"","users":"","value":"","tech":"",'
                  '"ops":"","metrics":""},"work_packages":[{"name":"工作包",'
                  '"deliverable":"交付物","optimistic":1,"likely":2,"pessimistic":4}],'
                  '"open_questions":[]}',
    "sign": '{"role":"<role id>","stances":[{"option":"OPT-001",'
            '"stance":"agree|conditional|oppose|unreviewed","reason":"理由",'
            '"condition":"有条件时的条件"}],"claims":[],"assumptions":[],'
            '"objections":[],"questions":[]}',
    "counterexample": '{"role":"skeptic","claims":[{"text":"反例","reason":"依据",'
                      '"evidence":"rule","assumes":[]}],"assumptions":[],'
                      '"objections":[],"questions":[]}',
    "review": '{"role":"<role id>","reviews":[{"claim":"CLM-001",'
              '"verdict":"preserved|deferred|omitted|distorted","note":"说明"}],'
              '"claims":[],"assumptions":[],"objections":[],"questions":[]}',
    "draft": '{"decisions":[{"requirement_ids":["R-01"],"choice":"方案",'
             '"reason":"理由","cost_days":2.0,"satisfies":"full|partial|declined",'
             '"risk":"风险"}],"claims":[{"text":"主张","requirement_ids":["R-01"],'
             '"evidence":"fact|rule|value|assumption"}],'
             '"assumptions":[{"text":"假设","verify_by":"验证方式"}],'
             '"risks":["风险"]}',
}

STAGE_RULES = {
    "clarify": "只问没有答复就无法继续的问题，最多 3 个。",
    "diverge": "从本角色关注点出发，给 2—3 条有依据的主张，至少 1 条假设。",
    "critique": "只针对其他角色的具体主张提出异议，必须写后果与严重级。",
    "respond": "逐条回应指到本角色的异议，立场三选一，反对要写理由。",
    "synthesize": "至少 2 个候选方案、2 个工作包；只能引用给定的主张编号。",
    "sign": "对每个候选方案给出四态立场之一；有条件必须写条件，反对必须写理由。",
    "counterexample": "提出一个能推翻当前共识的具体反例。",
    "review": "只复核给定的主张，逐条给出 preserved/deferred/omitted/distorted。",
    "draft": "覆盖快照里的每一条明确要求；只能引用给定编号；成本必须大于 0。",
}


class LLMAgent:
    """OpenAI 兼容接口的角色 Agent。

    与 MockAgent 实现同一个 ``respond(stage, role, context)``：调用方不需要知道
    背后是模板还是模型。解析失败会把校验错误回注给模型重试；每次调用都记入
    ``ai_calls.jsonl``（模型、耗时、token、状态），便于审计与成本核算。
    """

    name = "llm"

    def __init__(self, *, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, timeout: float = 60.0, retries: int = 2,
                 audit_path: str | Path | None = None,
                 transport: Callable[[list[dict[str, str]], float, bool],
                                     tuple[str, dict[str, Any]]] | None = None) -> None:
        self.base_url = (base_url or os.getenv("AI_BASE_URL")
                         or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key or os.getenv("AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""
        self.model = model or os.getenv("AI_MODEL") or ""
        if not self.api_key:
            raise ContractError("LLM_NOT_CONFIGURED",
                                "缺少 AI_API_KEY，无法发起真实模型调用", "api_key")
        if not self.model:
            raise ContractError("LLM_NOT_CONFIGURED", "缺少 AI_MODEL", "model")
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.audit_path = Path(audit_path) if audit_path else None
        self.transport = transport
        self._lock = threading.Lock()

    def bind_run(self, run_dir: str | Path) -> None:
        """把审计日志落到某次运行的目录；由 pipeline / specflow 调用。"""
        self.audit_path = Path(run_dir) / "ai_calls.jsonl"

    def respond(self, stage: str, role: Role, context: dict[str, Any]) -> dict[str, Any]:
        if stage not in STAGE_HINTS:
            raise ContractError("UNKNOWN_STAGE", f"LLMAgent 不支持阶段：{stage}", "stage")
        messages = [
            {"role": "system", "content": self._system_prompt(stage, role)},
            {"role": "user", "content": json.dumps(
                {"stage": stage, "role": role.id, "context": context},
                ensure_ascii=False, sort_keys=True)},
        ]
        temperature = 0.2 if stage in ("synthesize", "draft") else role.temperature
        last_error = ""
        for attempt in range(self.retries + 1):
            started = time.perf_counter()
            try:
                content, usage = self._chat(messages, temperature, stage)
                data = self._parse_json(content)
                data = self._postprocess(stage, role, context, data)
            except ContractError as error:
                last_error = f"{error.code}: {error.message}"
                self._log(stage, role, attempt, "invalid", started, usage={}, error=last_error)
                if attempt >= self.retries:
                    break
                messages = messages[:2] + [
                    {"role": "assistant", "content": content if "content" in locals() else ""},
                    {"role": "user", "content": f"输出不合格：{last_error}。"
                                                f"请只返回修正后的 JSON，不要解释。"
                                                f"必须符合这个形状：{STAGE_HINTS[stage]}"},
                ]
                continue
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last_error = f"LLM_UNAVAILABLE: {error}"
                self._log(stage, role, attempt, "error", started, usage={}, error=last_error)
                if attempt >= self.retries:
                    raise ContractError("LLM_UNAVAILABLE",
                                        f"模型调用失败：{error}", "transport") from None
                time.sleep(min(2 ** attempt, 4))
                continue
            self._log(stage, role, attempt, "ok", started, usage=usage)
            return data
        raise ContractError("LLM_BAD_OUTPUT",
                            f"模型输出未通过校验：{last_error}", stage)

    # ---------- 请求与解析 ----------

    def _system_prompt(self, stage: str, role: Role) -> str:
        return (
            f"你是「{role.name}」。关注点：{role.focus}。"
            f"必须回答：{'；'.join(role.must_answer)}。禁区：{role.forbidden}。"
            f"当前阶段：{stage}。{STAGE_RULES[stage]}"
            "只输出一个 JSON 对象，不要 Markdown 围栏、不要解释。"
            f"输出形状：{STAGE_HINTS[stage]}"
        )

    def _chat(self, messages: list[dict[str, str]], temperature: float,
              stage: str) -> tuple[str, dict[str, Any]]:
        if self.transport is not None:
            return self.transport(messages, temperature, True)
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "ignore")[:300]
            if error.code in (408, 409, 429) or error.code >= 500:
                raise urllib.error.URLError(f"HTTP {error.code}: {detail}") from None
            raise ContractError("LLM_REQUEST_FAILED",
                                f"模型返回 HTTP {error.code}：{detail}", "http") from None
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ContractError("LLM_BAD_RESPONSE", "模型响应缺少 choices", "body") from None
        return content, body.get("usage") or {}

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        text = (content or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise ContractError("LLM_BAD_JSON", "输出里没有 JSON 对象", "content") from None
            try:
                data = json.loads(text[start:end + 1])
            except json.JSONDecodeError as error:
                raise ContractError("LLM_BAD_JSON", f"JSON 解析失败：{error}", "content") from None
        if not isinstance(data, dict):
            raise ContractError("LLM_BAD_JSON", "输出必须是 JSON 对象", "content")
        return data

    # ---------- 各阶段的最低结构要求 ----------

    def _postprocess(self, stage: str, role: Role, context: dict[str, Any],
                     data: dict[str, Any]) -> dict[str, Any]:
        if stage == "synthesize":
            known = {row["id"] for row in context.get("claims", [])}
            return validate_synthesis(data, known_claims=known)
        if stage == "sign":
            known = {row["id"] for row in context.get("options", [])}
            return validate_stances(data, role_id=role.id, known_options=known)
        if stage == "draft":
            if not isinstance(data.get("decisions"), list) or not data["decisions"]:
                raise ContractError("EMPTY_DRAFT", "草案没有 decisions", "decisions")
            return data

        for key in ("claims", "assumptions", "objections", "questions"):
            data.setdefault(key, [])
            if not isinstance(data[key], list):
                raise ContractError("INVALID_OUTPUT", f"{key} 必须是数组", key)
        data["role"] = role.id

        if stage == "draft":
            return data
        if stage == "clarify":
            # 模型常把 claims 写成字符串数组；澄清阶段不产生主张，直接丢弃非对象项
            for key in ("claims", "assumptions", "objections"):
                data[key] = [row for row in data.get(key) or [] if isinstance(row, dict)]
            questions = [str(row).strip() for row in data.get("questions") or []
                         if str(row).strip()]
            if not questions:
                raise ContractError("EMPTY_CLARIFY", "澄清阶段没有提出问题", "questions")
            data["questions"] = questions[:3]
        if stage == "critique":
            for index, row in enumerate(data["objections"]):
                if not isinstance(row, dict) or not row.get("target"):
                    raise ContractError("INVALID_OUTPUT", "异议必须指向具体主张",
                                        f"objections[{index}]")
                row.setdefault("severity", "major")
        if stage == "respond":
            for index, row in enumerate(data.get("responses", [])):
                if not isinstance(row, dict) or row.get("position") not in (
                        "accept", "refute", "conditional"):
                    raise ContractError("INVALID_OUTPUT", "回应立场不合法",
                                        f"responses[{index}]")
        if stage == "review":
            for index, row in enumerate(data.get("reviews", [])):
                if not isinstance(row, dict) or row.get("verdict") not in (
                        "preserved", "deferred", "omitted", "distorted"):
                    raise ContractError("INVALID_OUTPUT", "复核结论不合法",
                                        f"reviews[{index}]")
        return data

    # ---------- 审计 ----------

    def _log(self, stage: str, role: Role, attempt: int, status: str,
             started: float, *, usage: dict[str, Any], error: str = "") -> None:
        if self.audit_path is None:
            return
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "stage": stage, "role": role.id, "attempt": attempt,
            "model": self.model, "status": status,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "error": error,
        }
        with self._lock:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def isolated_context(idea: str, constraints: list[str]) -> dict[str, Any]:
    """独立发散阶段的上下文：只有输入，没有任何其他角色的产出。"""
    return {"idea": idea, "constraints": deepcopy(constraints), "claims": []}


__all__ = ["Agent", "MockAgent", "LLMAgent", "DISCUSSION_ROLES",
           "isolated_context", "brief"]
