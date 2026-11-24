"""需求快照：把用户输入整理成带编号、带版本、可追溯的条目集合。

条目分三类（`requirement` / `constraint` / `open`），编号分别为 `R-01`、
`C-01`、`Q-01`，编号一旦分配就不再复用——草案和规格都靠它回指原始输入。

版本规则：
- 初始解析为 v1，状态 `draft`；
- `confirm()` 只改状态，不改内容，因此不升版本；
- 任何编辑走 `revise()`，版本 +1、状态退回 `draft`，旧版本文件保留只读。

来源规则：每条条目都带 `source{section,start_line,end_line,excerpt}`，
行号从 1 开始，指向原始输入里的真实位置，便于答辩时逐条对回原文。
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

from .contracts import ContractError
from .storage import content_hash, now_iso, read_json, write_json

ITEM_TYPES = ("requirement", "constraint", "open")
ITEM_TYPE_LABEL = {
    "requirement": "明确要求",
    "constraint": "约束",
    "open": "待澄清",
}
ITEM_PREFIX = {"requirement": "R", "constraint": "C", "open": "Q"}
PRIORITIES = ("must", "should", "could")
PRIORITY_LABEL = {"must": "必须", "should": "应当", "could": "可选"}
SNAPSHOT_STATUS = ("draft", "confirmed")

_BULLET = re.compile(r"^\s*(?:[-*+•]|\(?\d+[.)、]|[①-⑳])\s*")
_EXPLICIT = re.compile(r"^\s*(需求|要求|约束|限制|边界|待澄清|澄清|问题)\s*[:：]\s*(.+)$")
_ACCEPTANCE = re.compile(r"验收(?:标准)?\s*(?:是|为|[:：])\s*(.+)$")
_SENTENCE_END = re.compile(r"(?<=[。；;！!？?])")
_CLAUSE = re.compile(r"[，,]")

_PREFIX_TYPE = {
    "需求": "requirement", "要求": "requirement",
    "约束": "constraint", "限制": "constraint", "边界": "constraint",
    "待澄清": "open", "澄清": "open", "问题": "open",
}

# 约束是“对外部边界的限制”，不是功能点；这里只收资源、时间、技术与合规类措辞
CONSTRAINT_HINTS = (
    "期限", "截止", "预算", "成本", "经费", "人力", "只有", "仅限", "仅为",
    "不得", "禁止", "不做", "不能", "不支持", "不引入", "不接", "技术栈",
    "合规", "审核", "天内", "周内", "月内", "人以内", "人以内完成", "上限",
    "不超过", "不超过", "至少", "最多", "兼容", "必须使用", "必须部署",
)
QUESTION_HINTS = (
    "待定", "待确认", "是否", "要不要", "不确定", "暂不明确", "需要确认",
    "可能", "或者", "？", "?",
)
MUST_HINTS = ("必须", "核心", "关键", "首要", "一定要")
COULD_HINTS = ("可选", "最好", "以后", "后续", "加分项", "可以不做")


def _next_id(prefix: str, used: set[str]) -> str:
    number = 1
    while f"{prefix}-{number:02d}" in used:
        number += 1
    return f"{prefix}-{number:02d}"


def _looks_constraint(text: str) -> bool:
    return any(word in text for word in CONSTRAINT_HINTS)


def _looks_question(text: str) -> bool:
    return any(word in text for word in QUESTION_HINTS)


def classify(text: str) -> str:
    """按“待澄清 > 约束 > 明确要求”的顺序判定类型。

    明确的 `约束：` 前缀优先于自动判定；没有前缀时看措辞。
    """
    if _looks_question(text):
        return "open"
    if _looks_constraint(text):
        return "constraint"
    return "requirement"


def _priority(text: str, kind: str) -> str:
    if kind == "constraint":
        return "must"
    if any(word in text for word in MUST_HINTS):
        return "must"
    if any(word in text for word in COULD_HINTS):
        return "could"
    return "should"


def _acceptance(text: str) -> list[str]:
    match = _ACCEPTANCE.search(text)
    if not match:
        return []
    value = match.group(1).strip(" 。；;")
    return [value] if value else []


def _split_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_END.split(text)]
    return [part for part in parts if part]


def _split_clauses(text: str) -> list[str]:
    """约束句里常见“两周内上线，只有两名开发”，按逗号拆成两条更利于核对。"""
    clauses = [clause.strip() for clause in _CLAUSE.split(text) if clause.strip()]
    if len(clauses) > 1 and all(_looks_constraint(clause) for clause in clauses):
        return clauses
    return [text]


def parse_items(raw: str) -> list[dict[str, Any]]:
    """把原文切成条目，保留行号与章节，不在这里分配编号。"""
    items: list[dict[str, Any]] = []
    section = "输入"
    for line_no, line in enumerate(raw.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            section = stripped.lstrip("#").strip() or section
            continue

        text = _BULLET.sub("", stripped).strip()
        if len(text) < 4:
            continue

        forced: str | None = None
        explicit = _EXPLICIT.match(text)
        if explicit:
            forced = _PREFIX_TYPE[explicit.group(1)]
            text = explicit.group(2).strip()
            if len(text) < 2:
                continue

        for sentence in _split_sentences(text):
            kind = forced or classify(sentence)
            pieces = _split_clauses(sentence) if kind == "constraint" else [sentence]
            for piece in pieces:
                clean = piece.strip(" 。；;")
                if len(clean) < 2:
                    continue
                items.append({
                    "type": kind,
                    "text": clean,
                    "priority": _priority(clean, kind),
                    "acceptance": _acceptance(sentence),
                    "source": {
                        "section": section,
                        "start_line": line_no,
                        "end_line": line_no,
                        "excerpt": stripped[:120],
                    },
                })
    return items


def _dedupe(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result = []
    for item in items:
        key = (item["type"], item["text"])
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


@dataclass
class Snapshot:
    snapshot_id: str
    version: int
    status: str
    items: list[dict[str, Any]]
    raw_text: str
    created_at: str = field(default_factory=now_iso)
    parent_version: int | None = None
    confirmed_by: str | None = None
    confirmed_at: str | None = None
    retired_ids: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)

    # ---------- 内容与查询 ----------

    @property
    def hash(self) -> str:
        return content_hash(self._content())

    def _content(self) -> dict[str, Any]:
        keys = ("id", "type", "text", "priority", "acceptance")
        return {"items": [{key: item[key] for key in keys} for item in self.items]}

    def by_id(self, item_id: str) -> dict[str, Any]:
        for item in self.items:
            if item["id"] == item_id:
                return item
        raise ContractError("UNKNOWN_ITEM", f"快照里没有条目 {item_id}", "id")

    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [item for item in self.items if item["type"] == kind]

    @property
    def requirements(self) -> list[dict[str, Any]]:
        return self.of_type("requirement")

    @property
    def constraints(self) -> list[dict[str, Any]]:
        return self.of_type("constraint")

    @property
    def questions(self) -> list[dict[str, Any]]:
        return self.of_type("open")

    def counts(self) -> dict[str, int]:
        return {kind: len(self.of_type(kind)) for kind in ITEM_TYPES}

    def ids(self, kind: str | None = None) -> set[str]:
        rows = self.items if kind is None else self.of_type(kind)
        return {item["id"] for item in rows}

    def ensure_known(self, ids: Iterable[str], *, path: str = "requirement_ids") -> None:
        known = self.ids()
        unknown = [item_id for item_id in ids if item_id not in known]
        if unknown:
            raise ContractError("UNKNOWN_REQUIREMENT",
                                f"引用了快照中不存在的条目：{', '.join(unknown)}", path)

    # ---------- 版本操作 ----------

    def confirm(self, by: str) -> "Snapshot":
        if self.status == "confirmed":
            raise ContractError("ALREADY_CONFIRMED", "快照已经是确认状态", "status")
        if not by.strip():
            raise ContractError("INVALID_FIELD", "确认人不能为空", "confirmed_by")
        confirmed = replace(self, status="confirmed", confirmed_by=by.strip(),
                            confirmed_at=now_iso())
        confirmed.history = self._append_history(confirmed, f"由 {by.strip()} 确认", by.strip())
        return confirmed

    def revise(self, operations: list[dict[str, Any]], *,
               reason: str, actor: str = "human") -> "Snapshot":
        if not operations:
            raise ContractError("NO_OPERATION", "修订至少需要一步操作", "operations")
        if not reason.strip():
            raise ContractError("MISSING_REASON", "修订必须写明原因", "reason")

        items = deepcopy(self.items)
        retired = list(self.retired_ids)
        for index, op in enumerate(operations):
            path = f"operations[{index}]"
            if not isinstance(op, dict):
                raise ContractError("INVALID_FIELD", "操作必须是对象", path)
            kind = op.get("op")
            if kind == "add":
                self._apply_add(items, retired, op, path)
            elif kind == "update":
                self._apply_update(items, op, path)
            elif kind == "remove":
                self._apply_remove(items, retired, op, path)
            elif kind == "retype":
                self._apply_retype(items, op, path)
            else:
                raise ContractError("INVALID_OP", f"不支持的操作 {kind}", path)

        updated = replace(self, version=self.version + 1, status="draft",
                          items=items, retired_ids=retired,
                          parent_version=self.version,
                          confirmed_by=None, confirmed_at=None)
        if updated.hash == self.hash:
            raise ContractError("NO_CHANGE", "修订前后内容一致，未升版本", "operations")
        updated.history = self._append_history(updated, reason.strip(), actor)
        return updated

    def _append_history(self, snapshot: "Snapshot", reason: str,
                        actor: str) -> list[dict[str, Any]]:
        return [*self.history, {"version": snapshot.version, "hash": snapshot.hash,
                                "reason": reason, "actor": actor, "at": now_iso()}]

    def _used_ids(self, items: list[dict[str, Any]], retired: list[str]) -> set[str]:
        return {item["id"] for item in items} | set(retired)

    def _apply_add(self, items: list[dict[str, Any]], retired: list[str],
                   op: dict[str, Any], path: str) -> None:
        kind = op.get("type")
        if kind not in ITEM_TYPES:
            raise ContractError("INVALID_ENUM", f"未知条目类型 {kind}", f"{path}.type")
        text = str(op.get("text", "")).strip()
        if len(text) < 2:
            raise ContractError("EMPTY_TEXT", "条目内容不能为空", f"{path}.text")
        used = self._used_ids(items, retired)
        item_id = _next_id(ITEM_PREFIX[kind], used)
        items.append({
            "id": item_id,
            "type": kind,
            "text": text,
            "priority": op.get("priority") or _priority(text, kind),
            "acceptance": list(op.get("acceptance") or _acceptance(text)),
            "source": {"section": op.get("section", "人工补充"),
                       "start_line": 0, "end_line": 0, "excerpt": text[:120]},
        })

    def _find(self, items: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
        for item in items:
            if item["id"] == item_id:
                return item
        raise ContractError("UNKNOWN_ITEM", f"快照里没有条目 {item_id}", "id")

    def _apply_update(self, items: list[dict[str, Any]], op: dict[str, Any],
                      path: str) -> None:
        item = self._find(items, str(op.get("id", "")))
        if "text" in op:
            text = str(op["text"]).strip()
            if len(text) < 2:
                raise ContractError("EMPTY_TEXT", "条目内容不能为空", f"{path}.text")
            item["text"] = text
        if "priority" in op:
            if op["priority"] not in PRIORITIES:
                raise ContractError("INVALID_ENUM", "优先级取值不合法", f"{path}.priority")
            item["priority"] = op["priority"]
        if "acceptance" in op:
            item["acceptance"] = [str(value).strip() for value in op["acceptance"] if str(value).strip()]

    def _apply_remove(self, items: list[dict[str, Any]], retired: list[str],
                      op: dict[str, Any], path: str) -> None:
        item = self._find(items, str(op.get("id", "")))
        items.remove(item)
        retired.append(item["id"])

    def _apply_retype(self, items: list[dict[str, Any]], op: dict[str, Any],
                      path: str) -> None:
        item = self._find(items, str(op.get("id", "")))
        kind = op.get("type")
        if kind not in ITEM_TYPES:
            raise ContractError("INVALID_ENUM", f"未知条目类型 {kind}", f"{path}.type")
        item["type"] = kind

    def diff(self, other: "Snapshot") -> list[dict[str, Any]]:
        before = {item["id"]: item for item in self.items}
        after = {item["id"]: item for item in other.items}
        changes: list[dict[str, Any]] = []
        for item_id in sorted(set(before) | set(after)):
            if item_id not in after:
                changes.append({"op": "removed", "id": item_id, "text": before[item_id]["text"]})
            elif item_id not in before:
                changes.append({"op": "added", "id": item_id, "text": after[item_id]["text"]})
            else:
                for key in ("type", "text", "priority", "acceptance"):
                    if before[item_id][key] != after[item_id][key]:
                        changes.append({"op": "changed", "id": item_id, "field": key,
                                        "before": before[item_id][key],
                                        "after": after[item_id][key]})
        return changes

    # ---------- 序列化 ----------

    def to_json(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "version": self.version,
            "status": self.status,
            "hash": self.hash,
            "created_at": self.created_at,
            "parent_version": self.parent_version,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at,
            "retired_ids": list(self.retired_ids),
            "counts": self.counts(),
            "source": {"raw_text": self.raw_text, "char_count": len(self.raw_text)},
            "items": [
                {**item, "label": ITEM_TYPE_LABEL[item["type"]],
                 "priority_label": PRIORITY_LABEL[item["priority"]]}
                for item in self.items
            ],
            "history": list(self.history),
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "Snapshot":
        try:
            snapshot = cls(
                snapshot_id=payload["snapshot_id"],
                version=int(payload["version"]),
                status=payload["status"],
                items=payload["items"],
                raw_text=payload.get("source", {}).get("raw_text", ""),
                created_at=payload.get("created_at", now_iso()),
                parent_version=payload.get("parent_version"),
                confirmed_by=payload.get("confirmed_by"),
                confirmed_at=payload.get("confirmed_at"),
                retired_ids=list(payload.get("retired_ids", [])),
                history=list(payload.get("history", [])),
            )
        except KeyError as error:
            raise ContractError("INVALID_SNAPSHOT", f"快照缺少字段 {error}", "snapshot") from None
        if payload.get("hash") and payload["hash"] != snapshot.hash:
            raise ContractError("HASH_MISMATCH", "快照内容与记录的哈希不一致", "hash")
        return snapshot


def _normalize_text(text: Any) -> str:
    """去重用的归一化：去掉空白与标点，统一小写。"""
    return re.sub(r"[\s，。；、,.;:：!！?？（）()\[\]【】\"'“”‘’]+", "",
                  str(text)).lower()


def _discussion_items(board: Any, *, existing: Iterable[str] = (),
                      limit: int | None = None) -> list[dict[str, Any]]:
    """把讨论结论映射成快照条目，正文仍以原文切分为准。

    - 与原文条目、彼此之间做归一化去重；
    - 条目带上提出者与轮次（source.actor / source.round），便于追溯；
    - limit 用于封顶，must 优先保留，防止真实模型把快照撑爆。
    """
    items: list[dict[str, Any]] = []
    seen = {_normalize_text(text) for text in existing}

    def add(kind: str, text: str, priority: str,
            acceptance: list[str] | None = None,
            actor: Any = None, round_no: Any = None,
            key_text: Any = None) -> None:
        clean = str(text).strip()
        key = _normalize_text(clean if key_text is None else key_text)
        if not clean or key in seen:
            return
        seen.add(key)
        source: dict[str, Any] = {
            "section": "讨论补充", "start_line": 0, "end_line": 0,
            "excerpt": clean[:120],
        }
        if actor:
            source["actor"] = str(actor)
        if isinstance(round_no, int) and round_no >= 0:
            source["round"] = round_no
        items.append({
            "type": kind, "text": clean, "priority": priority,
            "acceptance": acceptance or ([] if kind == "open" else _acceptance(clean)),
            "source": source,
        })

    for row in getattr(board, "assumptions", []):
        text = row.get("text") if isinstance(row, dict) else row
        actor = row.get("role") if isinstance(row, dict) else None
        round_no = row.get("round") if isinstance(row, dict) else None
        add("open", f"推断假设：{text}", "should", actor=actor, round_no=round_no,
            key_text=text)

    for row in getattr(board, "open_questions", []):
        if not isinstance(row, dict) or row.get("resolution"):
            continue
        add("open", row.get("text", ""), "must" if row.get("blocking") else "should",
            actor=row.get("source"), round_no=row.get("round"))

    blocking = getattr(board, "unresolved_blocking", None)
    if callable(blocking):
        for row in blocking():
            text = row.get("text", "")
            consequence = row.get("consequence", "")
            add("open", f"未解决阻断：{text}（{consequence}）" if consequence
                else f"未解决阻断：{text}", "must",
                actor=row.get("role"), round_no=row.get("round"))

    for row in getattr(board, "non_goals", []):
        if not isinstance(row, dict):
            continue
        item = row.get("item", "")
        reason = row.get("reason", "")
        if item:
            add("constraint", f"不做 {item}（{reason}）" if reason else f"不做 {item}",
                "must", [f"确认首版不实现 {item}"])

    if limit is not None and len(items) > limit:
        items.sort(key=lambda row: 0 if row["priority"] == "must" else 1)
        items = items[:limit]
    return items


def build(raw_text: str, *, constraints: Iterable[str] | None = None,
          questions: Iterable[str] | None = None, board: Any | None = None,
          snapshot_id: str = "REQSET-001", actor: str = "parser") -> Snapshot:
    """从原始输入构建 v1 快照。额外约束/问题来自命令行或表单；可选吸收讨论结论。"""
    if not raw_text or not raw_text.strip():
        raise ContractError("EMPTY_INPUT", "输入不能为空", "raw_text")

    parsed = parse_items(raw_text)
    for text in constraints or []:
        clean = str(text).strip()
        if not clean:
            continue
        parsed.append({
            "type": "constraint", "text": clean,
            "priority": "must", "acceptance": _acceptance(clean),
            "source": {"section": "补充约束", "start_line": 0, "end_line": 0,
                       "excerpt": clean[:120]},
        })
    for text in questions or []:
        clean = str(text).strip()
        if not clean:
            continue
        parsed.append({
            "type": "open", "text": clean,
            "priority": "should", "acceptance": [],
            "source": {"section": "补充问题", "start_line": 0, "end_line": 0,
                       "excerpt": clean[:120]},
        })

    if board is not None:
        parsed.extend(_discussion_items(
            board,
            existing=[str(row.get("text") or "") for row in parsed],
            limit=max(8, len(parsed) * 3),
        ))

    items = _dedupe(parsed)
    if not items:
        raise ContractError("EMPTY_INPUT", "没有解析出任何条目", "raw_text")

    used: set[str] = set()
    for item in items:
        item["id"] = _next_id(ITEM_PREFIX[item["type"]], used)
        used.add(item["id"])
    items.sort(key=lambda item: (ITEM_TYPES.index(item["type"]), item["id"]))

    snapshot = Snapshot(snapshot_id=snapshot_id, version=1, status="draft",
                        items=items, raw_text=raw_text)
    snapshot.history = [{"version": 1, "hash": snapshot.hash, "reason": "初始解析",
                         "actor": actor, "at": now_iso()}]
    return snapshot


def save(snapshot: Snapshot, directory: str | Path) -> Path:
    """同时写版本文件与 latest 指针，旧版本不被覆盖。"""
    folder = Path(directory)
    versioned = write_json(folder / f"snapshot.v{snapshot.version}.json", snapshot.to_json())
    write_json(folder / "snapshot.json", snapshot.to_json())
    return versioned


def load(path: str | Path) -> Snapshot:
    target = Path(path)
    if target.is_dir():
        target = target / "snapshot.json"
    return Snapshot.from_json(read_json(target))
