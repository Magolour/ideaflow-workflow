import contextlib
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "contracts" / "v2"
sys.path.insert(0, str(ROOT / "modules" / "orchestrator" / "python"))

from agent import AgentHandler, AgentServer, RealtimeAgent  # noqa: E402


def load_schema(name: str) -> dict:
    return json.loads((V2 / name).read_text(encoding="utf-8"))


def inline(node, by_file):
    if isinstance(node, dict):
        if "$ref" in node:
            file_name, _, fragment = node["$ref"].partition("#")
            target = by_file[file_name]
            for part in [p for p in fragment.split("/") if p]:
                target = target[part]
            return inline(target, by_file)
        return {key: inline(value, by_file) for key, value in node.items()}
    if isinstance(node, list):
        return [inline(item, by_file) for item in node]
    return node


@contextlib.contextmanager
def running_server(out_dir: Path):
    agent = RealtimeAgent(out_dir=out_dir, mock=True,
                          upload_path=out_dir / "uploaded.json")
    handler = type("IsolatedHandler", (AgentHandler,), {"agent": agent})
    server = AgentServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", agent
    finally:
        server.shutdown()
        server.server_close()


def post_json(url: str, payload) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


class ApiGapTest(unittest.TestCase):
    """成员 4 实现后去掉 skip，绿了即验收。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    @unittest.skip("待实现：/api/start 接受 spec.json 并调用 plan_adapter")
    def test_start_accepts_spec_json(self):
        spec = json.loads((ROOT / "contracts" / "examples" / "spec.normal.json")
                          .read_text(encoding="utf-8"))
        with running_server(self.out) as (base, _):
            body = post_json(f"{base}/api/start", spec)
        self.assertTrue(body["accepted"])
        progress = body["progress"]
        self.assertTrue(str(progress["plan_id"]).startswith("PLAN-"))
        self.assertEqual(progress["spec_version"], f"v{spec['version']}")
        self.assertEqual(progress["total"], len(spec["modules"]))

    @unittest.skip("待实现：想法入口必须经需求模块生成规格后再分解（桥需可在测试内离线启动）")
    def test_requirement_goes_through_spec(self):
        with running_server(self.out) as (base, _):
            body = post_json(f"{base}/api/start",
                             {"requirement": "做一个校园二手书交易小程序，支持发布、搜索和线下交易"})
        progress = body["progress"]
        self.assertTrue(progress["plan_id"], "想法入口应产出计划号，说明走了 spec → plan")
        self.assertGreaterEqual(progress["total"], 2)
        self.assertEqual(progress["spec_version"], "v1")

    @unittest.skip("待实现：任务完成要落产物（artifact），交付记录才有据可依")
    def test_task_completion_records_artifact(self):
        with running_server(self.out) as (base, _):
            post_json(f"{base}/api/start",
                      {"tasks": [{"task_id": "T1", "name": "写接口", "suggested_role": "ai"}]})
            post_json(f"{base}/api/events",
                      {"type": "ai_report", "task_id": "T1", "result": "done", "detail": "完成"})
            for _ in range(20):
                time.sleep(0.25)
                progress = get_json(f"{base}/api/progress")
                if progress["tasks"][0]["status"] == "done":
                    break
        self.assertTrue(progress["tasks"][0].get("artifact_ids"),
                        "任务完成时应登记至少一个产物")
        artifacts = list((self.out / "artifacts").rglob("*")) if (self.out / "artifacts").exists() else []
        self.assertTrue(artifacts, "产物应有落盘文件或记录")

    @unittest.skip("待实现：全部任务终态后实例进入 delivery_pending 并生成交付记录")
    def test_all_done_enters_delivery_pending(self):
        with running_server(self.out) as (base, _):
            post_json(f"{base}/api/start",
                      {"tasks": [{"task_id": "T1", "name": "写接口", "suggested_role": "ai"}]})
            post_json(f"{base}/api/events",
                      {"type": "ai_report", "task_id": "T1", "result": "done", "detail": "完成"})
            for _ in range(20):
                time.sleep(0.25)
                progress = get_json(f"{base}/api/progress")
                if progress["status"] == "delivery_pending":
                    break
        self.assertEqual(progress["status"], "delivery_pending")
        delivery = progress["delivery"]
        self.assertIsNotNone(delivery)
        self.assertEqual(delivery["status"], "draft")
        self.assertEqual([item["task_id"] for item in delivery["items"]], ["T1"])

    @unittest.skip("待实现：交付确认后实例 completed，并记录 delivery 决策")
    def test_delivery_confirm_completes_instance(self):
        with running_server(self.out) as (base, _):
            post_json(f"{base}/api/start",
                      {"tasks": [{"task_id": "T1", "name": "写接口", "suggested_role": "ai"}]})
            post_json(f"{base}/api/events",
                      {"type": "ai_report", "task_id": "T1", "result": "done", "detail": "完成"})
            for _ in range(20):
                time.sleep(0.25)
                if get_json(f"{base}/api/progress")["status"] == "delivery_pending":
                    break
            post_json(f"{base}/api/events",
                      {"type": "human_op", "action": "approve", "operator": "tester"})
            for _ in range(20):
                time.sleep(0.25)
                progress = get_json(f"{base}/api/progress")
                if progress["status"] == "completed":
                    break
        self.assertEqual(progress["status"], "completed")
        self.assertEqual(progress["delivery"]["status"], "confirmed")
        self.assertEqual(progress["delivery"]["decided_by"], "human:tester")
        self.assertIn("delivery", [row["target_type"] for row in progress["decisions"]])

    @unittest.skip("待实现：/api/progress 返回 decisions")
    def test_progress_exposes_decisions(self):
        tasks = {"tasks": [{"task_id": "T1", "name": "确认验收", "suggested_role": "human"}]}
        with running_server(self.out) as (base, _):
            post_json(f"{base}/api/start", tasks)
            post_json(f"{base}/api/events",
                      {"type": "human_op", "action": "approve", "operator": "tester"})
            decisions = []
            for _ in range(20):
                time.sleep(0.25)
                decisions = get_json(f"{base}/api/progress").get("decisions") or []
                if decisions:
                    break
        self.assertEqual(len(decisions), 1)
        files = {"enums.schema.json": load_schema("enums.schema.json"),
                 "decision.schema.json": load_schema("decision.schema.json")}
        validator = Draft202012Validator(inline(files["decision.schema.json"], files))
        self.assertEqual([], sorted(validator.iter_errors(decisions[0]), key=str))
        self.assertEqual(decisions[0]["target_id"], "T1")


class OpenApiContractTest(unittest.TestCase):
    def setUp(self):
        try:
            import yaml
        except ImportError:  # pragma: no cover - CI 未装 pyyaml 时跳过
            self.skipTest("未安装 pyyaml，跳过 openapi 结构校验")
        self.spec = yaml.safe_load((V2 / "openapi.yaml").read_text(encoding="utf-8"))

    def test_start_accepts_spec_and_plan(self):
        start = self.spec["paths"]["/api/start"]["post"]["requestBody"]["content"][
            "application/json"]["schema"]
        ref = start["$ref"].rsplit("/", 1)[-1]
        targets = [item["$ref"] for item in
                   self.spec["components"]["schemas"][ref]["oneOf"]]
        self.assertIn("spec.schema.json", targets)
        self.assertIn("plan.schema.json", targets)

    def test_progress_declares_decisions(self):
        schema = self.spec["components"]["schemas"]["Progress"]
        self.assertEqual(schema["properties"]["decisions"]["items"]["$ref"],
                         "decision.schema.json")

    def test_internal_refs_resolve(self):
        serialized = json.dumps(self.spec)
        for ref in self.spec["components"]["responses"]:
            self.assertIn(f"#/components/responses/{ref}", serialized)


if __name__ == "__main__":
    unittest.main()
