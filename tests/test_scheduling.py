import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "orchestrator" / "python"))

from agent import RealtimeAgent, topo_layers  # noqa: E402


def make_agent(out_dir: Path, edges: list[dict]) -> RealtimeAgent:
    agent = RealtimeAgent(out_dir=out_dir, mock=True,
                          upload_path=out_dir / "uploaded.json")
    agent.tasks = {
        task_id: {"task_id": task_id, "name": task_id, "status": "pending",
                  "attempts": 0, "suggested_role": "ai", "priority": "medium",
                  "deliverable": "", "acceptance_items": []}
        for task_id in ("T1", "T2", "T3")
    }
    agent.edges = edges
    agent.layers, agent.order = topo_layers(["T1", "T2", "T3"], edges)
    agent.started = True
    agent.status = "running"
    return agent


EDGES = [
    {"from": "T1", "to": "T2", "type": "force", "reason": "需要 T1 的表结构"},
    {"from": "T1", "to": "T3", "type": "resource", "reason": "同一名开发"},
]


class ResourceEdgeTest(unittest.TestCase):
    """resource 只表示资源共享，不阻塞调度；契约与模型提示词均如此。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_resource_edge_does_not_block(self):
        agent = make_agent(self.out, EDGES)
        agent._dispatch_ready()

        self.assertEqual(agent.tasks["T1"]["status"], "running")
        self.assertEqual(agent.tasks["T3"]["status"], "running",
                         "T3 只与 T1 共用资源，应同时可派发")
        self.assertEqual(agent.tasks["T2"]["status"], "pending",
                         "T2 依赖 T1 的产出，必须等待")

        agent._ai_report({"task_id": "T1", "result": "done", "detail": "完成"})
        self.assertEqual(agent.tasks["T2"]["status"], "running",
                         "T1 完成后 T2 应被派发")

    def test_resource_peer_shares_first_layer(self):
        agent = make_agent(self.out, EDGES)
        self.assertEqual(agent.layers, [["T1", "T3"], ["T2"]])


if __name__ == "__main__":
    unittest.main()
