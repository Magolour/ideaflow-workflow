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

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "contracts" / "v2"
sys.path.insert(0, str(ROOT / "modules" / "orchestrator" / "python"))

from agent import AgentHandler, AgentServer, RealtimeAgent  # noqa: E402


def load_schema(name: str) -> dict:
    return json.loads((V2 / name).read_text(encoding="utf-8"))


def inline(node, by_file):
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            if ref.startswith("#/components/schemas/"):
                return inline(by_file[ref.rsplit("/", 1)[-1]], by_file)
            file_name, _, fragment = ref.partition("#")
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
    with _OPENER.open(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str) -> dict:
    with _OPENER.open(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


class RuntimeProgressContractTest(unittest.TestCase):
    """真实运行时快照必须过 Progress 契约，防止前端字段与实现分叉。"""

    def setUp(self):
        try:
            import yaml
        except ImportError:  # pragma: no cover
            self.skipTest("未安装 pyyaml")
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.out = Path(self.tmp.name)
        openapi = yaml.safe_load((V2 / "openapi.yaml").read_text(encoding="utf-8"))
        schemas = {path.name: load_schema(path.name) for path in V2.glob("*.json")}
        schemas["Progress"] = openapi["components"]["schemas"]["Progress"]
        schemas["TaskState"] = openapi["components"]["schemas"]["TaskState"]
        schemas["Edge"] = openapi["components"]["schemas"]["Edge"]
        self.validator = Draft202012Validator(inline(schemas["Progress"], schemas))

    def tearDown(self):
        self.tmp.cleanup()

    def test_progress_matches_contract(self):
        agent = RealtimeAgent(out_dir=self.out, mock=True,
                              upload_path=self.out / "uploaded.json")
        state = agent.start({"tasks": [
            {"task_id": "T1", "name": "确认边界", "suggested_role": "human"},
            {"task_id": "T2", "name": "写接口", "suggested_role": "ai"},
        ]}, b"{}")
        payload = {**state, "edges": agent.edges}
        errors = sorted(self.validator.iter_errors(payload), key=str)
        self.assertEqual([], [f"{list(error.absolute_path)} {error.message}" for error in errors])


class ApiGapTest(unittest.TestCase):
    """成员 4 实现后去掉 skip，绿了即验收。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

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

    def test_requirement_goes_through_spec(self):
        with running_server(self.out) as (base, _):
            body = post_json(f"{base}/api/start", {"requirement": (
                "做一个校园二手书交易小程序。"
                "学生可以发布二手教材。"
                "学生可以按书名搜索教材。"
                "买家可以在线付款并线下自提。")})
        progress = body["progress"]
        self.assertTrue(progress["plan_id"], "想法入口应产出计划号，说明走了 spec → plan")
        self.assertGreaterEqual(progress["total"], 2,
                                "多句需求应拆出多个模块与任务")
        self.assertEqual(progress["spec_version"], "v1")

    def test_plan_dependencies_used_without_model(self):
        plan = {
            "plan_id": "PLAN-001",
            "version": 1,
            "spec_ref": {"spec_id": "SPEC-001", "version": 1, "hash": "a" * 64},
            "status": "draft",
            "created_at": "2026-11-20T11:00:00+08:00",
            "tasks": [
                {"task_id": "T1", "name": "拆模块", "deliverable": "模块清单",
                 "acceptance_items": ["评审通过"], "suggested_role": "ai",
                 "priority": "high"},
                {"task_id": "T2", "name": "写接口", "deliverable": "接口代码",
                 "acceptance_items": ["可用"], "suggested_role": "ai",
                 "priority": "high", "depends_on": ["T1"]},
                {"task_id": "T3", "name": "写页面", "deliverable": "页面",
                 "acceptance_items": ["可点"], "suggested_role": "ai",
                 "priority": "high", "depends_on": ["T1"]},
            ],
        }
        with running_server(self.out) as (base, agent):
            post_json(f"{base}/api/start", plan)
        pairs = {(edge["from"], edge["to"]) for edge in agent.edges}
        self.assertEqual(pairs, {("T1", "T2"), ("T1", "T3")},
                         "应采用计划依赖，而不是按顺序串成链")
        self.assertEqual({edge["type"] for edge in agent.edges}, {"force"})
        self.assertNotIn("mock 模式", agent.analysis_summary)

    def test_task_provenance_reaches_progress(self):
        from plan_adapter import plan_from_spec
        spec = json.loads((ROOT / "contracts" / "examples" / "spec.normal.json")
                          .read_text(encoding="utf-8"))
        plan = plan_from_spec(spec, plan_id="PLAN-001")
        with running_server(self.out) as (base, _):
            body = post_json(f"{base}/api/start", plan)
        task = body["progress"]["tasks"][0]
        self.assertTrue(task.get("requirement_refs"), "任务应保留对需求条款的引用")
        self.assertTrue(task.get("design_refs"), "任务应保留对设计模块的引用")

    def test_task_completion_records_artifact(self):
        with running_server(self.out) as (base, _):
            post_json(f"{base}/api/start",
                      {"tasks": [{"task_id": "T1", "name": "写接口",
                                 "deliverable": "接口清单", "suggested_role": "ai"}]})
            post_json(f"{base}/api/events",
                      {"type": "ai_report", "task_id": "T1", "result": "done", "detail": "完成"})
            for _ in range(20):
                time.sleep(0.25)
                progress = get_json(f"{base}/api/progress")
                if progress["tasks"][0]["status"] == "done":
                    break
        task = progress["tasks"][0]
        self.assertEqual(task["status"], "done")
        self.assertEqual(len(task["artifact_ids"]), 1)
        artifact_id = task["artifact_ids"][0]
        artifact = json.loads((self.out / "artifacts" / artifact_id / "artifact.json")
                              .read_text(encoding="utf-8"))
        files = {name: load_schema(name) for name in
                 ("artifact.schema.json", "enums.schema.json")}
        Draft202012Validator(inline(files["artifact.schema.json"], files)).validate(artifact)
        self.assertEqual(artifact["artifact_id"], artifact_id)
        self.assertEqual(artifact["task_id"], task["task_id"])
        self.assertEqual(artifact["summary"], "接口清单")

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
        self.assertEqual(delivery["items"][0]["artifact_ids"],
                         progress["tasks"][0]["artifact_ids"])
        self.assertEqual(progress["decisions"], [])

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
        delivery = progress["delivery"]
        self.assertEqual(delivery["status"], "confirmed")
        self.assertEqual(delivery["decided_by"], "human:tester")
        self.assertEqual(json.loads((self.out / "delivery.json").read_text(encoding="utf-8")),
                         delivery)
        self.assertEqual(len(progress["decisions"]), 1)
        decision = progress["decisions"][0]
        self.assertEqual(decision["target_type"], "delivery")
        self.assertEqual(decision["target_id"], delivery["delivery_id"])
        self.assertEqual(decision["decision_id"], delivery["decision_id"])
        self.assertEqual(decision["action"], "approve")
        self.assertEqual([json.loads(line) for line in
                          (self.out / "decisions.jsonl").read_text(encoding="utf-8").splitlines()],
                         [decision])
        files = {name: load_schema(name) for name in
                 ("delivery.schema.json", "decision.schema.json", "enums.schema.json")}
        Draft202012Validator(inline(files["delivery.schema.json"], files)).validate(delivery)
        Draft202012Validator(inline(files["decision.schema.json"], files)).validate(decision)

    def test_delivery_reject_requires_reason_and_blocks_instance(self):
        def wait_for(base, predicate):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                progress = get_json(f"{base}/api/progress?since=0")
                if predicate(progress):
                    return progress
                time.sleep(0.05)
            self.fail(f"运行未达到预期状态：{progress}")

        reason = "交付物缺少验证记录"
        with running_server(self.out) as (base, _):
            post_json(f"{base}/api/start",
                      {"tasks": [{"task_id": "T1", "name": "写接口", "suggested_role": "ai"}]})
            post_json(f"{base}/api/events",
                      {"type": "ai_report", "task_id": "T1", "result": "done"})
            wait_for(base, lambda progress: progress["status"] == "delivery_pending")

            post_json(f"{base}/api/events",
                      {"type": "human_op", "action": "reject", "reason": " "})
            pending = wait_for(base, lambda progress: bool(progress["last_error"]))
            self.assertIn("必须填写理由", pending["last_error"])
            self.assertEqual(pending["status"], "delivery_pending")
            self.assertEqual(pending["delivery"]["status"], "draft")
            self.assertEqual(pending["decisions"], [])

            post_json(f"{base}/api/events",
                      {"type": "human_op", "action": "reject", "reason": reason,
                       "operator": "tester"})
            blocked = wait_for(base, lambda progress: progress["status"] == "blocked")

            post_json(f"{base}/api/events", {"type": "human_op", "action": "approve"})
            replay = wait_for(base, lambda progress: bool(progress["last_error"]))
            self.assertEqual(replay["status"], "blocked")
            self.assertEqual(replay["decisions"], blocked["decisions"])

        delivery = blocked["delivery"]
        self.assertEqual(delivery["status"], "rejected")
        self.assertEqual(delivery["decided_by"], "human:tester")
        self.assertEqual(len(blocked["decisions"]), 1)
        decision = blocked["decisions"][0]
        self.assertEqual((decision["target_type"], decision["action"], decision["reason"]),
                         ("delivery", "reject", reason))
        self.assertEqual(decision["target_id"], delivery["delivery_id"])
        self.assertEqual(decision["decision_id"], delivery["decision_id"])
        self.assertEqual(json.loads((self.out / "delivery.json").read_text(encoding="utf-8")),
                         delivery)
        self.assertEqual([json.loads(line) for line in
                          (self.out / "decisions.jsonl").read_text(encoding="utf-8").splitlines()],
                         [decision])
        self.assertIn("delivery_rejected", [row["action"] for row in blocked["event_log"]])
        files = {name: load_schema(name) for name in
                 ("delivery.schema.json", "decision.schema.json", "enums.schema.json")}
        Draft202012Validator(inline(files["delivery.schema.json"], files)).validate(delivery)
        Draft202012Validator(inline(files["decision.schema.json"], files)).validate(decision)

    def test_full_chain_from_idea_to_delivery(self):
        with running_server(self.out) as (base, _):
            body = post_json(f"{base}/api/start", {"requirement": (
                "做一个校园二手书交易小程序。"
                "学生可以发布二手教材。"
                "学生可以按书名搜索教材。"
                "买家可以在线付款并线下自提。")})
            progress = body["progress"]
            self.assertTrue(progress["plan_id"], "想法入口应产出计划号")
            self.assertEqual(progress["spec_version"], "v1", "规格版本应来自需求模块")
            self.assertGreaterEqual(progress["total"], 3, "应拆出多个任务")

            for _ in range(40):
                time.sleep(0.25)
                progress = get_json(f"{base}/api/progress")
                if progress["status"] in {"delivery_pending", "blocked"}:
                    break
            self.assertEqual(progress["status"], "delivery_pending",
                             "任务应自动执行完并等待交付确认")

            post_json(f"{base}/api/events",
                      {"type": "human_op", "action": "approve", "operator": "tester"})
            for _ in range(20):
                time.sleep(0.25)
                progress = get_json(f"{base}/api/progress")
                if progress["status"] == "completed":
                    break
            self.assertEqual(progress["status"], "completed")
            self.assertEqual(progress["delivery"]["status"], "confirmed")
            self.assertIn("delivery", [row["target_type"] for row in progress["decisions"]])

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

    def test_component_schemas_are_valid(self):
        """openapi 里的内联 schema 也要过 Draft 2020-12 元校验。"""
        schemas = dict(self.spec["components"]["schemas"])
        by_file = {path.name: load_schema(path.name) for path in V2.glob("*.json")}
        by_file.update(schemas)
        for name, schema in schemas.items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(inline(schema, by_file))

    def test_internal_refs_resolve(self):
        serialized = json.dumps(self.spec)
        for ref in self.spec["components"]["responses"]:
            self.assertIn(f"#/components/responses/{ref}", serialized)


if __name__ == "__main__":
    unittest.main()
