"""应用当前人工问题的回答，并发布重新审查后的规格版本。"""

from contextlib import contextmanager
from dataclasses import replace
from math import ceil
from pathlib import Path
import os

from .audit import audit_spec
from .constraints import deadline_days, replace_deadline
from .contracts import ContractError
from .render_html import render_markdown
from .revisions import save_revisions, scope_revision
from .snapshot import load, save
from .spec import load_spec
from .spec_review import finalize_review, save_reviewed_spec
from .storage import content_hash, now_iso, read_json, write_json


@contextmanager
def _answer_lock(root: Path):
    """跨 HTTP 线程和 CLI 进程串行化同一运行的回答。"""
    with (root / ".spec-answer.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ContractError("ANSWER_IN_PROGRESS", "该运行正在处理另一个回答", "run_id") from error
        # 关闭文件时由系统释放锁。
        yield


def _extend_schedule(spec, snapshot, question, reason, actor):
    ids = question["affected"]["constraints"]
    if len(ids) != 1:
        raise ContractError("MANUAL_REVISION_REQUIRED", "无法唯一确定需要更新的期限约束", "option")
    item = snapshot.by_id(ids[0])
    before = deadline_days(item["text"])
    days = ceil(spec.effort["commit_days"])
    if item["type"] != "constraint" or before is None or days <= before:
        raise ContractError("MANUAL_REVISION_REQUIRED", "期限已变更，请重新核对", "option")
    updated = snapshot.revise([
        {"op": "update", "id": item["id"], "text": replace_deadline(item["text"], days)}
    ], reason=reason, actor=actor).confirm(actor)
    return replace(spec, version=spec.version + 1, created_at=now_iso(),
                   snapshot_version=updated.version, snapshot_hash=updated.hash), updated


def answer_question(run_dir: str | Path, spec_ref: dict, question_id: str,
                    option: str, answer: str, actor: str) -> dict:
    root = Path(run_dir)
    if not all((root / name).is_file() for name in ("spec.json", "flow.json", "manifest.json")):
        raise ContractError("UNKNOWN_RUN", "找不到完整的规格运行产物", "run_id")
    fields = {"question_id": question_id, "option": option, "answer": answer, "actor": actor}
    if (not isinstance(spec_ref, dict) or set(spec_ref) != {"spec_id", "version", "hash"}
            or type(spec_ref["version"]) is not int or spec_ref["version"] < 1
            or not all(isinstance(spec_ref[key], str) and spec_ref[key] for key in ("spec_id", "hash"))
            or any(not isinstance(value, str) or not value.strip() for value in fields.values())):
        raise ContractError("INVALID_BODY", "请提交完整的规格引用、问题、选项、回答和处理人", "answer")
    answer, actor = answer.strip(), actor.strip()
    with _answer_lock(root):
        spec = load_spec(root)
        current = {"spec_id": spec.spec_id, "version": spec.version, "hash": spec.hash}
        record_name = f"answer.v{spec_ref['version']}.json"
        record_path = root / record_name
        if spec_ref != current:
            if record_path.exists() and spec_ref["version"] < spec.version:
                prior = read_json(record_path)
                if prior["spec_ref"] == spec_ref and prior["question_id"] == question_id:
                    raise ContractError("QUESTION_ALREADY_ANSWERED", "该问题已经处理", "question_id")
            raise ContractError("STALE_SPEC_REF", "规格引用已过期，请刷新当前版本", "spec_ref")
        if spec.status != "awaiting_human" or not spec.escalation:
            raise ContractError("SPEC_NOT_AWAITING_HUMAN", "当前规格没有待回答的问题", "question_id")
        question = spec.escalation
        if question["id"] != question_id:
            raise ContractError("STALE_QUESTION", "问题已变更，请刷新", "question_id")
        if option not in {row["key"] for row in question["options"]}:
            raise ContractError("INVALID_BODY", "提交的选项不属于当前问题", "option")
        if question["type"] != "SCHEDULE_CONFLICT" or option not in ("A", "B"):
            raise ContractError("MANUAL_REVISION_REQUIRED", "此选项需要人工修订后再审查", "option")

        snapshot = load(root / f"snapshot.v{spec.snapshot_version}.json")
        if snapshot.hash != spec.snapshot_hash or snapshot.snapshot_id != spec.snapshot_id:
            raise ContractError("STALE_SPEC_REF", "规格绑定的快照不匹配", "spec_ref")
        flow = read_json(root / "flow.json")
        manifest = read_json(root / "manifest.json")
        old_snapshot_version = snapshot.version
        if option == "A":
            revised, snapshot = _extend_schedule(spec, snapshot, question, answer, actor)
        else:
            audit = audit_spec(spec, snapshot)
            revised, revision, _ = scope_revision(spec, snapshot, audit)
            revision["actor"] = actor

        audit = audit_spec(revised, snapshot)
        audit["spec_version"] = revised.version
        reviewed = finalize_review(revised, snapshot, audit, root.name)
        prepared = save_reviewed_spec(reviewed, snapshot, root, publish=False)
        if snapshot.version != old_snapshot_version:
            save(snapshot, root)
        if option == "B":
            save_revisions(reviewed, root)
        record = {"spec_ref": current, "question_id": question_id, "option": option,
                  "answer": answer, "actor": actor, "created_at": now_iso(),
                  "result_ref": reviewed.review["spec_ref"]}
        write_json(record_path, record)

        flow.update(status=reviewed.status,
                    spec={"id": reviewed.spec_id, "version": reviewed.version, "hash": reviewed.hash},
                    question=reviewed.escalation["id"] if reviewed.escalation else None)
        write_json(root / "flow.json", flow)
        new_files = [record_name, prepared["json"], prepared["markdown"],
                     f"audit.v{reviewed.version}.json", f"snapshot.v{snapshot.version}.json"]
        if reviewed.escalation:
            new_files.append(f"human_question.v{reviewed.version}.json")
        if option == "B":
            new_files.extend([f"revision.v{reviewed.version}.json", "revisions.json"])
        manifest["files"] = list(dict.fromkeys(manifest["files"] + new_files))
        hashes = {row["file"]: row["hash"] for row in manifest.get("extras", [])}
        for name in (*new_files, "flow.json"):
            payload = (root / name).read_text(encoding="utf-8") if name.endswith(".md") else read_json(root / name)
            hashes[name] = content_hash(payload)
        hashes["spec.md"] = content_hash(prepared["markdown_text"])
        if reviewed.escalation:
            hashes["human_question.json"] = content_hash(reviewed.escalation)
        manifest["extras"] = [{"file": name, "hash": digest} for name, digest in sorted(hashes.items())]
        write_json(root / "manifest.json", manifest)
        save_reviewed_spec(reviewed, snapshot, root)
        return {"status": reviewed.status, "spec": reviewed.to_json(), "audit": reviewed.review["audit"],
                "question": reviewed.escalation, "snapshot": snapshot.to_json(),
                "counts": snapshot.counts(), "markdown": prepared["markdown_text"],
                "html": render_markdown(prepared["markdown_text"])}
