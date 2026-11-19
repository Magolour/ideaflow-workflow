"""落盘与内容哈希：各阶段共用的小工具。

所有中间产物都用“临时文件 + 原子替换”写入，避免中途失败留下半个 JSON；
内容哈希统一走 `content_hash`，保证 JSON 与 Markdown 指向同一版内容。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def canonical(payload: Any) -> str:
    """稳定序列化：键排序、无多余空格，用于计算哈希。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(payload: Any) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def write_text(path: str | Path, text: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, target)
    return target


def write_json(path: str | Path, payload: Any) -> Path:
    return write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))
