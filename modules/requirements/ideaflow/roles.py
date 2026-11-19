"""角色卡：差异化关注点、必答问题与禁区。

设计依据：文档第一部分第 2 章。角色不等于不同模型，但必须有不同的
关注点、必答问题与输出模板，否则多智能体只是在制造同质化文本。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Role:
    id: str
    name: str
    focus: str
    must_answer: tuple[str, ...]
    forbidden: str
    # 收敛类角色需要低随机性，发散类允许更高温度（文档 2.3）
    temperature: float = 0.7


DISCUSSION_ROLES: tuple[Role, ...] = (
    Role(
        id="ba",
        name="需求分析",
        focus="识别用户、场景、问题与功能需求，指出信息缺口",
        must_answer=(
            "这个想法要解决的具体问题是什么？",
            "哪些需求是原始描述里没有、但必须补的？",
            "哪些结论目前只是猜测，需要列为待确认？",
        ),
        forbidden="无法推断的内容必须写入待确认项，不得用推测填充后当作事实。",
        temperature=0.6,
    ),
    Role(
        id="user",
        name="用户视角",
        focus="用户画像、使用场景、痛点真实性与使用路径",
        must_answer=(
            "目标用户在什么场景下会打开它？",
            "现有替代方案是什么，迁移成本有多大？",
            "哪一步最可能让用户放弃？",
        ),
        forbidden="模拟用户观点必须标为假设，不代表真实用户认可。",
        temperature=0.8,
    ),
    Role(
        id="tech",
        name="技术专家",
        focus="技术可行性、依赖、实现难点与技术风险",
        must_answer=(
            "首版用什么最小技术方案可以跑通？",
            "有哪些外部依赖或平台限制？",
            "哪里最容易在联调阶段出问题？",
        ),
        forbidden="不确定的外部事实必须标注假设，不给无依据的工期承诺。",
        temperature=0.6,
    ),
    Role(
        id="innovation",
        name="创新顾问",
        focus="替代方案、差异化能力与改进方向",
        must_answer=(
            "与现有做法相比，差异化在哪里？",
            "有没有更小的切口可以先验证？",
            "哪些所谓创新其实是伪需求？",
        ),
        forbidden="建议必须针对目标场景，不做空泛创新。",
        temperature=0.9,
    ),
    Role(
        id="risk",
        name="风险评估",
        focus="质疑前提，识别成本、进度、安全、数据与实施风险",
        must_answer=(
            "最可能让项目失败的三件事是什么？",
            "哪些风险在首版就必须处理？",
            "风险发生时的止损方案是什么？",
        ),
        forbidden="风险必须指向具体条目并说明后果，不能只写“有风险”。",
        temperature=0.7,
    ),
    Role(
        id="skeptic",
        name="质疑者",
        focus="挑战关键假设、寻找逻辑跳跃与反例",
        must_answer=(
            "本轮最关键的一个假设是什么，它的反例是什么？",
            "如果这个假设不成立，方案还剩什么？",
            "有没有被大家忽略的利益相关方？",
        ),
        forbidden="禁止以附和为主要输出；每轮至少攻击一个关键假设并给出具体反例。",
        temperature=1.0,
    ),
)

SYNTHESIZER = Role(
    id="synthesizer",
    name="记录员/整合者",
    focus="去重聚类、保留来源、生成候选方案与 Proposal 草案",
    must_answer=(
        "哪些观点可以合并，哪些必须分开保留？",
        "少数意见和重大风险是否都写进了方案？",
        "哪些问题必须留给人工裁决？",
    ),
    forbidden="只做结构化，不做价值判断；不得删除少数意见。",
    temperature=0.3,
)

HOST = Role(
    id="host",
    name="主持人",
    focus="组织讨论、判断收敛、维护共识文档",
    must_answer=(
        "关键目标是否清晰？",
        "重大异议是否已解决或有缓解？",
        "是否满足终止条件？",
    ),
    forbidden="不站立场、不投票、不删除少数意见。",
    temperature=0.2,
)

ALL_ROLES: dict[str, Role] = {role.id: role for role in (*DISCUSSION_ROLES, SYNTHESIZER, HOST)}

# 盲评权重（文档 4.2），第三部分实现评分引擎时使用
EVALUATION_WEIGHTS = {
    "user_value": 0.35,
    "feasibility": 0.25,
    "cost": 0.20,
    "risk": 0.20,
}
EVALUATION_LABEL = {
    "user_value": "用户价值",
    "feasibility": "技术可行性",
    "cost": "成本与周期",
    "risk": "风险可控程度",
}


def role_card(role_id: str) -> Role:
    try:
        return ALL_ROLES[role_id]
    except KeyError:
        raise KeyError(f"未知角色：{role_id}") from None
