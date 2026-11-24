"""可视化契约夹具（成员5 自用，不参与生产链路）。

为什么需要它：`/api/progress` 里的 `decisions`、`delivery`、`requirement_refs`、
`design_refs`、`artifact_ids` 属于契约 v2 已定义、后端尚未透传的字段（见 contracts/README.md
的「待实现」表）。等工作台去等后端，前端就没法验证交付确认、来源与产物这些界面。

这个夹具按 `contracts/v2/openapi.yaml` 的 Progress / TaskState / Edge、
`delivery.schema.json`、`decision.schema.json` 直接返回**契约合规**的样例数据，
并复用真实的 `modules/orchestrator/web/index.html`，因此可以：
  1) 验证前端在「字段齐全」时的渲染（交付确认、决策记录、来源、产物）；
  2) 给成员6 当 UI 回归夹具（同一份 payload 可以断言 DOM）。

用法：
    python modules/visualization/progress_fixture.py --port 8012
    # 浏览器打开 http://127.0.0.1:8012/

注意：这不是生产服务。真实链路仍然是 modules/orchestrator/python/agent.py。
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "modules" / "orchestrator" / "web" / "index.html"

NOW = "2026-11-20T19:55:00+08:00"


def ts(minute: int, second: int = 0) -> str:
    return f"2026-11-20T19:{minute:02d}:{second:02d}+08:00"


# ---------------------------------------------------------------- 样例数据
TASKS = [
    {
        "task_id": "T1", "name": "确认交易范围与规则", "status": "done", "attempts": 0,
        "suggested_role": "human", "priority": "high", "deliverable": "范围与规则说明",
        "acceptance_items": ["范围评审通过", "线下交易免责条款确认"],
        "requirement_refs": ["R-01", "R-02"], "design_refs": ["M-01"],
        "depends_on": [], "write_paths": ["docs/scope.md"], "estimate_days": 1,
    },
    {
        "task_id": "T2", "name": "书籍发布与搜索", "status": "done", "attempts": 0,
        "suggested_role": "ai", "priority": "high", "deliverable": "发布 / 搜索页面",
        "acceptance_items": ["可发布带图书籍信息", "可按书名与学院搜索"],
        "requirement_refs": ["R-03", "R-04"], "design_refs": ["M-02", "M-03"],
        "depends_on": ["T1"], "write_paths": ["web/books/*"], "estimate_days": 3,
        "artifact_ids": ["ART-1001", "ART-1002"],
    },
    {
        "task_id": "T3", "name": "线下交易地点与时段", "status": "done", "attempts": 1,
        "suggested_role": "ai", "priority": "medium", "deliverable": "地点 / 时段选择模块",
        "acceptance_items": ["可选校内地点", "可选交易时段"],
        "requirement_refs": ["R-05"], "design_refs": ["M-04"],
        "depends_on": ["T1"], "write_paths": ["web/places/*"], "estimate_days": 2,
        "artifact_ids": ["ART-1003"],
    },
    {
        "task_id": "T4", "name": "交易流程联调", "status": "done", "attempts": 0,
        "suggested_role": "ai", "priority": "high", "deliverable": "联调记录与缺陷清单",
        "acceptance_items": ["主流程走通", "缺陷登记完整"],
        "requirement_refs": ["R-06"], "design_refs": ["M-05"],
        "depends_on": ["T2", "T3"], "write_paths": ["docs/integration.md"], "estimate_days": 2,
        "artifact_ids": ["ART-1004"],
    },
    {
        "task_id": "T5", "name": "验收用例与回归", "status": "done", "attempts": 0,
        "suggested_role": "ai", "priority": "high", "deliverable": "验收用例与回归报告",
        "acceptance_items": ["用例覆盖发布/搜索/交易", "回归全部通过"],
        "requirement_refs": ["R-06", "R-07"], "design_refs": ["M-06"],
        "depends_on": ["T4"], "write_paths": ["tests/*"], "estimate_days": 1.5,
        "artifact_ids": ["ART-1005"],
    },
    {
        "task_id": "T6", "name": "交付清单与说明", "status": "done", "attempts": 0,
        "suggested_role": "ai", "priority": "low", "deliverable": "交付清单与启动说明",
        "acceptance_items": ["清单齐全", "启动说明可复现"],
        "requirement_refs": ["R-08"], "design_refs": ["M-07"],
        "depends_on": ["T5"], "write_paths": ["README.md"], "estimate_days": 1,
        "artifact_ids": ["ART-1006"],
    },
]

EDGES = [
    {"from": "T1", "to": "T2", "type": "force", "reason": "范围先定"},
    {"from": "T1", "to": "T3", "type": "force", "reason": "范围先定"},
    {"from": "T2", "to": "T4", "type": "force"},
    {"from": "T3", "to": "T4", "type": "force"},
    {"from": "T4", "to": "T5", "type": "force"},
    {"from": "T5", "to": "T6", "type": "force"},
    {"from": "T2", "to": "T6", "type": "data", "reason": "交付清单引用发布接口契约"},
    {"from": "T3", "to": "T6", "type": "resource", "reason": "共用图片存储配额（不阻塞）"},
]

DECISIONS = [
    {
        "decision_id": "DEC-0001", "target_type": "task", "target_id": "T1",
        "target_version": 1, "action": "approve", "actor": "human:组长",
        "reason": "范围与免责条款确认", "created_at": ts(43, 12),
    },
    {
        "decision_id": "DEC-0002", "target_type": "task", "target_id": "T3",
        "target_version": 1, "action": "return", "return_to": "plan",
        "actor": "human:组长", "reason": "地点选项缺少下雨天室内备选",
        "affected_ids": ["T3", "T4"], "created_at": ts(47, 5),
    },
    {
        "decision_id": "DEC-0003", "target_type": "task", "target_id": "T5",
        "target_version": 2, "action": "approve", "actor": "human:组长",
        "created_at": ts(52, 40),
    },
]

DELIVERY = {
    "delivery_id": "DLV-20261120-01",
    "instance_id": "INST-2026-0007",
    "plan_id": "PLAN-20261120-02",
    "items": [
        {"task_id": "T1", "artifact_ids": ["ART-1000"], "acceptance": "passed"},
        {"task_id": "T2", "artifact_ids": ["ART-1001", "ART-1002"], "acceptance": "passed"},
        {"task_id": "T3", "artifact_ids": ["ART-1003"], "acceptance": "passed", "note": "雨天备选已补"},
        {"task_id": "T4", "artifact_ids": ["ART-1004"], "acceptance": "passed"},
        {"task_id": "T5", "artifact_ids": ["ART-1005"], "acceptance": "passed"},
        {"task_id": "T6", "artifact_ids": ["ART-1006"], "acceptance": "passed"},
    ],
    "status": "draft",
    "created_at": ts(54, 2),
}

EVENTS = [
    ("system", "instance", "INST-2026-0007", "created", "实例创建，绑定计划 PLAN-20261120-02（规格 v2）"),
    ("system", "spec", "SPEC-2026-002", "spec_confirmed", "设计规格 v2 已确认，7 个模块、8 条需求条款"),
    ("system", "plan", "PLAN-20261120-02", "plan_validated", "计划校验通过：6 个任务、8 条依赖、无环"),
    ("system", "plan", "PLAN-20261120-02", "plan_confirmed", "计划 v1 确认，进入编排"),
    ("system", "graph", "G-v1.0", "graph_built", "建图完成：6 个任务、4 个并行组、无环"),
    ("system", "task", "T1", "task_dispatched", "派发 确认交易范围与规则（human）"),
    ("system", "instance", "INST-2026-0007", "human_gate_entered", "人工节点 T1：确认交易范围与规则，等待人工操作"),
    ("human:组长", "instance", "INST-2026-0007", "human_gate_exited", "approve T1：范围与免责条款确认"),
    ("human:组长", "decision", "DEC-0001", "decision_recorded", "approve T1：范围与免责条款确认"),
    ("system", "task", "T2", "task_dispatched", "派发 书籍发布与搜索（ai）"),
    ("system", "task", "T3", "task_dispatched", "派发 线下交易地点与时段（ai）"),
    ("ai", "task", "T2", "task_done", "发布与搜索页面完成"),
    ("human:组长", "task", "T3", "return", None),
    ("system", "change_request", "CR-20261120-01", "change_submitted", "变更提交（plan）：地点选项补充下雨天室内备选"),
    ("system", "change_request", "CR-20261120-01", "change_confirmed", "变更确认：影响 2 个任务，新增 0 个任务、0 条依赖"),
    ("system", "graph", "G-v1.1", "replan_done", "增量重排完成：重置 2 个任务，当前 6 个任务、4 个并行组"),
    ("system", "task", "T3", "task_reopened", "恢复 2 个任务为 pending：T3、T4"),
    ("ai", "task", "T3", "task_retried", "首次执行缺少室内备选，第 1 次自动重试"),
    ("ai", "task", "T3", "task_done", "地点与时段模块完成（含室内备选）"),
    ("ai", "task", "T4", "task_done", "联调记录与缺陷清单归档"),
    ("ai", "task", "T5", "task_done", "验收用例与回归报告完成"),
    ("ai", "task", "T6", "task_done", "交付清单与说明完成"),
    ("ai", "task", "T2", "artifact_created", "登记产物 ART-1001、ART-1002"),
    ("system", "instance", "INST-2026-0007", "delivery_created", "全部任务终态，生成交付记录 DLV-20261120-01，等待人工确认"),
    ("system", "instance", "INST-2026-0007", "human_gate_entered", "交付确认：DLV-20261120-01 等待人工确认"),
]


class Fixture:
    """内存状态：只服务可视化联调，不做业务判断。"""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.status = "delivery_pending"
        self.decisions = [dict(item) for item in DECISIONS]
        self.delivery = json.loads(json.dumps(DELIVERY, ensure_ascii=False))
        self.waiting_on = {
            "gate_type": "delivery_confirm", "waiting_for": "human",
            "object_id": self.delivery["delivery_id"], "since": ts(54, 30),
        }
        self.seq = 0
        self.log: list[dict] = []
        for actor, object_type, object_id, action, detail in EVENTS:
            if detail is None:
                continue
            self.emit(actor, object_type, object_id, action, detail)

    def emit(self, actor: str, object_type: str, object_id: str, action: str, detail: str) -> None:
        self.seq += 1
        self.log.append({
            "seq": self.seq, "ts": ts(55, min(59, self.seq)), "actor": actor,
            "object_type": object_type, "object_id": object_id,
            "action": action, "detail": detail,
        })

    def progress(self) -> dict:
        done = sum(1 for t in TASKS if t["status"] == "done")
        return {
            "instance_id": "INST-2026-0007",
            "status": self.status,
            "waiting_on": self.waiting_on,
            "plan_id": "PLAN-20261120-02",
            "spec_version": "v2",
            "list_version": "v1.2",
            "graph_id": "G-v1.1",
            "total": len(TASKS),
            "done": done,
            "failed": 0, "blocked": 0, "skipped": 0, "running": 0,
            "pending": len(TASKS) - done,
            "percent": round(100 * done / len(TASKS), 1),
            "analysis": "计划来自 spec（plan_adapter）：6 个任务、4 个并行组；"
                        "resource 边不参与分层（共用配额提示）",
            "last_error": "",
            "tasks": TASKS,
            "edges": EDGES,
            "layers": [["T1"], ["T2", "T3"], ["T4", "T5"], ["T6"]],
            "execution_order": ["T1", "T2", "T3", "T4", "T5", "T6"],
            "decisions": self.decisions,
            "delivery": self.delivery,
        }

    def human_op(self, packet: dict) -> None:
        action = str(packet.get("action") or "")
        operator = str(packet.get("operator") or "web")
        reason = str(packet.get("reason") or "")
        if action not in {"approve", "reject"}:
            raise ValueError("夹具只演示交付确认：action 仅支持 approve / reject")
        if action == "reject" and not reason:
            raise ValueError("退回必须填写理由")
        delivery_id = self.delivery["delivery_id"]
        decision = {
            "decision_id": f"DEC-{len(self.decisions) + 1:04d}",
            "target_type": "delivery",
            "target_id": delivery_id,
            "target_version": 1,
            "action": "approve" if action == "approve" else "reject",
            "actor": f"human:{operator}",
            "created_at": ts(56, 10),
        }
        if reason:
            decision["reason"] = reason
        self.decisions.append(decision)
        self.emit(f"human:{operator}", "decision", decision["decision_id"], "decision_recorded",
                  f"{decision['action']} {delivery_id}" + (f"：{reason}" if reason else ""))
        if action == "approve":
            self.delivery["status"] = "confirmed"
            self.delivery["decided_by"] = f"human:{operator}"
            self.delivery["decided_at"] = ts(56, 12)
            self.delivery["decision_id"] = decision["decision_id"]
            self.status = "completed"
            self.waiting_on = None
            self.emit(f"human:{operator}", "instance", "INST-2026-0007", "delivery_confirmed",
                      f"交付确认 {delivery_id}，实例完成")
        else:
            self.delivery["status"] = "rejected"
            self.delivery["decided_by"] = f"human:{operator}"
            self.delivery["decided_at"] = ts(56, 12)
            self.emit(f"human:{operator}", "instance", "INST-2026-0007", "delivery_rejected",
                      f"交付退回 {delivery_id}：{reason}")


FIXTURE = Fixture()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path in {"/", "/index.html"}:
            body = WEB.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/health":
            self._json(200, {"status": "ok", "started": True,
                             "instance_status": FIXTURE.status, "fixture": True})
            return
        if parsed.path == "/api/progress":
            query = parse_qs(parsed.query)
            with FIXTURE.lock:
                payload = FIXTURE.progress()
                if "since" in query or "limit" in query:
                    since = int(query.get("since", ["0"])[0] or 0)
                    limit = min(int(query.get("limit", ["200"])[0] or 200), 1000)
                    remaining = [e for e in FIXTURE.log if e["seq"] > since]
                    events = remaining[:limit]
                    payload.update({
                        "event_log": events,
                        "next_since": events[-1]["seq"] if events else since,
                        "has_more": len(remaining) > limit,
                    })
            self._json(200, payload)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            packet = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "非法 JSON"})
            return
        if parsed.path == "/api/events":
            try:
                with FIXTURE.lock:
                    if packet.get("type") != "human_op":
                        raise ValueError("夹具只支持 human_op（交付确认）；执行侧请用真实编排器")
                    FIXTURE.human_op(packet)
                self._json(202, {"accepted": 1, "queue_size": 0, "progress": FIXTURE.progress()})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            return
        if parsed.path == "/api/start":
            self._json(409, {"error": "夹具实例已启动，刷新页面即可看到交付确认场景"})
            return
        self._json(404, {"error": "not found"})

    def log_message(self, fmt: str, *args) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="可视化契约夹具")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8012)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"夹具已启动：http://{args.host}:{args.port}/  （页面复用 modules/orchestrator/web/index.html）")
    print("场景：交付待确认（delivery_pending）+ 决策记录 + 任务来源/产物齐全")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
