"""从约束文本里抽取可计算的边界：期限、人力、禁止事项。

自审查（覆盖／冲突／验收）依赖这三个抽取器，因此单独成模块，
避免把解析规则散落在草案生成与审查逻辑里。
"""

from __future__ import annotations

import re

_NUMBER = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
_DEADLINE = re.compile(r"(\d+|[一二两三四五六七八九十]+)\s*(天|日|周|星期|个月|月)")
_HEADCOUNT = re.compile(r"(\d+|[一二两三四五六七八九十]+)\s*(?:名|个|位)?\s*(?:开发|工程师|人)")
_FORBIDDEN = re.compile(r"(?:不做|不支持|禁止|不得|不引入|不接)\s*([\w\u4e00-\u9fff]{2,10})")


def to_int(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    if token in _NUMBER:
        return _NUMBER[token]
    # 十一、二十这类简单组合
    if "十" in token:
        left, _, right = token.partition("十")
        tens = _NUMBER.get(left, 1) if left else 1
        ones = _NUMBER.get(right, 0) if right else 0
        return tens * 10 + ones
    return None


def deadline_days(text: str) -> int | None:
    """把“两周”“14 天”“1 个月”换算成天；没有期限信息返回 None。"""
    match = _DEADLINE.search(text)
    if not match:
        return None
    amount = to_int(match.group(1))
    if amount is None:
        return None
    unit = match.group(2)
    if unit in ("周", "星期"):
        return amount * 7
    if unit in ("个月", "月"):
        return amount * 30
    return amount


def replace_deadline(text: str, days: int) -> str:
    """替换期限片段，保留同一条约束里的其余内容。"""
    return _DEADLINE.sub(f"{days} 天", text, count=1)


def headcount(text: str) -> int | None:
    """“只有两名开发”“2 人” → 2。"""
    match = _HEADCOUNT.search(text)
    if not match:
        return None
    return to_int(match.group(1))


def forbidden_subjects(text: str) -> list[str]:
    """抽取被明确排除的东西：不做在线支付 → 在线支付。"""
    return [match.strip() for match in _FORBIDDEN.findall(text) if match.strip()]


def shares_topic(left: str, right: str, *, window: int = 2) -> bool:
    """粗略判断两段文字是否在说同一件事。

    长主题（≥6 字）要求 3 字连续重叠，避免“跨校区自提调度”与“自提点”
    因共享“自提”被误判；短主题（如“在线支付”）仍用 2 字，保留“支付”这类判断。
    最终是否算冲突仍由人工问题兜底。
    """
    if not left or not right:
        return False
    size = 3 if len(left) >= 6 else window
    grams = {left[index:index + size] for index in range(len(left) - size + 1)}
    return any(gram in right for gram in grams if gram.strip())
