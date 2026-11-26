"""放行与当前正文、快照和审查一致的规格。"""

import hashlib
import json
import re


class SpecGateError(RuntimeError):
    def __init__(self, code, message, *, status=409, details=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or {}


def _digest(payload):
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def spec_body_hash(spec):
    metadata = {"hash", "status", "created_at", "revisions", "escalation", "review"}
    return _digest({key: value for key, value in spec.items() if key not in metadata})


def _spec_ref(spec):
    return {key: spec.get(key) for key in ("spec_id", "version", "hash")}


def _valid_ref(ref, id_key):
    return (
        isinstance(ref, dict)
        and set(ref) == {id_key, "version", "hash"}
        and isinstance(ref[id_key], str) and bool(ref[id_key])
        and type(ref["version"]) is int and ref["version"] > 0
        and isinstance(ref["hash"], str) and re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", ref["hash"]) is not None
    )


def validate_spec_review(spec):
    review = spec.get("review")
    if not isinstance(review, dict) or review.get("status") == "draft":
        raise SpecGateError("SPEC_APPROVAL_REQUIRED", "请提交已完成审查的规格")
    expected = _spec_ref(spec)
    snapshot = spec.get("snapshot_ref")
    audit = review.get("audit")
    details = {"expected": expected, "actual": review.get("spec_ref")}
    if (not _valid_ref(expected, "spec_id") or not _valid_ref(snapshot, "snapshot_id")
            or review.get("spec_ref") != expected or review.get("snapshot_ref") != snapshot
            or expected["hash"].removeprefix("sha256:") != spec_body_hash(spec)
            or not isinstance(audit, dict)):
        raise SpecGateError("SPEC_AUDIT_MISMATCH", "规格正文、版本或快照与审查记录不匹配",
                            details=details)

    issues, checks = audit.get("issues"), audit.get("checks")
    audit_hash = audit.get("hash")
    if (audit.get("spec_ref") != expected or audit.get("snapshot_ref") != snapshot
            or not isinstance(issues, list)
            or any(not isinstance(issue, dict) or issue.get("severity")
                   not in ("blocking", "major", "minor") for issue in issues)
            or not isinstance(checks, dict)
            or not isinstance(audit_hash, str)
            or audit_hash.removeprefix("sha256:") != _digest(
                {"spec_ref": expected, "issues": issues, "checks": checks})):
        raise SpecGateError("SPEC_AUDIT_MISMATCH", "审查文件内容或绑定引用不匹配",
                            details=details)

    counts = {severity: sum(issue["severity"] == severity for issue in issues)
              for severity in ("blocking", "major", "minor")}
    counts["total"] = len(issues)
    passed = counts["blocking"] == 0
    if audit.get("counts") != counts or audit.get("passed") is not passed:
        raise SpecGateError("SPEC_AUDIT_MISMATCH", "审查计数或结论与问题列表不匹配",
                            details=details)

    question = review.get("question")
    status = review.get("status")
    if question is not None and (
            not isinstance(question, dict) or not question.get("id")
            or not question.get("question") or question.get("spec_ref") != expected
            or question.get("snapshot_ref") != snapshot):
        raise SpecGateError("SPEC_AUDIT_MISMATCH", "人工问题与当前规格或快照不匹配",
                            details=details)

    if (passed and question is None and spec.get("escalation") is None
            and (status, spec.get("status")) == ("approved", "confirmed")):
        return
    if (not passed and question
            and (status, spec.get("status")) == ("awaiting_human", "awaiting_human")
            and spec.get("escalation") == question):
        raise SpecGateError("SPEC_HUMAN_REQUIRED", "规格需要人工决策，复审通过后请重新提交",
                            details={"question": question, "audit": audit})
    if (not passed and question is None and spec.get("escalation") is None
            and (status, spec.get("status")) == ("blocked", "blocked")):
        raise SpecGateError("SPEC_BLOCKED_NO_QUESTION", "规格审查有阻断，暂无可提交的人工问题",
                            status=422, details={"audit": audit})
    raise SpecGateError("SPEC_STATE_MISMATCH", "规格状态与审查结论不一致",
                        details={"audit": audit})


def attach_local_review(spec, audit, run_id):
    """从本次需求模块写出的审查文件补齐旧版输出，外部导入不走此路径。"""
    expected = _spec_ref(spec)
    snapshot = spec.get("snapshot_ref")
    question = spec.get("escalation")
    if question:
        question = {**question, "spec_ref": expected, "snapshot_ref": snapshot, "run_id": run_id}
    review_status, spec_status = ("approved", "confirmed") if audit.get("passed") is True else (
        ("awaiting_human", "awaiting_human") if question else ("blocked", "blocked"))
    review = {"status": review_status, "spec_ref": expected, "snapshot_ref": snapshot,
              "audit": audit, "question": question}
    return {**spec, "status": spec_status, "escalation": question, "review": review}
