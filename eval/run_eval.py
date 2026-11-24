"""评测入口：同一批案例上比较多 Agent 流程与单 Agent 基线。

用法：
    python eval/run_eval.py                      # 全部案例，离线（Mock）
    python eval/run_eval.py --case device-booking
    python eval/run_eval.py --real               # 单 Agent 基线走真实模型

输出 eval/report.json 与 eval/report.md。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "requirements"))
sys.path.insert(0, str(ROOT / "modules" / "orchestrator" / "python"))

from metrics import RULES, summarize  # noqa: E402

CASES_DIR = Path(__file__).resolve().parent / "cases"
DEFAULT_BASE_URL = "https://ws-381dxklyh659xv9g.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash-1017"

BASELINE_PROMPT = (
    "你是需求分析师。把下面的产品想法整理成需求清单，"
    "只输出 JSON 对象：{\"requirements\":[\"一条一句话的需求\", ...]}，不要解释。"
)


def git_revision() -> tuple[str, bool]:
    """取当前提交与是否有未提交改动，报告据此可复核。"""
    import subprocess
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"],
                                    capture_output=True, text=True, check=True).stdout.strip())
        return commit or "unknown", dirty
    except Exception:  # noqa: BLE001 - 无 git 时不影响评测
        return "unknown", False


def load_case_meta(name: str) -> dict:
    return json.loads((CASES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def case_names(selector: str) -> list[str]:
    if selector == "all":
        return sorted(path.stem for path in CASES_DIR.glob("*.json"))
    return [selector]


def run_multi(case_name: str, agent=None) -> tuple[list[str], list[dict], dict]:
    """多 Agent：需求模块跑完整流程，再经 plan_adapter 出计划。"""
    from ideaflow.specflow import load_case, run as run_spec  # noqa: WPS433
    from plan_adapter import plan_from_spec  # noqa: WPS433

    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        result = run_spec(load_case(case_name), out_dir=tmp, agent=agent,
                          discuss=agent is not None)
        spec = result.spec.to_json()
        calls, tokens = 0, 0
        log = result.out_dir / "ai_calls.jsonl"
        if log.exists():
            for line in log.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                calls += 1
                record = json.loads(line)
                tokens += (record.get("prompt_tokens") or 0) + (record.get("completion_tokens") or 0)
    duration = time.perf_counter() - started

    # 覆盖率只看需求与约束；待澄清项不计入，否则讨论自己生成的条目会抬高覆盖率
    texts = [item["text"] for item in result.snapshot.requirements]
    texts += [item["text"] for item in result.snapshot.constraints]
    tasks = plan_from_spec(spec)["tasks"]
    human = len(result.snapshot.questions) + (1 if result.question else 0)
    return texts, tasks, {"duration_s": duration, "tokens": tokens or None,
                          "human_interventions": human, "ai_calls": calls}


def chat_with_usage(system: str, user: str) -> tuple[str, int | None]:
    api_key = os.getenv("AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""
    if not api_key:
        raise SystemExit("单 Agent 基线需要 AI_API_KEY（写在 python/.env 或环境变量）")
    base_url = os.getenv("AI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    payload = {
        "model": os.getenv("AI_MODEL", DEFAULT_MODEL),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
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
    with urllib.request.urlopen(request, timeout=120) as response:
        body = json.loads(response.read().decode("utf-8"))
    usage = body.get("usage") or {}
    tokens = usage.get("total_tokens")
    return body["choices"][0]["message"]["content"], tokens


def run_baseline(case_name: str, real: bool) -> tuple[list[str], list[dict], dict]:
    """单 Agent：一次调用产出需求清单；Mock 模式只用于跑通流程。"""
    from ideaflow.specflow import load_case  # noqa: WPS433
    from plan_adapter import plan_from_spec  # noqa: WPS433

    raw = load_case(case_name)
    started = time.perf_counter()
    if real:
        content, tokens = chat_with_usage(BASELINE_PROMPT, raw)
        start, end = content.find("{"), content.rfind("}")
        data = json.loads(content[start:end + 1])
        texts = [str(item) for item in data.get("requirements") or []]
    else:
        lines = [line.strip() for line in raw.splitlines()
                 if line.strip() and not line.strip().startswith("#")]
        texts = lines[:2]
        tokens = None
    duration = time.perf_counter() - started

    # 基线也要出计划，指标口径才可比；没有规格时按需求条目一比一建任务
    spec = {
        "spec_id": f"SPEC-{case_name.upper()}",
        "version": 1,
        "modules": [
            {"id": f"M-{index:02d}", "name": text[:20], "responsibility": text,
             "requirement_ids": [], "decision_ids": [], "interfaces": [],
             "estimate_days": 1, "deliverable": text[:20],
             "acceptance_items": ["验收通过"], "depends_on": [],
             "write_paths": [], "suggested_role": "ai"}
            for index, text in enumerate(texts, 1)
        ],
    }
    tasks = plan_from_spec(spec)["tasks"] if spec["modules"] else []
    return texts, tasks, {"duration_s": duration, "tokens": tokens,
                          "human_interventions": 0,
                          "ai_calls": 1 if real else 0}


def to_markdown(report: dict) -> str:
    header = (f"模型：{report['model']} ｜ 提交：{report['commit']}"
              f"{'（有未提交改动）' if report['dirty'] else ''}"
              f" ｜ 生成时间：{report['generated_at']}\n\n")
    rules = "判定规则：\n" + "\n".join(f"- {key}：{text}"
                                   for key, text in report["scoring_rules"].items()) + "\n\n"
    rows = ["| 案例 | 模式 | 覆盖率 | 严重遗漏 | 需求条数 | 任务数 | 无效依赖 | 人工介入 | 调用数 | 耗时(s) | tokens |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in report["results"]:
        rows.append(
            f"| {row['case']} | {row['mode']} | {row['requirements']['coverage_rate']:.0%} "
            f"| {len(row['requirements']['severe_missing'])} "
            f"| {row['requirements']['count']} | {row['plan']['tasks']} "
            f"| {len(row['plan']['invalid_dependencies'])} "
            f"| {row['run']['human_interventions']} "
            f"| {row['run'].get('ai_calls', '-')} "
            f"| {row['run']['duration_s']} | {row['run']['tokens'] or '-'} |")
    failed = [row for row in report["results"] if row.get("errors")]
    notes = ""
    if failed:
        notes = "\n失败原因：\n" + "\n".join(
            f"- {row['case']}/{row['mode']}：{'；'.join(row['errors'])}" for row in failed) + "\n"
    return header + rules + "\n".join(rows) + "\n" + notes


def main() -> int:
    parser = argparse.ArgumentParser(description="IdeaFlow 评测")
    parser.add_argument("--case", default="all", help="案例名或 all")
    parser.add_argument("--mode", default="both", choices=["multi", "baseline", "both"])
    parser.add_argument("--real", action="store_true",
                        help="多 Agent 与单 Agent 基线都调用真实模型")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()

    agent = None
    if args.real:
        from ideaflow.agents import LLMAgent  # noqa: WPS433

        try:
            agent = LLMAgent()
        except Exception as error:  # noqa: BLE001
            print(f"真实调用未就绪：{error}")
            return 2

    results = []
    for name in case_names(args.case):
        case = load_case_meta(name)
        runners = []
        if args.mode in ("multi", "both"):
            runners.append(("multi", lambda n=name: run_multi(n, agent)))
        if args.mode in ("baseline", "both"):
            runners.append(("baseline", lambda n=name: run_baseline(n, args.real)))
        for mode, runner in runners:
            try:
                texts, tasks, run = runner()
            except Exception as error:  # noqa: BLE001 - 单条失败不拖垮整份报告
                reason = f"{type(error).__name__}: {error}"
                results.append(summarize(case, [], [], mode=mode, duration_s=0.0,
                                         tokens=None, human_interventions=0,
                                         errors=[reason]))
                print(f"[{name}/{mode}] 失败：{reason}")
                continue
            results.append(summarize(case, texts, tasks, mode=mode, **run))
            print(f"[{name}/{mode}] 覆盖 {results[-1]['requirements']['coverage_rate']:.0%}，"
                  f"任务 {len(tasks)}，无效依赖 {len(results[-1]['plan']['invalid_dependencies'])}")

    commit, dirty = git_revision()
    model = ((getattr(agent, "model", "") if agent else "")
             or (os.getenv("AI_MODEL") if args.real else "")
             or "mock")
    report = {
        "model": model,
        "commit": commit,
        "dirty": dirty,
        "generated_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "scoring_rules": RULES,
        "cases": list({row["case"] for row in results}),
        "real_baseline": args.real,
        "real_multi": bool(agent),
        "results": results,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "report.md").write_text(to_markdown(report), encoding="utf-8")
    print(f"报告已写入 {args.out / 'report.json'} 与 report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
