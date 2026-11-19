"""Web 工作台测试：Markdown 渲染、接口契约与下载行为。

服务在测试进程内以随机端口启动，用标准库 urllib 发真实请求，
覆盖“提交 → 轮询 → 下载”的完整链路。
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from ideaflow.render_html import document_html, render_markdown
from ideaflow.server import make_server


class RenderTest(unittest.TestCase):
    def test_renders_headings_and_paragraphs(self) -> None:
        html = render_markdown("# 标题\n\n第一段\n第二段\n\n## 二级\n\n正文")
        self.assertIn('<h1 id="h1">标题</h1>', html)
        self.assertIn("<p>第一段第二段</p>", html)  # 中文换行直接拼接
        self.assertIn('<h2 id="h2">二级</h2>', html)

    def test_renders_table_blockquote_and_inline(self) -> None:
        source = (
            "> 引用 **重点**\n\n"
            "| 编号 | 结论 |\n| --- | --- |\n| DEC-01 | 先做核心流程 |\n\n"
            "- 第一项\n- 第二项，含 `code`\n"
        )
        html = render_markdown(source)
        self.assertIn("<blockquote><p>引用 <strong>重点</strong></p></blockquote>", html)
        self.assertIn("<th>编号</th>", html)
        self.assertIn("<td>DEC-01</td>", html)
        self.assertIn("<code>code</code>", html)
        self.assertEqual(html.count("<li>"), 2)

    def test_escapes_raw_html(self) -> None:
        html = render_markdown('<script>alert("x")</script>')
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_document_html_is_standalone(self) -> None:
        markdown = "# 我的提案\n\n## 一\n\n内容\n\n## 二\n\n内容\n\n## 三\n\n内容"
        page = document_html(markdown)
        self.assertTrue(page.startswith("<!DOCTYPE html>"))
        self.assertIn("<title>我的提案</title>", page)
        self.assertIn('class="toc"', page)  # 三个二级标题生成目录
        # 目录必须排在标题之后，不能跑到标题上面
        self.assertLess(page.index("<h1"), page.index('class="toc"'))

    def test_link_scheme_is_restricted(self) -> None:
        html = render_markdown("[危险](javascript:alert(1))")
        self.assertNotIn("javascript:", html)
        self.assertIn("<span>危险</span>", html)


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.runs = Path(cls.tmp.name) / "runs"
        cls.server = make_server("127.0.0.1", 0, cls.runs, quiet=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    # ---------- 辅助 ----------

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        try:
            with urllib.request.urlopen(self.url(path), timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def post(self, path: str, payload: dict) -> tuple[int, bytes]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url(path), data=data, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def wait_job(self, job_id: str, timeout: float = 20.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, _, body = self.get(f"/api/job?id={job_id}")
            self.assertEqual(status, 200)
            job = json.loads(body)["job"]
            if job["state"] in ("done", "error"):
                return job
            time.sleep(0.05)
        self.fail("任务超时未完成")

    # ---------- 用例 ----------

    def test_index_and_static_assets_are_served(self) -> None:
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("IdeaFlow", body.decode("utf-8"))

        status, headers, body = self.get("/static/paper.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers["Content-Type"])
        self.assertIn(b".paper", body)

    def test_health_reports_runs_dir(self) -> None:
        status, _, body = self.get("/api/health")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(Path(payload["runs_dir"]), self.runs.resolve())

    def test_generate_then_download(self) -> None:
        status, body = self.post("/api/generate", {
            "idea": "做一个校园二手书交易小程序",
            "constraints": ["两周内出演示版"],
            "max_rounds": 1,
        })
        self.assertEqual(status, 202)
        job = self.wait_job(json.loads(body)["job_id"])
        self.assertEqual(job["state"], "done", job.get("error"))

        result = job["result"]
        self.assertTrue(result["markdown"].startswith("# "))
        self.assertIn("<h2", result["html"])
        self.assertTrue(result["options"])
        self.assertTrue(result["objections"])
        self.assertIn("label", result["consensus"])
        self.assertIn(result["consensus"]["state"],
                      ("consensus", "conditional", "pending_verification", "no_consensus"))
        self.assertEqual(result["constraints"], ["两周内出演示版"])
        self.assertTrue((self.runs / result["run_id"] / "proposal.md").exists())

        status, headers, body = self.get(
            f"/api/download?run_id={quote(result['run_id'])}&format=md")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn("filename*=UTF-8''", headers["Content-Disposition"])
        self.assertIn("校园二手书交易", body.decode("utf-8"))

        status, headers, body = self.get(
            f"/api/download?run_id={quote(result['run_id'])}&format=html")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"<!DOCTYPE html>", body)

    def test_rejects_empty_idea(self) -> None:
        status, body = self.post("/api/generate", {"idea": "   "})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "EMPTY_IDEA")

    def test_rejects_invalid_rounds(self) -> None:
        status, body = self.post("/api/generate", {"idea": "做一个工具", "max_rounds": 9})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "INVALID_FIELD")

    def test_unknown_job_and_run(self) -> None:
        status, _, body = self.get("/api/job?id=not-exist")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"]["code"], "UNKNOWN_JOB")

        status, _, body = self.get("/api/download?run_id=..%2F..%2Fetc&format=md")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"]["code"], "UNKNOWN_RUN")

    def test_case_endpoint(self) -> None:
        status, _, body = self.get("/api/case?name=campus-books-tight")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIn("校园二手书", payload["title"])
        self.assertNotIn("# 校园", payload["text"])

    def test_unknown_case_is_rejected(self) -> None:
        status, body = self.post("/api/generate", {"flow": "spec", "case": "nope"})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "INVALID_CASE")

    def test_spec_flow_and_downloads(self) -> None:
        status, body = self.post("/api/generate", {
            "flow": "spec", "case": "campus-books-tight", "idea": "案例",
        })
        self.assertEqual(status, 202)
        job = self.wait_job(json.loads(body)["job_id"], timeout=40)
        self.assertEqual(job["state"], "done", job.get("error"))
        self.assertEqual(job["flow"], "spec")

        result = job["result"]
        self.assertEqual(result["flow"], "spec")
        self.assertEqual(result["status"], "awaiting_human")
        self.assertEqual([row["spec_version"] for row in result["audits"]], [1, 2])
        self.assertEqual(len(result["revisions"]), 1)
        self.assertEqual(result["question"]["id"], "HUMAN-Q-01")
        self.assertEqual(len(result["drafts"]), 2)

        run_id = quote(result["run_id"])
        status, headers, body = self.get(
            f"/api/download?run_id={run_id}&artifact=spec-md")
        self.assertEqual(status, 200)
        self.assertIn("text/markdown", headers["Content-Type"])
        text = body.decode("utf-8")
        self.assertIn("# 设计规格", text)
        self.assertIn("需人工决策", text)

        status, headers, body = self.get(
            f"/api/download?run_id={run_id}&artifact=spec-json")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["hash"], result["spec"]["hash"])
        self.assertEqual(payload["artifacts"]["markdown"], result["files"]["markdown"])

        status, _, body = self.get(
            f"/api/download?run_id={run_id}&artifact=question")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["id"], "HUMAN-Q-01")

        status, body = self.post("/api/generate", {"flow": "spec",
                                                   "case": "campus-books-tight"})
        self.assertEqual(status, 202)
        job = self.wait_job(json.loads(body)["job_id"], timeout=40)
        status, _, body = self.get(
            f"/api/download?run_id={quote(job['result']['run_id'])}&artifact=nope")
        self.assertEqual(status, 400)



if __name__ == "__main__":
    unittest.main()
