"""把实际审查结论绑定到对应的规格和快照。"""

from dataclasses import replace
from math import ceil
from pathlib import Path

from .audit import audit_hash, save_audit
from .escalate import build_question, save_question
from .snapshot import Snapshot
from .spec import Spec
from .spec_doc import export_spec
from .storage import content_hash, write_json


def finalize_review(spec: Spec, snapshot: Snapshot, audit: dict, run_id: str) -> Spec:
    question = None if audit["passed"] else build_question(spec, snapshot, audit)
    if question:
        schedule = question["type"] == "SCHEDULE_CONFLICT"
        question.update({
            "id": f"HUMAN-Q-{content_hash([run_id, spec.hash])[:12]}",
            "spec_ref": audit["spec_ref"],
            "snapshot_ref": audit["snapshot_ref"],
            "run_id": run_id,
        })
        for option in question["options"]:
            option["automatic"] = schedule and option["key"] in ("A", "B")
            if schedule and option["key"] == "A":
                days = ceil(spec.effort["commit_days"])
                option.update(text=f"把期限放宽到 {days} 天", deadline_days=days)
    if audit["passed"]:
        status, review_status = "confirmed", "approved"
    elif question:
        status, review_status = "awaiting_human", "awaiting_human"
    else:
        status = review_status = "blocked"
    review = {"status": review_status, "spec_ref": audit["spec_ref"],
              "snapshot_ref": audit["snapshot_ref"],
              "audit": {**audit, "hash": audit_hash(audit)}, "question": question}
    return replace(spec, status=status, escalation=question, review=review)


def save_reviewed_spec(spec: Spec, snapshot: Snapshot, directory: Path,
                       *, publish: bool = True) -> dict:
    audit = spec.review["audit"]
    if publish:
        save_audit(audit, directory, spec.version)
    else:
        write_json(directory / f"audit.v{spec.version}.json", audit)
    if spec.escalation:
        write_json(directory / f"human_question.v{spec.version}.json", spec.escalation)
        if publish:
            save_question(spec.escalation, directory)
    return export_spec(spec, snapshot, directory, publish=publish)
