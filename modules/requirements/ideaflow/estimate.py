"""三点估算（PERT）。

依据文档第二部分 5：
E = (O + 4M + P) / 6，标准差 = (P - O) / 6；
总期望为各工作包期望之和，总标准差为方差和开平方；
对外承诺取 E + 1σ，并在里程碑前留 10%—20% 缓冲。
"""

from __future__ import annotations

from math import sqrt
from typing import Any


def pert(packages: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    total = 0.0
    variance = 0.0
    for item in packages:
        optimistic = float(item["optimistic"])
        likely = float(item["likely"])
        pessimistic = float(item["pessimistic"])
        expected = (optimistic + 4 * likely + pessimistic) / 6
        deviation = (pessimistic - optimistic) / 6
        total += expected
        variance += deviation * deviation
        rows.append({
            **item,
            "expected": round(expected, 1),
            "deviation": round(deviation, 1),
        })
    total_deviation = sqrt(variance)
    return {
        "rows": rows,
        "expected_total": round(total, 1),
        "deviation_total": round(total_deviation, 1),
        # 对外承诺：期望 + 1σ
        "commit_days": round(total + total_deviation, 1),
        "buffer_low": round(total * 0.10, 1),
        "buffer_high": round(total * 0.20, 1),
    }
