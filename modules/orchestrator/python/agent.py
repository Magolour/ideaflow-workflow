"""单 Agent HTTP 编排服务：外部上传任务 JSON 后建图，事件驱动增量迭代。

启动：
    py -3 python/agent.py serve --host 127.0.0.1 --port 8000 --out output/chat_miniapp

服务启动时不读取任务文件；POST /api/start 接收外部 JSON。
上传原文保存为 json/uploaded_tasks.json；GET /api/progress 查询状态，带 since 查询事件。
"""

import argparse
import itertools
import json
import os
import queue as queuelib
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from entry import RequirementsUnavailable, normalize_tasks, parse_start, reviewed_replacement
from plan_adapter import plan_from_spec
from plan_review import review_plan
from spec_gate import SpecGateError

PYTHON_DIR = Path(__file__).resolve().parent
ROOT = PYTHON_DIR.parent
UPLOAD_PATH = ROOT / "json" / "uploaded_tasks.json"
WEB_PAGE = ROOT / "web" / "index.html"
DEFAULT_BASE_URL = "https://ws-381dxklyh659xv9g.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash-1017"
EDGE_TYPES = {"force", "data", "resource"}
BLOCKING_EDGE_TYPES = {"force", "data"}  # resource 只表示共享资源，不参与调度阻塞


def load_env() -> None:
    env_file = PYTHON_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        if key.strip() and value.strip():
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------- AI 分析 ----------

SYSTEM_PROMPT = (
    "你是任务依赖分析器。根据给定任务清单，分析任务之间的依赖关系，只输出 JSON 对象，不要解释。"
    "依赖只有三类：force（强制前置）、data（数据依赖）、resource（资源共享，不阻塞）。"
    "规则：只允许引用清单中存在的任务编号；禁止自依赖；禁止任何环。"
    "只保留直接前置依赖：若 A 已依赖 B、B 已依赖 C，不要再给出 A 依赖 C 的传递边。"
    "依赖要精简，只保留真正卡住后续任务的边，总边数不超过任务数的 1.5 倍。"
    '输出格式：{"edges":[{"from":"T1","to":"T2","type":"force","reason":"简短理由"}],"summary":"一句话分析结论"}'
)


