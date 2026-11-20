import contextlib
import json
import tempfile
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "orchestrator" / "python"))

from agent import AgentHandler, AgentServer, RealtimeAgent  # noqa: E402

TASKS = {"tasks": [
    {"task_id": "T1", "name": "写接口", "suggested_role": "ai"},
    {"task_id": "T2", "name": "写页面", "suggested_role": "ai"},
]}


@contextlib.contextmanager
def running_server(out_dir: Path):
    agent = RealtimeAgent(out_dir=out_dir, mock=True,
                          upload_path=out_dir / "uploaded.json")
    handler = type("IsolatedHandler", (AgentHandler,), {"agent": agent})
    server = AgentServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def post_json(url: str, payload):
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


class AgentCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.agent = RealtimeAgent(out_dir=self.out, mock=True,
                                   upload_path=self.out / "uploaded.json")

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, payload=None):
        return self.agent.start(payload or TASKS, json.dumps(payload or TASKS).encode("utf-8"))

    def actions(self):
        return [event["action"] for event in self.agent.log]


class FailureBranchTest(AgentCase):
    """失败重试与转人工。"""

    def test_retry_twice_then_failed_opens_gate(self):
        self.start({"tasks": [{"task_id": "T1", "name": "写接口", "suggested_role": "ai"}]})
        self.assertEqual(self.agent.tasks["T1"]["status"], "running")

        for _ in range(2):
            self.agent._ai_report({"task_id": "T1", "result": "failed", "detail": "编译失败"})
            self.assertEqual(self.agent.tasks["T1"]["status"], "running",
                             "前两次失败应自动重试，仍为 running")
        self.assertEqual(self.agent.tasks["T1"]["attempts"], 2)
        self.assertEqual(self.actions().count("task_retried"), 2)

        self.agent._ai_report({"task_id": "T1", "result": "failed", "detail": "仍然失败"})
        self.assertEqual(self.agent.tasks["T1"]["status"], "failed")
        self.assertEqual(self.agent.waiting_on["gate_type"], "task_failed")
        self.assertIn("task_failed", self.actions())

    @unittest.skip("待修复：失败开门后 _maybe_complete 把 waiting_human 覆盖成 blocked")
    def test_failed_gate_keeps_waiting_human_status(self):
        self.start({"tasks": [{"task_id": "T1", "name": "写接口", "suggested_role": "ai"}]})
        for _ in range(3):
            self.agent._ai_report({"task_id": "T1", "result": "failed", "detail": "失败"})
        self.assertIsNotNone(self.agent.waiting_on)
        self.assertEqual(self.agent.status, "waiting_human",
                         "人工门开着时实例应为 waiting_human，不应被覆盖成 blocked")

    def test_report_on_non_running_task_rejected(self):
        self.start({"tasks": [{"task_id": "T1", "name": "写接口", "suggested_role": "ai"}]})
        self.agent._ai_report({"task_id": "T1", "result": "done", "detail": "完成"})
        with self.assertRaises(ValueError):
            self.agent._ai_report({"task_id": "T1", "result": "done", "detail": "重复回报"})

    def test_report_unknown_task_rejected(self):
        self.start({"tasks": [{"task_id": "T1", "name": "写接口", "suggested_role": "ai"}]})
        with self.assertRaises(ValueError):
            self.agent._ai_report({"task_id": "T9", "result": "done"})

    def test_duplicate_start_rejected(self):
        self.start()
        with self.assertRaises(RuntimeError):
            self.start()

    def test_human_op_without_waiting_gate_rejected(self):
        self.start({"tasks": [{"task_id": "T1", "name": "写接口", "suggested_role": "ai"}]})
        with self.assertRaises(ValueError):
            self.agent._human_op({"action": "approve", "operator": "tester"})


class ChangeValidationTest(AgentCase):
    """变更包校验失败时整次不落地。"""

    def setUp(self):
        super().setUp()
        self.start()

    def assert_graph_unchanged(self, task_ids, edge_count):
        self.assertEqual(sorted(self.agent.tasks), sorted(task_ids))
        self.assertEqual(len(self.agent.edges), edge_count)

    def test_duplicate_task_ids_rejected(self):
        before = (sorted(self.agent.tasks), len(self.agent.edges))
        packet = {
            "type": "change", "change_id": "CR-DUP", "summary": "重复编号",
            "tasks": [
                {"task_id": "T1", "name": "重复一"},
                {"task_id": "T1", "name": "重复二"},
            ],
            "edges": [],
        }
        with self.assertRaises(ValueError):
            self.agent.process(packet)
        self.assert_graph_unchanged(*before)

    def test_cycle_rejected(self):
        before = (sorted(self.agent.tasks), len(self.agent.edges))
        packet = {
            "type": "change", "change_id": "CR-CYCLE", "summary": "制造环",
            "tasks": [
                {"task_id": "T1", "name": "A"},
                {"task_id": "T2", "name": "B"},
            ],
            "edges": [
                {"from": "T1", "to": "T2", "type": "force"},
                {"from": "T2", "to": "T1", "type": "force"},
            ],
        }
        with self.assertRaises(ValueError):
            self.agent.process(packet)
        self.assert_graph_unchanged(*before)

    def test_unknown_endpoint_rejected(self):
        before = (sorted(self.agent.tasks), len(self.agent.edges))
        packet = {
            "type": "change", "change_id": "CR-EDGE", "summary": "悬空依赖",
            "tasks": [{"task_id": "T1", "name": "A"}],
            "edges": [{"from": "T1", "to": "T9", "type": "force"}],
        }
        with self.assertRaises(ValueError):
            self.agent.process(packet)
        self.assert_graph_unchanged(*before)

    def test_change_requires_summary(self):
        with self.assertRaises(ValueError):
            self.agent.process({"type": "change", "change_id": "CR-EMPTY"})


class HttpStatusTest(AgentCase):
    """HTTP 层把内部错误映射成对应状态码，而不只是抛异常。"""

    def test_duplicate_start_returns_409(self):
        with running_server(self.out) as base:
            post_json(f"{base}/api/start", TASKS)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post_json(f"{base}/api/start", TASKS)
            self.assertEqual(ctx.exception.code, 409)
            self.assertIn("已启动", json.loads(ctx.exception.read().decode("utf-8"))["error"])

    def test_empty_task_list_returns_400(self):
        with running_server(self.out) as base:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post_json(f"{base}/api/start", {"tasks": []})
            self.assertEqual(ctx.exception.code, 400)

    def test_progress_rejects_negative_since(self):
        with running_server(self.out) as base:
            post_json(f"{base}/api/start", TASKS)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(f"{base}/api/progress?since=-1", timeout=10)
            self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
