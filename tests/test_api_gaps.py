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
