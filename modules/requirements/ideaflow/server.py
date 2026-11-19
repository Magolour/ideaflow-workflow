"""本地 Web 工作台：输入想法 → 生成设计文档 → 在线预览与下载。

只依赖标准库。生成放在后台线程，前端轮询阶段日志，因此即便将来接上
真实模型（单次调用数十秒）也不会把 HTTP 请求拖到超时；同一进程内可以
同时跑多个任务，结果互不覆盖。
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
import uuid
import webbrowser
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from .contracts import (CONSENSUS_LABEL, DIVERGENCE_LABEL, SEVERITY_LABEL,
                        STANCE_LABEL, ContractError)
from .pipeline import run
from .render_html import PAPER_CSS, document_html, render_markdown
from .roles import ALL_ROLES, DISCUSSION_ROLES
from .specflow import CASE_NAMES, load_case, run as run_spec

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}
MAX_IDEA = 1200
MAX_CONSTRAINTS = 12
MAX_CONSTRAINT_LEN = 120
MAX_BODY = 64 * 1024

QID = re.compile(r"^Q-\d{3}$")

STATUS_LABEL = {"open": "未解决", "resolved": "已关闭"}
OUTCOME_LABEL = {"accept": "已接受", "refute": "已反驳"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# ---------------------------------------------------------------- 任务模型


@dataclass
class Job:
    id: str
    idea: str
    constraints: list[str]
    answers: dict[str, str]
    max_rounds: int
    threshold: float
    flow: str = "proposal"
    case: str | None = None
    state: str = "queued"
    logs: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, str] | None = None
    result: dict[str, Any] | None = None
    run_dir: Path | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def log(self, text: str, store: "JobStore | None" = None) -> None:
        entry = {"at": round(time.time() - self.started_at, 3), "text": text}
        if store is None:
            self.logs.append(entry)
            return
        with store.lock:
            self.logs.append(entry)

    def view(self, *, include_result: bool = True) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "state": self.state,
            "flow": self.flow,
            "case": self.case,
            "idea": self.idea,
            "constraints": list(self.constraints),
            "logs": list(self.logs),
            "elapsed_ms": round(((self.finished_at or time.time()) - self.started_at) * 1000),
        }
        if self.error:
            payload["error"] = self.error
        if include_result and self.result:
            payload["result"] = self.result
        return payload


class JobStore:
    """内存任务表：保存任务状态、运行目录与最近的结果。"""

    def __init__(self, runs_dir: Path, *, keep: int = 40) -> None:
        self.runs_dir = runs_dir
        self.keep = keep
        self.lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._runs: dict[str, Path] = {}

    def create(self, **kwargs: Any) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], **kwargs)
        with self.lock:
            self._jobs[job.id] = job
            if len(self._jobs) > self.keep:
                for stale in sorted(self._jobs.values(), key=lambda item: item.started_at)[:-self.keep]:
                    self._jobs.pop(stale.id, None)
        return job

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self._jobs.get(job_id)

    def attach_run(self, job: Job, run_id: str, path: Path) -> None:
        with self.lock:
            self._runs[run_id] = path
            job.run_dir = path

    def run_dir(self, run_id: str) -> Path | None:
        with self.lock:
            return self._runs.get(run_id)

    def submit(self, job: Job) -> None:
        threading.Thread(target=self._execute, args=(job,), daemon=True).start()

    # ---------- 后台执行 ----------

    def _execute(self, job: Job) -> None:
        job.state = "running"

        def progress(message: str) -> None:
            job.log(message, self)

        if job.flow == "spec":
            self._execute_spec(job, progress)
            return

        try:
            result = run(job.idea, constraints=job.constraints, out_dir=self.runs_dir,
                         answers=job.answers, max_rounds=job.max_rounds,
                         threshold=job.threshold, progress=progress)
        except ContractError as error:
            self._fail(job, error.code, error.message, error.path)
            return
        except Exception as error:  # noqa: BLE001 - 任何异常都要变成可读的失败信息
            self._fail(job, "INTERNAL_ERROR", f"{type(error).__name__}: {error}", "")
            return

        try:
            payload = _result_payload(result)
        except Exception as error:  # noqa: BLE001
            self._fail(job, "RESULT_ERROR", f"结果整理失败：{error}", "")
            return

        self.attach_run(job, result.run_id, result.out_dir)
        job.log("完成：文档已写入运行目录", self)
        job.finished_at = time.time()
        job.result = payload
        job.state = "done"

    def _execute_spec(self, job: Job, progress: Any) -> None:
        try:
            raw = load_case(job.case) if job.case else job.idea
            result = run_spec(raw, job.constraints, out_dir=self.runs_dir,
                              case_name=job.case, progress=progress)
        except ContractError as error:
            self._fail(job, error.code, error.message, error.path)
            return
        except Exception as error:  # noqa: BLE001
            self._fail(job, "INTERNAL_ERROR", f"{type(error).__name__}: {error}", "")
            return

        self.attach_run(job, result.run_id, result.out_dir)
        job.log("完成：规格与审查记录已写入运行目录", self)
        job.finished_at = time.time()
        job.result = result.payload
        job.state = "done"

    def _fail(self, job: Job, code: str, message: str, path: str) -> None:
        job.log(f"失败：{code} {message}", self)
        job.error = {"code": code, "message": message, "path": path}
        job.finished_at = time.time()
        job.state = "error"


def _result_payload(result: Any) -> dict[str, Any]:
    markdown = result.proposal_path.read_text(encoding="utf-8")
    board = result.board.snapshot()
    meta_path = result.out_dir / "run.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    counts = result.report["counts"]

    options = []
    for option in board["options"]:
        reasons = option.get("stance_reasons", {})
        tally = Counter(item["stance"] for item in reasons.values())
        options.append({
            **{key: option[key] for key in ("id", "key", "name", "scope", "cost_days",
                                            "risk", "tradeoffs", "based_on")},
            "stances": {ALL_ROLES[role].name if role in ALL_ROLES else role: item["stance"]
                        for role, item in reasons.items()},
            "tally": {STANCE_LABEL[state]: tally.get(state, 0) for state in STANCE_LABEL},
        })

    objections = []
    for row in board["objections"]:
        response = row.get("response") or {}
        objections.append({
            "id": row["id"],
            "role": row["role"],
            "role_name": ALL_ROLES[row["role"]].name if row["role"] in ALL_ROLES else row["role"],
            "target": row["target"],
            "text": row["text"],
            "consequence": row.get("consequence", ""),
            "severity": row["severity"],
            "severity_label": SEVERITY_LABEL[row["severity"]],
            "status": row["status"],
            "status_label": OUTCOME_LABEL.get(row.get("outcome", ""), STATUS_LABEL[row["status"]]),
            "divergence": DIVERGENCE_LABEL.get(board["divergence"].get(row["id"], ""), ""),
            "resolution": row.get("resolution", ""),
            "response": {"position": response.get("position", ""),
                         "argument": response.get("argument", ""),
                         "by": response.get("role", "")},
        })

    questions = [{"id": row["id"], "text": row["text"], "blocking": row["blocking"],
                  "resolution": row["resolution"], "owner": row["owner"]}
                 for row in board["open_questions"]]

    return {
        "run_id": result.run_id,
        "created_at": meta.get("created_at", ""),
        "agent": meta.get("agent", ""),
        "idea": board["idea"],
        "constraints": board["constraints"],
        "markdown": markdown,
        "html": render_markdown(markdown),
        "counts": counts,
        "support": max((row["ratio"] for row in result.report["support"]), default=0.0),
        "option_support": result.report["support"],
        "consensus": {"state": board["consensus"]["state"],
                      "label": CONSENSUS_LABEL[board["consensus"]["state"]],
                      "reason": board["consensus"]["reason"]},
        "termination": result.report["termination"],
        "options": options,
        "decisions": board["decisions"],
        "objections": objections,
        "questions": questions,
        "assumptions": board["assumptions"],
        "work_packages": board["work_packages"],
        "non_goals": board["non_goals"],
        "roles": [{"id": role.id, "name": role.name} for role in DISCUSSION_ROLES],
    }


# ---------------------------------------------------------------- 请求解析


def _clean_text(value: Any, *, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "INVALID_FIELD", f"{field} 必须是字符串")
    text = value.strip()
    if len(text) > limit:
        raise ApiError(400, "TOO_LONG", f"{field} 超过 {limit} 字")
    return text


def _parse_generate(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(400, "INVALID_BODY", "请求体必须是 JSON 对象")

    flow = payload.get("flow", "proposal")
    if flow not in ("proposal", "spec"):
        raise ApiError(400, "INVALID_FLOW", "flow 只支持 proposal 或 spec")

    case = payload.get("case") or None
    if case is not None and case not in CASE_NAMES:
        raise ApiError(400, "INVALID_CASE", "未知的案例名")

    idea = _clean_text(payload.get("idea", ""), field="想法", limit=MAX_IDEA)
    if not idea and not case:
        raise ApiError(400, "EMPTY_IDEA", "请先写下一句想法")

    raw_constraints = payload.get("constraints", [])
    if isinstance(raw_constraints, str):
        raw_constraints = raw_constraints.splitlines()
    if not isinstance(raw_constraints, list):
        raise ApiError(400, "INVALID_FIELD", "constraints 必须是字符串数组")

    constraints: list[str] = []
    for item in raw_constraints:
        text = _clean_text(item, field="约束", limit=MAX_CONSTRAINT_LEN)
        if text and text not in constraints:
            constraints.append(text)
    if len(constraints) > MAX_CONSTRAINTS:
        raise ApiError(400, "TOO_MANY_CONSTRAINTS", f"约束最多 {MAX_CONSTRAINTS} 条")

    raw_answers = payload.get("answers", {}) or {}
    if not isinstance(raw_answers, dict):
        raise ApiError(400, "INVALID_FIELD", "answers 必须是对象")
    answers: dict[str, str] = {}
    for key, value in raw_answers.items():
        if not QID.match(str(key)):
            raise ApiError(400, "INVALID_FIELD", f"澄清问题编号格式应为 Q-001，收到 {key}")
        text = _clean_text(value, field=f"答复 {key}", limit=300)
        if text:
            answers[str(key)] = text
    if len(answers) > 20:
        raise ApiError(400, "TOO_MANY_ANSWERS", "答复最多 20 条")

    try:
        max_rounds = int(payload.get("max_rounds", 2))
    except (TypeError, ValueError):
        raise ApiError(400, "INVALID_FIELD", "讨论轮次必须是整数") from None
    if not 1 <= max_rounds <= 4:
        raise ApiError(400, "INVALID_FIELD", "讨论轮次应在 1—4 之间")

    try:
        threshold = float(payload.get("threshold", 0.7))
    except (TypeError, ValueError):
        raise ApiError(400, "INVALID_FIELD", "共识阈值必须是数字") from None
    if not 0.3 <= threshold <= 1.0:
        raise ApiError(400, "INVALID_FIELD", "共识阈值应在 0.3—1.0 之间")

    return {"idea": idea or case or "", "constraints": constraints, "answers": answers,
            "max_rounds": max_rounds, "threshold": round(threshold, 2),
            "flow": flow, "case": case}


# ---------------------------------------------------------------- HTTP 层


def make_server(host: str, port: int, runs_dir: str | Path, *,
                quiet: bool = False) -> ThreadingHTTPServer:
    base = Path(runs_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    store = JobStore(base)

    class Handler(BaseHTTPRequestHandler):
        server_version = "IdeaFlow/0.1"
        protocol_version = "HTTP/1.1"

        # ---------- 路由 ----------

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
            route = urlparse(self.path)
            try:
                if route.path in ("/", "/index.html"):
                    self._send_static("index.html")
                elif route.path.startswith("/static/"):
                    self._send_static(route.path[len("/static/"):])
                elif route.path == "/api/health":
                    self._send_json(HTTPStatus.OK, {"ok": True, "mode": "mock",
                                                    "runs_dir": str(base)})
                elif route.path == "/api/case":
                    self._handle_case(parse_qs(route.query))
                elif route.path == "/api/job":
                    self._handle_job(parse_qs(route.query))
                elif route.path == "/api/download":
                    self._handle_download(parse_qs(route.query))
                else:
                    raise ApiError(404, "NOT_FOUND", f"找不到 {route.path}")
            except ApiError as error:
                self._send_error(error)

        def do_POST(self) -> None:  # noqa: N802
            route = urlparse(self.path)
            try:
                if route.path != "/api/generate":
                    raise ApiError(404, "NOT_FOUND", f"找不到 {route.path}")
                payload = self._read_json()
                options = _parse_generate(payload)
                job = store.create(**options)
                store.submit(job)
                self._send_json(HTTPStatus.ACCEPTED, {"job_id": job.id,
                                                      "state": job.state})
            except ApiError as error:
                self._send_error(error)

        # ---------- 处理函数 ----------

        def _handle_case(self, query: dict[str, list[str]]) -> None:
            name = (query.get("name") or [""])[0]
            try:
                raw = load_case(name)
            except ContractError as error:
                raise ApiError(404, error.code, error.message) from None
            lines = [line for line in raw.splitlines() if not line.strip().startswith("#")]
            text = "\n".join(lines).strip()
            title = next((line.strip().lstrip("#").strip()
                          for line in raw.splitlines() if line.strip()), name)
            self._send_json(HTTPStatus.OK, {"name": name, "title": title, "text": text})

        def _handle_job(self, query: dict[str, list[str]]) -> None:
            job_id = (query.get("id") or [""])[0]
            job = store.get(job_id)
            if job is None:
                raise ApiError(404, "UNKNOWN_JOB", "任务不存在或已过期，请重新生成")
            self._send_json(HTTPStatus.OK, {"job": job.view()})

        def _handle_download(self, query: dict[str, list[str]]) -> None:
            run_id = (query.get("run_id") or [""])[0]
            artifact = (query.get("artifact") or [""])[0]
            run_dir = store.run_dir(run_id)
            if run_dir is None:
                raise ApiError(404, "UNKNOWN_RUN", "找不到该次运行，请先生成文档")

            if artifact:
                self._send_artifact(run_dir, artifact, run_id)
                return

            fmt = (query.get("format") or ["md"])[0]
            if fmt not in ("md", "html"):
                raise ApiError(400, "INVALID_FORMAT", "format 只支持 md 或 html")

            target = (run_dir / "proposal.md").resolve()
            if not target.is_relative_to(base) or not target.exists():
                raise ApiError(404, "UNKNOWN_RUN", "运行目录不在允许范围内")

            markdown = target.read_text(encoding="utf-8")
            if fmt == "md":
                body = markdown.encode("utf-8")
                content_type = "text/markdown; charset=utf-8"
            else:
                body = document_html(markdown).encode("utf-8")
                content_type = "text/html; charset=utf-8"
            self._send_file(body, content_type, f"{run_id}.{fmt}", f"design-document.{fmt}")

        def _send_artifact(self, run_dir: Path, artifact: str, run_id: str) -> None:
            if artifact not in ("spec-md", "spec-json", "question"):
                raise ApiError(400, "INVALID_ARTIFACT", "artifact 只支持 spec-md、spec-json、question")
            if artifact == "question":
                target = (run_dir / "human_question.json").resolve()
                content_type = "application/json; charset=utf-8"
                fallback, suffix = "human-question.json", "question.json"
            else:
                meta_path = (run_dir / "spec.json").resolve()
                if not meta_path.exists():
                    raise ApiError(404, "UNKNOWN_SPEC", "该次运行没有规格文件")
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                artifacts = meta.get("artifacts", {})
                key = "markdown" if artifact == "spec-md" else "json"
                name = artifacts.get(key)
                if not name:
                    raise ApiError(404, "UNKNOWN_SPEC", "规格缺少导出记录")
                target = (run_dir / name).resolve()
                content_type = ("text/markdown; charset=utf-8" if artifact == "spec-md"
                                else "application/json; charset=utf-8")
                fallback, suffix = (("design-spec.md", "spec.md") if artifact == "spec-md"
                                    else ("design-spec.json", "spec.json"))

            if not target.is_relative_to(base) or not target.exists():
                raise ApiError(404, "UNKNOWN_RUN", "运行目录不在允许范围内")
            self._send_file(target.read_bytes(), content_type,
                            f"{run_id}-{suffix}", fallback)

        def _send_file(self, body: bytes, content_type: str, filename: str,
                       fallback: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition",
                             f"attachment; filename=\"{fallback}\"; "
                             f"filename*=UTF-8''{quote(filename)}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> Any:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ApiError(400, "INVALID_LENGTH", "Content-Length 不合法") from None
            if length <= 0:
                raise ApiError(400, "EMPTY_BODY", "请求体为空")
            if length > MAX_BODY:
                raise ApiError(413, "BODY_TOO_LARGE", "请求体过大")
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ApiError(400, "INVALID_JSON", "请求体不是合法的 UTF-8 JSON") from None

        def _send_static(self, name: str) -> None:
            if name == "paper.css":  # 与下载用的 HTML 共用同一份排版规则
                body = PAPER_CSS.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/css; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

            target = (WEB_DIR / name).resolve()
            if not target.is_relative_to(WEB_DIR) or not target.is_file():
                raise ApiError(404, "NOT_FOUND", "静态资源不存在")
            body = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type",
                             STATIC_TYPES.get(target.suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, error: ApiError) -> None:
            self._send_json(error.status,
                            {"error": {"code": error.code, "message": error.message}})

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            if not quiet:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}", flush=True)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ideaflow-web",
        description="启动本地 Web 工作台：输入想法，生成并下载设计文档")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="端口，默认 8000")
    parser.add_argument("--out", default="runs", help="运行产物目录，默认 runs/")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args(argv)

    try:
        server = make_server(args.host, args.port, args.out)
    except OSError as error:
        print(f"启动失败：{error}")
        print(f"端口 {args.port} 可能被占用，换一个：--port {args.port + 1}")
        return 1

    host, port = server.server_address[:2]
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{shown}:{port}/"
    print(f"IdeaFlow 工作台已启动：{url}")
    print(f"产物目录：{Path(args.out).resolve()}")
    print("按 Ctrl+C 停止。")
    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