def chat_json(system: str, user: str) -> dict:
    api_key = os.getenv("AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""
    if not api_key:
        raise SystemExit("未找到 AI_API_KEY，请写入 .env 或使用 --mock")
    base_url = os.getenv("AI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    payload = {
        "model": os.getenv("AI_MODEL", DEFAULT_MODEL),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=float(os.getenv("AI_TIMEOUT", "90"))) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"模型接口 HTTP {exc.code}：{exc.read().decode('utf-8', 'replace')[:300]}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SystemExit(f"模型接口不可达：{exc}")

    content = data["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.strip("`")
        content = content[4:] if content.lower().startswith("json") else content
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("模型未返回 JSON 对象")
    return json.loads(content[start:end + 1])


def validate_edges(raw_edges, task_ids: set[str]) -> list[dict]:
    edges, seen = [], set()
    for item in raw_edges or []:
        source, target = str(item.get("from", "")).strip(), str(item.get("to", "")).strip()
        edge_type = str(item.get("type", "force")).strip()
        if source not in task_ids or target not in task_ids:
            raise ValueError(f"依赖边端点不存在：{source} -> {target}")
        if source == target:
            raise ValueError(f"任务不能依赖自身：{source}")
        if edge_type not in EDGE_TYPES:
            raise ValueError(f"依赖类型非法：{edge_type}")
        key = (source, target, edge_type)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"from": source, "to": target, "type": edge_type,
                      "reason": str(item.get("reason") or "")})
    return edges


def topo_layers(task_ids: list[str], edges: list[dict]) -> tuple[list[list[str]], list[str]]:
    """确定性 Kahn 分层；有环直接报错，不自动拆环。"""
    indegree = {task_id: 0 for task_id in task_ids}
    children = {task_id: [] for task_id in task_ids}
    for edge in edges:
        if edge.get("type") not in BLOCKING_EDGE_TYPES:
            continue
        indegree[edge["to"]] += 1
        children[edge["from"]].append(edge["to"])

    layers, remaining = [], set(task_ids)
    while remaining:
        layer = sorted(task_id for task_id in remaining if indegree[task_id] == 0)
        if not layer:
            raise ValueError(f"检测到循环依赖，涉及任务：{sorted(remaining)}")
        layers.append(layer)
        for task_id in layer:
            remaining.discard(task_id)
            for child in children[task_id]:
                indegree[child] -= 1
    return layers, [task_id for layer in layers for task_id in layer]


def planned_edges(tasks: list[dict]) -> list[dict]:
    """计划自带 depends_on 时直接转成 force 边，不再调用模型。"""
    task_ids = {task["task_id"] for task in tasks}
    edges, seen = [], set()
    for task in tasks:
        for source in task.get("depends_on") or []:
            source = str(source).strip()
            if source not in task_ids:
                raise ValueError(f"计划依赖引用了不存在的任务：{source} -> {task['task_id']}")
            key = (source, task["task_id"])
            if key in seen:
                continue
            seen.add(key)
            edges.append({"from": source, "to": task["task_id"],
                          "type": "force", "reason": "计划自带依赖"})
    return edges


def analyze(tasks: list[dict], mock: bool) -> tuple[list[dict], str]:
    if any(task.get("depends_on") for task in tasks):
        edges = planned_edges(tasks)
        topo_layers([task["task_id"] for task in tasks], edges)
        return edges, "采用计划自带依赖（未调用模型）"
    if mock:
        edges = [{"from": tasks[i]["task_id"], "to": tasks[i + 1]["task_id"],
                  "type": "force", "reason": "mock 模式按清单顺序串行"}
                 for i in range(len(tasks) - 1)]
        return edges, "mock 模式：按清单顺序串行（未调用模型）"

    task_ids = {task["task_id"] for task in tasks}
    user = json.dumps({"tasks": tasks}, ensure_ascii=False)
    last_error = None
    for attempt in range(1, 3):
        try:
            data = chat_json(SYSTEM_PROMPT, user)
            edges = validate_edges(data.get("edges"), task_ids)
            topo_layers([task["task_id"] for task in tasks], edges)
            return edges, str(data.get("summary") or "").strip()
        except (ValueError, KeyError, TypeError) as exc:
            last_error = exc
            print(f"  第 {attempt} 次校验未通过：{exc}")
    raise SystemExit(f"模型输出连续 2 次未通过校验，转人工：{last_error}")


# ---------- 可视化 ----------

def draw_graph(tasks, edges, layers, out_path: Path, summary: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.patches import FancyArrowPatch

    for font_name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC"):
        try:
            font_manager.findfont(font_name, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [font_name]
            break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False

    nodes = {task["task_id"]: task for task in tasks}
    positions, max_rows = {}, max(len(layer) for layer in layers)
    for index, layer in enumerate(layers):
        for row, task_id in enumerate(layer):
            offset = (max_rows - len(layer)) / 2
            positions[task_id] = (index, -(row + offset))

    width = max(8, 2.3 * len(layers) + 3)
    height = max(5, 1.15 * max_rows + 2.6)
    figure, axes = plt.subplots(figsize=(width, height))
    axes.set_xlim(-0.8, len(layers) - 0.2)
    axes.set_ylim(-max_rows - 0.6, 0.9)
    axes.axis("off")

    colors = {"ai": "#4C78A8", "human": "#E45756", "ai+human": "#F58518"}
    for edge in edges:
        start, end = positions[edge["from"]], positions[edge["to"]]
        axes.add_patch(FancyArrowPatch(
            start, end, arrowstyle="-|>", mutation_scale=12,
            color="#9AA0A6" if edge["type"] != "data" else "#7B61FF",
            linewidth=1.2, linestyle="--" if edge["type"] == "data" else "-",
            shrinkA=22, shrinkB=22, connectionstyle="arc3,rad=0.08"))

    for task_id, (x, y) in positions.items():
        task = nodes[task_id]
        color = colors.get(task["suggested_role"], "#4C78A8")
        label = task_id
        axes.text(x, y + 0.06, label, ha="center", va="center", fontsize=10,
                  fontweight="bold", color="white",
                  bbox=dict(boxstyle="round,pad=0.55", facecolor=color,
                            edgecolor="white", linewidth=1.2), zorder=3)
        axes.text(x, y - 0.30, task["name"][:10], ha="center", va="center",
                  fontsize=7.5, color="#333333", zorder=3)

    for index in range(len(layers)):
        axes.text(index, 0.62, f"第 {index + 1} 层", ha="center", fontsize=9, color="#777777")

    axes.set_title(
        f"任务依赖图 · {len(tasks)} 个任务 / {len(layers)} 个并行组 / {len(edges)} 条依赖",
        fontsize=13, fontweight="bold", pad=16)
    if summary:
        figure.text(0.5, 0.02, f"AI 分析：{summary[:80]}", ha="center", fontsize=9, color="#555555")
    figure.tight_layout()
    figure.savefig(out_path, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(figure)


# ---------- 实时编排运行时（serve 模式） ----------

PRIORITIES = {"high": 0, "medium": 1, "low": 2}
TERMINAL = {"done", "skipped"}
MAX_RETRY = 2

IMPACT_PROMPT = (
    "你是变更影响分析器。根据变更描述、现有任务与依赖边，找出受影响任务，必要时补新增任务和依赖，只输出 JSON。"
    '输出格式：{"impacted_task_ids":["T2"],"new_tasks":[{"task_id":"T13","name":"",'
    '"deliverable":"","acceptance_items":[""],"suggested_role":"ai|human|ai+human",'
    '"priority":"high|medium|low","estimate_days":2.0}],'
    '"new_edges":[{"from":"T3","to":"T13","type":"force"}],"summary":"一句话结论"}'
    "规则：不得改动已完成任务编号；新增任务编号从现有最大编号递增；依赖端点必须存在；"
    "禁止自依赖和环；不需要新增就返回空数组。"
)

PM_PROMPT = (
    "你是项目 PM。把用户给出的自然语言需求拆成可执行任务清单，只输出 JSON 对象，不要解释。"
    '输出格式：{"tasks":[{"task_id":"T1","name":"","deliverable":"",'
    '"acceptance_items":[""],"suggested_role":"ai|human|ai+human",'
    '"priority":"high|medium|low"}],"summary":"一句话结论"}'
    "规则：任务 5 到 12 个，编号从 T1 连续递增；每个任务必须有明确交付物和验收项；"
    "需要人工确认的节点用 human 或 ai+human。"
)


def pm_breakdown(requirement: str, mock: bool = False) -> tuple[list[dict], str]:
    """把自然语言需求拆成任务清单；mock 模式不调用模型。"""
    if mock:
        return [{
            "task_id": "T1", "name": requirement[:40] or "需求拆解",
            "deliverable": "需求拆解文档", "acceptance_items": ["需求可执行"],
            "suggested_role": "ai", "priority": "high",
        }], "mock 模式：按需求文本生成单个占位任务"
    last_error = None
    for attempt in range(1, 3):
        try:
            try:
                data = chat_json(PM_PROMPT, requirement)
            except SystemExit as exc:
                raise ValueError(str(exc))
            tasks, _ = normalize_tasks(data.get("tasks") or [])
            return tasks, str(data.get("summary") or "").strip()
        except (ValueError, KeyError, TypeError) as exc:
            last_error = exc
            print(f"  需求拆解第 {attempt} 次未通过校验：{exc}")
    raise ValueError(f"需求拆解连续 2 次未通过校验：{last_error}")


class RealtimeAgent:
    """按优先级处理数据包，维护实例状态并追加事件流。"""

    def __init__(self, out_dir: Path = ROOT / "output", mock: bool = False,
                 auto_exec: bool = False, upload_path: Path = UPLOAD_PATH) -> None:
        self.lock = threading.RLock()
        self.out_dir = out_dir
        self.upload_path = upload_path
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = out_dir / "events.jsonl"
        self.decisions_path = out_dir / "decisions.jsonl"
        self.state_path = out_dir / "runtime_state.json"
        self.mock = mock
        self.auto_exec = auto_exec
        self.instance_id = "INST-2026-0001"
        self.spec_version = "v1.0"
        self.list_version = "v1.0"
        self.graph_id = "G-v1.0"
        self.plan_id: str | None = None
        self.plan: dict | None = None
        self.spec: dict | None = None
        self.plan_review: dict | None = None
        self.auto_execute = False
        self.analysis_summary = ""
        self.tasks: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.layers: list[list[str]] = []
        self.order: list[str] = []
        self.status = "created"
        self.waiting_on: dict | None = None
        self.started = False
        self.last_error = ""
        self.seq = self._last_seq()
        self.log: list[dict] = []
        self.decisions: list[dict] = []
        self.delivery: dict | None = None
        self.artifact_seq = 0
        self._auto_thread: threading.Thread | None = None
        self.queue: queuelib.PriorityQueue = queuelib.PriorityQueue()
        self._tick = itertools.count()
        threading.Thread(target=self._worker, daemon=True).start()

    def _last_seq(self) -> int:
        if not self.events_path.exists():
            return 0
        last = 0
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                last = max(last, int(json.loads(line)["seq"]))
        return last

    def emit(self, actor: str, object_type: str, object_id: str,
             action: str, detail: str) -> None:
        with self.lock:
            terminal = sum(1 for t in self.tasks.values() if t["status"] in TERMINAL)
            text = f"{detail}｜进度 {terminal}/{len(self.tasks)}" if self.tasks else detail
            self.seq += 1
            event = {"seq": self.seq, "ts": now_iso(), "actor": actor,
                     "object_type": object_type, "object_id": object_id,
                     "action": action, "detail": text}
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.log.append(event)

    # ----- 启动 -----

    def start(self, data, raw: bytes) -> dict:
        with self.lock:
            if self.started:
                raise RuntimeError("实例已启动，不能重复输入任务清单")
            entry = parse_start(data, self.out_dir / "requirements", self.list_version)
            tasks, spec_obj = entry.tasks, entry.spec
            spec_version, requirement, pm_summary = entry.spec_version, entry.requirement, entry.summary
            plan_id = entry.plan_id or f"PLAN-{self.instance_id}"
            if not re.fullmatch(r"PLAN-[A-Za-z0-9_-]+", plan_id):
                raise ValueError("计划 ID 格式无效")
            plan = entry.plan or {"spec_ref": None, "tasks": tasks, "created_at": now_iso()}
            version = plan.get("version", 1)
            if type(version) is not int or version < 1:
                raise ValueError("计划版本必须是正整数")
            plan = {**plan, "plan_id": plan_id, "version": version, "status": "validated"}
            edges, summary, layers, order = self._build_plan_graph(tasks)

            self.plan, self.spec = plan, spec_obj
            self.plan_id, self.list_version = plan_id, entry.list_version
            self.spec_version = spec_version
            self.auto_execute = self.auto_exec

            self.tasks = {task["task_id"]: {**task, "status": "pending", "attempts": 0}
                          for task in tasks}
            self.edges, self.layers, self.order = edges, layers, order
            self.analysis_summary = f"{pm_summary}｜{summary}".strip("｜") if pm_summary else summary
            self.started = True
            self.upload_path.parent.mkdir(parents=True, exist_ok=True)
            self.upload_path.write_bytes(raw)
            self.emit("system", "instance", self.instance_id, "created",
                      f"实例创建，绑定计划 {self.plan_id}（规格 {spec_version}）"
                      if self.plan_id else f"实例创建，绑定规格 {spec_version}")
            if requirement:
                self.emit("system", "task_list", f"TL-{self.list_version}",
                          "requirement_parsed",
                          f"PM Agent 拆解需求为 {len(tasks)} 个任务：{pm_summary or requirement[:40]}")
            self.emit("system", "task_list", f"TL-{self.list_version}",
                      "dependency_confirmed",
                      f"确认 {len(edges)} 条依赖边（AI 分析：{summary or '无'}）")
            self.emit("system", "graph", self.graph_id, "graph_built",
                      f"建图完成：{len(tasks)} 个任务、{len(layers)} 个并行组、无环")
            self._publish_plan_for_approval()
            return self.snapshot()

    def _build_plan_graph(self, tasks):
        try:
            edges, summary = analyze(tasks, self.mock)
        except SystemExit as exc:
            raise RuntimeError(str(exc)) from None
        layers, order = topo_layers([task["task_id"] for task in tasks], edges)
        return edges, summary, layers, order

    def _publish_plan_for_approval(self) -> None:
        if self.spec is not None:
            spec_json = json.dumps(self.spec, ensure_ascii=False, indent=2)
            for name in ("spec.json", f"spec.v{self.spec['version']}.json"):
                (self.out_dir / name).write_text(spec_json, encoding="utf-8")
            review = review_plan(self.spec, self.plan)
            self.plan_review = review
            (self.out_dir / "plan_review.json").write_text(
                json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
            self.emit("system", "plan", self.plan_id, "plan_validated",
                      f"计划质量检查：{len(review['issues'])} 个问题"
                      if review["issues"] else "计划质量检查通过")
        self._open_gate("plan_approval", self.plan_id,
                        f"计划 {self.plan_id} v{self.plan['version']} 待批准")
        self.save(update_graph=True)
        self._write_plan_artifacts(self.plan)

    def _waiting_for_plan(self) -> bool:
        return bool(self.waiting_on and self.waiting_on["gate_type"] == "plan_approval")

    # ----- 队列与处理 -----

    def enqueue(self, packet: dict) -> int:
        if (packet.get("type") == "human_op" and packet.get("action") == "approve"
                and "plan_id" not in packet and "plan_version" not in packet):
            with self.lock:
                if self._waiting_for_plan():
                    packet = {**packet, "plan_id": self.plan_id,
                              "plan_version": self.plan["version"]}
        priority = PRIORITIES.get(str(packet.get("priority", "medium")), 1)
        self.queue.put((priority, next(self._tick), packet))
        return self.queue.qsize()

    def _worker(self) -> None:
        while True:
            _, _, packet = self.queue.get()
            try:
                with self.lock:
                    self.process(packet)
                    self.last_error = ""
                    self.save(update_graph=packet.get("type") == "change")
            except Exception as exc:  # 处理失败留在 last_error，等待人工介入
                self.last_error = str(exc)
                print(f"数据包处理失败：{exc}")

    def process(self, packet: dict) -> None:
        if not self.started:
            raise ValueError("实例尚未启动，请先 POST /api/start")
        kind = str(packet.get("type") or "")
        if kind == "ai_report":
            self._ai_report(packet)
        elif kind == "human_op":
            self._human_op(packet)
        elif kind == "change":
            if self._waiting_for_plan():
                raise ValueError("计划批准前不能变更或派发任务")
            self._apply_change(packet)
        else:
            raise ValueError(f"未知数据包类型：{kind}")

    def _ai_report(self, packet: dict) -> None:
        task_id = str(packet.get("task_id") or "")
        task = self.tasks.get(task_id)
        if not task:
            raise ValueError(f"AI 回报引用了不存在的任务：{task_id}")
        if task["status"] != "running":
            raise ValueError(
                f"任务 {task_id} 当前状态为 {task['status']}，只有 running 的 AI 任务可以回报")
        detail = str(packet.get("detail") or "")
        if str(packet.get("result")) == "done":
            task["status"] = "done"
            self._record_artifact(task)
            self.emit("ai", "task", task_id, "task_done",
                      detail or f"完成 {task['name']}")
        else:
            task["attempts"] += 1
            if task["attempts"] <= MAX_RETRY:
                self.emit("ai", "task", task_id, "task_retried",
                          f"{detail or '执行失败'}，第 {task['attempts']} 次自动重试")
                self.emit("system", "task", task_id, "task_dispatched",
                          f"重新派发 {task['name']}")
            else:
                task["status"] = "failed"
                self.emit("ai", "task", task_id, "task_failed",
                          f"{detail or '执行失败'}，重试额度用尽")
                self._open_gate("task_failed", task_id,
                                f"任务 {task_id} 失败，等待人工接管")
        self._dispatch_ready()

    def _human_op(self, packet: dict) -> None:
        action = str(packet.get("action") or "")
        if self._waiting_for_plan() or "plan_id" in packet or "plan_version" in packet or action == "return":
            self._plan_op(packet)
            return
        if action not in {"approve", "reject", "skip"}:
            raise ValueError(f"非法人工操作：{action}")
        reason = str(packet.get("reason") or "")
        if action in {"reject", "skip"} and not reason.strip():
            raise ValueError("驳回或跳过必须填写理由")
        operator = str(packet.get("operator") or "demo")
        if not self.waiting_on:
            if (self.status == "delivery_pending" and self.delivery
                    and self.delivery["status"] == "draft" and action in {"approve", "reject"}):
                self._decide_delivery(action, reason, operator)
                return
            raise ValueError("当前没有等待中的人工节点")
        task_id = self.waiting_on["object_id"]
        self.waiting_on = None
        self.status = "running"
        task = self.tasks.get(task_id)
        if task:
            task["status"] = {"approve": "done", "reject": "blocked",
                              "skip": "skipped"}[action]
        self._record_decision(action, task_id, reason, operator)
        self.emit(f"human:{operator}", "instance",
                  self.instance_id, "human_gate_exited",
                  f"{action} {task_id}" + (f"：{reason}" if reason else ""))
        if task and action == "approve":
            self.emit(f"human:{operator}", "task", task_id,
                      "task_done", f"人工确认完成 {task['name']}")
        if action == "reject":
            self._cascade_blocked(task_id)
        self._dispatch_ready()

    def _plan_op(self, packet: dict) -> None:
        if not self._waiting_for_plan():
            raise ValueError("当前没有待批准的计划")
        plan = self.plan
        action = packet.get("action")
        if action == "return" or "plan_id" in packet or "plan_version" in packet:
            version = packet.get("plan_version")
            if (packet.get("plan_id") != self.plan_id or type(version) is not int
                    or version != plan["version"]):
                raise ValueError("计划 ID 或版本已变更，请刷新后再操作")
        operator = str(packet.get("operator") or "demo")
        if action == "approve":
            plan["status"] = "approved"
            self._write_plan_artifacts(plan)
            self._record_decision("approve", self.plan_id, "", operator,
                                  target_type="plan", target_version=plan["version"])
            self.waiting_on = None
            self.status = "running"
            self.emit(f"human:{operator}", "instance", self.instance_id, "human_gate_exited",
                      f"计划 {self.plan_id} v{plan['version']} 已批准")
            self._dispatch_ready()
            if self.auto_execute:
                self._start_auto_executor()
            return
        if action != "return":
            raise ValueError("待批准计划只接受批准或定向退回")
        self._return_plan(packet, operator)

    def _return_plan(self, packet: dict, operator: str) -> None:
        target = packet.get("return_to")
        reason = str(packet.get("reason") or "").strip()
        if target not in {"spec", "plan"} or not reason:
            raise ValueError("退回必须指定 spec 或 plan 并填写理由")
        old_plan, old_spec = self.plan, self.spec
        spec = old_spec
        if target == "spec":
            if old_spec is None:
                raise ValueError("本次运行没有可退回的规格正文")
            spec = reviewed_replacement(old_spec, packet.get("spec"))

        plan = (plan_from_spec(spec, plan_id=self.plan_id,
                               max_parallel=old_plan.get("max_parallel", 2))
                if spec is not None else {**old_plan, "created_at": now_iso()})
        plan = {**plan, "version": old_plan["version"] + 1, "status": "validated"}
        tasks, _ = normalize_tasks(plan)
        edges, summary, layers, order = self._build_plan_graph(tasks)
        artifact = old_spec if target == "spec" else old_plan
        target_id = artifact["spec_id"] if target == "spec" else self.plan_id
        self._record_decision("return", target_id, reason, operator, target_type=target,
                              target_version=artifact["version"], return_to=target)
        self.emit(f"human:{operator}", target, target_id, f"{target}_returned",
                  f"{target_id} v{artifact['version']} 已退回：{reason}")
        self.plan, self.spec = plan, spec
        if spec is not None:
            self.spec_version = f"v{spec['version']}"
        self.tasks = {task["task_id"]: {**task, "status": "pending", "attempts": 0} for task in tasks}
        self.edges, self.layers, self.order = edges, layers, order
        self.analysis_summary = summary
        self.graph_id = self._bump(self.graph_id, "G-v")
        self._publish_plan_for_approval()

    def _record_decision(self, action: str, target_id: str, reason: str,
                         operator: str, target_type: str = "task",
                         target_version: int = 1, return_to: str | None = None) -> dict:
        decision = {
            "decision_id": f"DEC-{len(self.decisions) + 1:04d}",
            "target_type": target_type,
            "target_id": target_id,
            "target_version": target_version,
            "action": action,
            "actor": f"human:{operator}",
            "created_at": now_iso(),
        }
        if reason:
            decision["reason"] = reason
        if return_to:
            decision["return_to"] = return_to
        self.decisions.append(decision)
        with self.decisions_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(decision, ensure_ascii=False) + "\n")
        self.emit(f"human:{operator}", "decision", decision["decision_id"],
                  "decision_recorded", f"{action} {target_id}" + (f"：{reason}" if reason else ""))
        return decision

    def _cascade_blocked(self, root: str) -> list[str]:
        """驳回后把下游未开始的任务一并置为 blocked，避免工作流静默卡死。"""
        queue, blocked = [root], []
        while queue:
            current = queue.pop(0)
            for edge in self.edges:
                if edge["from"] != current or edge.get("type") not in BLOCKING_EDGE_TYPES:
                    continue
                child_id, child = edge["to"], self.tasks[edge["to"]]
                if child["status"] != "pending":
                    continue
                child["status"] = "blocked"
                child["attempts"] = 0
                blocked.append(child_id)
                self.emit("system", "task", child_id, "task_blocked",
                          f"上游 {current} 已驳回，任务 {child['name']} 置为 blocked")
                queue.append(child_id)
        return blocked

    # ----- 变更与增量重排 -----

    def _apply_change(self, packet: dict) -> None:
        summary = str(packet.get("summary") or "").strip()
        change_id = str(packet.get("change_id") or f"CR-{datetime.now():%Y%m%d%H%M%S}")
        change_type = str(packet.get("change_type") or "task")
        if packet.get("reopen"):
            self._reopen_tasks([str(item) for item in packet["reopen"]],
                               change_id, summary)
            return
        if packet.get("tasks") is not None:
            summary = summary or "人工调整任务与依赖"
            self.emit("human:demo", "change_request", change_id, "change_submitted",
                      f"人工调整提交（{change_type}）：{summary}")
            self._apply_manual_graph(packet["tasks"], packet.get("edges") or [],
                                     change_id)
            return
        if not summary:
            raise ValueError("变更包缺少 summary")
        self.emit("human:demo", "change_request", change_id, "change_submitted",
                  f"变更提交（{change_type}）：{summary}")

        impact = self._impact_analysis(summary)
        self.emit("human:demo", "change_request", change_id, "change_confirmed",
                  f"变更确认：影响 {len(impact['impacted_task_ids'])} 个任务，"
                  f"新增 {len(impact['new_tasks'])} 个任务、{len(impact['new_edges'])} 条依赖")

        if change_type == "spec":
            self.spec_version = self._bump(self.spec_version, "v")
            self.emit("system", "spec", "SPEC-2026-002", "baseline_refrozen",
                      f"规格升版至 {self.spec_version}，旧基线 superseded")
        self.list_version = self._bump(self.list_version, "v")
        self.graph_id = self._bump(self.graph_id, "G-v")

        reset = []
        for task_id in impact["impacted_task_ids"]:
            task = self.tasks[task_id]
            if task["status"] not in TERMINAL:
                task["status"] = "pending"
                task["attempts"] = 0
                reset.append(task_id)
        for task in impact["new_tasks"]:
            self.tasks[task["task_id"]] = {**task, "status": "pending", "attempts": 0}

        merged = self.edges + impact["new_edges"]
        self.edges = list({(e["from"], e["to"], e["type"]): e for e in merged}.values())
        self.layers, self.order = topo_layers(list(self.tasks), self.edges)
        self.emit("system", "graph", self.graph_id, "replan_done",
                  f"增量重排完成：重置 {len(reset)} 个任务，"
                  f"当前 {len(self.tasks)} 个任务、{len(self.layers)} 个并行组")
        self._dispatch_ready()

    def _reopen_tasks(self, task_ids: list[str], change_id: str, summary: str) -> None:
        """把 blocked / failed 任务重置回 pending，重新进入调度。"""
        reopened = []
        for task_id in task_ids:
            task = self.tasks.get(task_id)
            if not task:
                raise ValueError(f"恢复操作引用了不存在的任务：{task_id}")
            if task["status"] not in {"blocked", "failed"}:
                continue
            task["status"] = "pending"
            task["attempts"] = 0
            reopened.append(task_id)
        if not reopened:
            raise ValueError("没有可恢复的 blocked 或 failed 任务")

        self.list_version = self._bump(self.list_version, "v")
        self.graph_id = self._bump(self.graph_id, "G-v")
        self.emit("human:demo", "change_request", change_id, "change_submitted",
                  f"恢复提交：{summary or '恢复被阻塞任务'}")
        self.emit("system", "task", reopened[0], "task_reopened",
                  f"恢复 {len(reopened)} 个任务为 pending：{'、'.join(reopened)}")
        self.emit("system", "graph", self.graph_id, "replan_done",
                  f"恢复后重新调度：{len(self.tasks)} 个任务、{len(self.layers)} 个并行组")
        self._dispatch_ready()

    def _apply_manual_graph(self, raw_tasks, raw_edges, change_id: str) -> None:
        """采用人工提交的任务与依赖，校验后重算拓扑层和执行顺序。"""
        tasks, _ = normalize_tasks(raw_tasks)
        incoming = {task["task_id"]: task for task in tasks}
        protected, dropped = [], []
        for task_id in self.tasks:
            if task_id in incoming:
                continue
            if self.tasks[task_id]["status"] in TERMINAL:
                protected.append(task_id)
            else:
                dropped.append(task_id)

        merged = {}
        for task_id, task in incoming.items():
            old = self.tasks.get(task_id)
            merged[task_id] = {**task,
                               "status": old["status"] if old else "pending",
                               "attempts": old["attempts"] if old else 0}
        for task_id in protected:
            merged[task_id] = self.tasks[task_id]

        edges = validate_edges(raw_edges, set(merged))
        layers, order = topo_layers(list(merged), edges)
        self.tasks, self.edges, self.layers, self.order = merged, edges, layers, order
        self.list_version = self._bump(self.list_version, "v")
        self.graph_id = self._bump(self.graph_id, "G-v")
        self.emit("human:demo", "change_request", change_id, "change_applied",
                  f"人工调整已采用：{len(merged)} 个任务、{len(edges)} 条依赖"
                  + (f"，删除 {len(dropped)} 个未终态任务" if dropped else "")
                  + (f"，保留 {len(protected)} 个已终态任务" if protected else ""))
        self.emit("system", "graph", self.graph_id, "replan_done",
                  f"按人工调整重排：{len(self.tasks)} 个任务、{len(self.layers)} 个并行组")
        self._dispatch_ready()

    def _impact_analysis(self, summary: str) -> dict:
        if self.mock:
            pending = [task_id for task_id in self.order
                       if self.tasks[task_id]["status"] == "pending"]
            return {"impacted_task_ids": pending, "new_tasks": [],
                    "new_edges": [], "summary": "mock 模式：仅重排未开始任务"}
        payload = {
            "change": summary,
            "tasks": [{"task_id": t["task_id"], "name": t["name"],
                       "deliverable": t["deliverable"], "status": t["status"]}
                      for t in self.tasks.values()],
            "edges": [{"from": e["from"], "to": e["to"], "type": e["type"]}
                      for e in self.edges],
        }
        last_error = None
        for attempt in range(1, 3):
            try:
                data = chat_json(IMPACT_PROMPT, json.dumps(payload, ensure_ascii=False))
                raw_new = data.get("new_tasks") or []
                new_tasks, _ = normalize_tasks(raw_new) if raw_new else ([], "")
                overlap = {t["task_id"] for t in new_tasks} & set(self.tasks)
                if overlap:
                    raise ValueError(f"新增任务编号冲突：{sorted(overlap)}")
                all_ids = list(self.tasks) + [t["task_id"] for t in new_tasks]
                new_edges = validate_edges(data.get("new_edges") or [], set(all_ids))
                topo_layers(all_ids, self.edges + new_edges)
                impacted = [str(x) for x in data.get("impacted_task_ids") or []]
                unknown = [x for x in impacted if x not in self.tasks]
                if unknown:
                    raise ValueError(f"影响集合含未知任务：{unknown}")
                return {"impacted_task_ids": impacted, "new_tasks": new_tasks,
                        "new_edges": new_edges,
                        "summary": str(data.get("summary") or "")}
            except (ValueError, KeyError, TypeError) as exc:
                last_error = exc
                print(f"  影响分析第 {attempt} 次校验未通过：{exc}")
        raise RuntimeError(f"影响分析连续 2 次未通过校验，转人工：{last_error}")

    @staticmethod
    def _bump(version: str, prefix: str) -> str:
        match = re.match(rf"({prefix})(\d+)\.(\d+)$", version)
        if not match:
            return f"{prefix}1.0"
        return f"{match.group(1)}{match.group(2)}.{int(match.group(3)) + 1}"

    # ----- 调度与状态 -----

    def _deps_done(self, task_id: str) -> bool:
        return all(self.tasks[edge["from"]]["status"] in TERMINAL
                   for edge in self.edges
                   if edge["to"] == task_id and edge.get("type") in BLOCKING_EDGE_TYPES)

    def _dispatch_ready(self) -> None:
        if not self.started or self._waiting_for_plan():
            return
        progressed = True
        while progressed:
            progressed = False
            for task_id in self.order:
                task = self.tasks[task_id]
                if task["status"] != "pending" or not self._deps_done(task_id):
                    continue
                if "human" in task["suggested_role"]:
                    if self.waiting_on:
                        continue
                    self._open_gate("task_execution", task_id,
                                    f"人工节点 {task_id}：{task['name']}，等待人工操作")
                    continue
                task["status"] = "running"
                self.emit("system", "task", task_id, "task_dispatched",
                          f"派发 {task['name']}（{task['suggested_role']}）")
                progressed = True
        self._maybe_complete()

    def _open_gate(self, gate_type: str, object_id: str, detail: str) -> None:
        self.waiting_on = {"gate_type": gate_type, "waiting_for": "human",
                           "object_id": object_id, "since": now_iso()}
        self.status = "waiting_human"
        self.emit("system", "instance", self.instance_id, "human_gate_entered", detail)

    def _maybe_complete(self) -> None:
        """收口实例状态：人工门开着时优先 waiting_human，全部终态后转交付。"""
        states = [task["status"] for task in self.tasks.values()]
        if not states:
            return
        all_terminal = all(state in TERMINAL for state in states)
        if all(state in TERMINAL or state in {"blocked", "failed"} for state in states):
            if all_terminal:
                if self.delivery is None:
                    self._create_delivery()
                self.status = {
                    "draft": "delivery_pending",
                    "confirmed": "completed",
                    "rejected": "blocked",
                }[self.delivery["status"]]
            elif self.waiting_on:
                self.status = "waiting_human"
            else:
                self.status = "blocked"
            return
        if not self.waiting_on:
            self.status = "running"

    # ----- 产物与交付 -----

    def _record_artifact(self, task: dict) -> str:
        """任务完成时登记产物，交付记录据此可追溯。"""
        self.artifact_seq += 1
        artifact_id = f"ART-{self.artifact_seq:04d}"
        folder = self.out_dir / "artifacts" / artifact_id
        folder.mkdir(parents=True, exist_ok=True)
        record = {
            "artifact_id": artifact_id,
            "task_id": task["task_id"],
            "kind": "record",
            "summary": task.get("deliverable") or task["name"],
            "created_at": now_iso(),
        }
        (folder / "artifact.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        task.setdefault("artifact_ids", []).append(artifact_id)
        self.emit("system", "artifact", artifact_id, "artifact_created",
                  f"任务 {task['task_id']} 产出 {artifact_id}")
        return artifact_id

    def _create_delivery(self) -> None:
        items = []
        for task_id in self.order or list(self.tasks):
            task = self.tasks[task_id]
            if task["status"] not in TERMINAL:
                continue
            items.append({
                "task_id": task_id,
                "artifact_ids": list(task.get("artifact_ids") or []),
                "acceptance": "passed" if task["status"] == "done" else "skipped",
            })
        delivery = {
            "delivery_id": "DLV-0001",
            "instance_id": self.instance_id,
            "items": items,
            "status": "draft",
            "created_at": now_iso(),
        }
        if self.plan_id:
            delivery["plan_id"] = self.plan_id
        self.delivery = delivery
        self._write_delivery()
        self.emit("system", "delivery", delivery["delivery_id"], "delivery_created",
                  f"交付记录 {delivery['delivery_id']} 待人工确认（{len(items)} 项）")

    def _write_delivery(self) -> None:
        if self.delivery is None:
            return
        (self.out_dir / "delivery.json").write_text(
            json.dumps(self.delivery, ensure_ascii=False, indent=2), encoding="utf-8")

    def _decide_delivery(self, action: str, reason: str, operator: str) -> None:
        delivery = self.delivery
        approved = action == "approve"
        decision = self._record_decision(action, delivery["delivery_id"], reason,
                                         operator, target_type="delivery")
        delivery.update({
            "status": "confirmed" if approved else "rejected",
            "decided_by": decision["actor"],
            "decided_at": decision["created_at"],
            "decision_id": decision["decision_id"],
        })
        self._write_delivery()
        self.status = "completed" if approved else "blocked"
        self.emit(decision["actor"], "delivery", delivery["delivery_id"],
                  "delivery_confirmed" if approved else "delivery_rejected",
                  f"交付 {delivery['delivery_id']} 已确认" if approved
                  else f"交付 {delivery['delivery_id']} 已驳回：{reason}")

    def _start_auto_executor(self) -> None:
        """自动执行器：把 running 的 AI 任务逐个收尾，仅在 --auto-exec 时启用。"""
        if self._auto_thread is not None:
            return

        def run() -> None:
            while True:
                time.sleep(0.05)
                with self.lock:
                    if not self.started:
                        continue
                    if self.status in {"delivery_pending", "completed", "blocked", "aborted"}:
                        return
                    task = next((item for item in self.tasks.values()
                                 if item["status"] == "running"), None)
                    if task is None:
                        continue
                    try:
                        self._ai_report({"task_id": task["task_id"], "result": "done",
                                         "detail": "自动执行完成（--auto-exec）"})
                        self.save()
                    except Exception as exc:  # 自动执行失败留在 last_error，等人工介入
                        self.last_error = str(exc)

        self._auto_thread = threading.Thread(target=run, daemon=True)
        self._auto_thread.start()

    # ----- 计划产物 -----

    def _write_plan_artifacts(self, plan: dict) -> None:
        """plan.json / plan.md / 依赖图共用同一个 plan_id + version，便于核对同版。"""
        plan_id = str(plan.get("plan_id") or self.plan_id or "PLAN-UNKNOWN")
        version = int(plan.get("version") or 1)
        stem = f"{plan_id}-v{version}"
        payload = {**plan, "plan_id": plan_id, "version": version}
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        (self.out_dir / "plan.json").write_text(text, encoding="utf-8")
        (self.out_dir / f"plan-{stem}.json").write_text(text, encoding="utf-8")
        markdown = self._plan_markdown(payload, plan_id, version)
        (self.out_dir / "plan.md").write_text(markdown, encoding="utf-8")
        (self.out_dir / f"plan-{stem}.md").write_text(markdown, encoding="utf-8")
        png = self.out_dir / "task_flow.png"
        if png.exists():
            (self.out_dir / f"task_flow-{stem}.png").write_bytes(png.read_bytes())

    @staticmethod
    def _plan_markdown(plan: dict, plan_id: str, version: int) -> str:
        spec_ref = plan.get("spec_ref") or {}
        lines = [
            f"# 计划 {plan_id} · v{version}",
            "",
            f"- 计划号：{plan_id}",
            f"- 计划版本：v{version}",
            f"- 规格：{spec_ref.get('spec_id', '')} v{spec_ref.get('version', '')}",
            f"- 任务数：{len(plan.get('tasks') or [])}",
            "",
            "| 任务 | 名称 | 角色 | 预估天数 | 依赖 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for task in plan.get("tasks") or []:
            lines.append(
                f"| {task.get('task_id', '')} | {task.get('name', '')} | "
                f"{task.get('suggested_role', '')} | {task.get('estimate_days', '')} | "
                f"{'、'.join(task.get('depends_on') or [])} |")
        return "\n".join(lines) + "\n"

    # ----- 对外输出 -----

    def snapshot(self) -> dict:
        counts = {state: sum(1 for t in self.tasks.values() if t["status"] == state)
                  for state in ["pending", "running", "done", "failed", "blocked", "skipped"]}
        terminal = counts["done"] + counts["skipped"]
        total = len(self.tasks)
        return {
            "instance_id": self.instance_id,
            "status": self.status,
            "waiting_on": self.waiting_on,
            "spec_version": self.spec_version,
            "plan_id": self.plan_id,
            "plan_version": self.plan["version"] if self.plan else None,
            "plan_review": self.plan_review,
            "can_return_spec": self.spec is not None and self._waiting_for_plan(),
            "list_version": self.list_version,
            "graph_id": self.graph_id,
            "total": total,
            "done": counts["done"],
            "failed": counts["failed"],
            "blocked": counts["blocked"],
            "skipped": counts["skipped"],
            "running": counts["running"],
            "pending": counts["pending"],
            "percent": round(100 * terminal / total, 1) if total else 0.0,
            "analysis": self.analysis_summary,
            "last_error": self.last_error,
            "tasks": [
                {"task_id": t["task_id"], "name": t["name"], "status": t["status"],
                 "attempts": t["attempts"], "suggested_role": t["suggested_role"],
                 "priority": t["priority"], "deliverable": t["deliverable"],
                 "acceptance_items": t["acceptance_items"],
                 "requirement_refs": t.get("requirement_refs", []),
                 "design_refs": t.get("design_refs", []),
                 "depends_on": t.get("depends_on", []),
                 "write_paths": t.get("write_paths", []),
                 "review_required": t.get("review_required", False),
                 "artifact_ids": list(t.get("artifact_ids") or []),
                 **({"estimate_days": t["estimate_days"]} if "estimate_days" in t else {})}
                for t in self.tasks.values()
            ],
            "layers": self.layers,
            "execution_order": self.order,
            "decisions": list(self.decisions),
            "delivery": self.delivery,
        }

    def event_stream(self) -> dict:
        with self.lock:
            return {
                "schema_version": "1.1",
                "note": "实时编排运行时：完整事件流，顶层为当前状态",
                "instance_id": self.instance_id,
                "spec_version": self.spec_version,
                "plan_id": self.plan_id,
                "graph_id": self.graph_id,
                "status": self.status,
                "waiting_on": self.waiting_on,
                "event_log": list(self.log),
            }

    def save(self, update_graph: bool = False) -> None:
        with self.lock:
            payload = {"state": self.snapshot(), "edges": self.edges,
                       "updated_at": now_iso()}
            self.state_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            if self.started:
                (self.out_dir / "event_stream_example.json").write_text(
                    json.dumps(self.event_stream(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
                if update_graph:
                    try:
                        draw_graph(list(self.tasks.values()), self.edges, self.layers,
                                   self.out_dir / "task_flow.png", self.analysis_summary)
                    except ImportError:
                        pass  # 图形是本地可选产物，缺 matplotlib 不影响编排


class AgentHandler(BaseHTTPRequestHandler):
    agent: RealtimeAgent = None
    protocol_version = "HTTP/1.1"
    timeout = 10

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_raw(code, body)

    def _send_raw(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, code: int) -> None:
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _body(self) -> tuple[object, bytes]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}"), raw

    def do_OPTIONS(self) -> None:
        self._send_empty(204)

    def _send_page(self) -> None:
        if not WEB_PAGE.exists():
            self._send(404, {"error": "web/index.html 不存在"})
            return
        body = WEB_PAGE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send_page()
        elif parsed.path == "/favicon.ico":
            self._send_empty(204)
        elif parsed.path == "/api/health":
            self._send(200, {"status": "ok", "started": self.agent.started,
                             "instance_status": self.agent.status})
        elif parsed.path == "/api/import":
            query = parse_qs(parsed.query)
            target = (query.get("path", [""])[0] or "").strip()
            if not target:
                self._send(400, {"error": "缺少 path 参数"})
                return
            source = Path(target).expanduser()
            if not source.is_file():
                self._send(404, {"error": f"文件不存在：{source}"})
                return
            if source.suffix.lower() not in {".md", ".markdown", ".txt", ".json"}:
                self._send(400, {"error": "只支持 md / markdown / txt / json 文件"})
                return
            if source.stat().st_size > 512 * 1024:
                self._send(400, {"error": "文件超过 512KB，请精简后再导入"})
                return
            try:
                content = source.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                self._send(400, {"error": "文件不是 UTF-8 编码"})
                return
            self._send(200, {"path": str(source), "suffix": source.suffix.lower(),
                             "content": content})
        elif parsed.path == "/api/progress":
            query = parse_qs(parsed.query)
            paged = "since" in query or "limit" in query
            since, limit = 0, 200
            if paged:
                try:
                    since = int(query.get("since", ["0"])[0] or 0)
                    limit = int(query.get("limit", ["200"])[0] or 200)
                except ValueError:
                    self._send(400, {"error": "since 和 limit 必须是整数"})
                    return
                if since < 0 or limit <= 0:
                    self._send(400, {"error": "since 不能为负数，limit 必须是大于 0 的整数"})
                    return
                limit = min(limit, 1000)
            with self.agent.lock:
                payload = {**self.agent.snapshot(), "edges": self.agent.edges}
                if paged:
                    stream = self.agent.event_stream()
                    remaining = [e for e in stream["event_log"] if e["seq"] > since]
                    events = remaining[:limit]
                    payload.update({
                        "schema_version": stream["schema_version"],
                        "note": "实时编排事件流：event_log 为本批事件，since=0 从头拉取",
                        "event_log": events,
                        "next_since": events[-1]["seq"] if events else since,
                        "has_more": len(remaining) > limit,
                    })
            self._send(200, payload)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        try:
            data, raw = self._body()
            if parsed.path == "/api/start":
                progress = self.agent.start(data, raw)
                self._send(200, {"accepted": True, "progress": progress})
            elif parsed.path == "/api/events":
                packets = data.get("events") if isinstance(data, dict) and "events" in data else [data]
                for packet in packets:
                    self.agent.enqueue(packet)
                self._send(202, {"accepted": len(packets),
                                 "queue_size": self.agent.queue.qsize(),
                                 "progress": self.agent.snapshot()})
            else:
                self._send(404, {"error": "not found"})
        except SpecGateError as exc:
            self._send(exc.status, {"error": exc.code, "message": str(exc), "details": exc.details})
        except RequirementsUnavailable as exc:
            self._send(503, {"error": str(exc)})
        except RuntimeError as exc:
            self._send(409, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})

    def log_message(self, fmt: str, *args) -> None:
        return

    def handle_one_request(self) -> None:
        """浏览器预连接被重置时不打印回溯，避免终端刷屏。"""
        try:
            super().handle_one_request()
        except ConnectionResetError:
            self.close_connection = True


class AgentServer(ThreadingHTTPServer):
    """扩大监听队列，避免浏览器并发和 keep-alive 连接挤爆默认 backlog。"""
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 256


def serve_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent.py serve", description="实时编排运行时")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--out", type=Path, default=ROOT / "output")
    parser.add_argument("--mock", action="store_true", help="不调模型")
    parser.add_argument("--auto-exec", dest="auto_exec", action="store_true",
                        help="演示模式：计划批准后自动回报 AI 任务完成（默认关闭）")
    args = parser.parse_args(argv)

    agent = RealtimeAgent(args.out, args.mock, args.auto_exec)
    AgentHandler.agent = agent
    server = AgentServer((args.host, args.port), AgentHandler)
    print(f"编排器已启动：http://{args.host}:{args.port}")
    print(f"网页原型：http://{args.host}:{args.port}/")
    print("POST /api/start | POST /api/events | GET /api/progress[?since=N&limit=200] | GET /api/health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "serve":
        raise SystemExit("用法：py -3 python/agent.py serve [--host HOST] [--port PORT] [--out DIR] [--mock] [--auto-exec]")
    return serve_cli(sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
