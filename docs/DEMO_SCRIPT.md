# 五步演示脚本（课堂作业 3）

- 版本：v1（2026-11-26）
- 用途：整组验收与答辩现场，一条干净的运行从头跑到尾
- 前置：本机 Python 可用，`pytest` 可跑；端口 8071 未被占用

## 0. 准备一次干净运行

```bash
cd "D:/软件工程/第二次课堂作业/ideaflow-workflow"
rm -rf output/demo5
python modules/orchestrator/python/agent.py serve --mock --port 8071 --out output/demo5
```

另开一个终端放公共函数：

```bash
cd "D:/软件工程/第二次课堂作业/ideaflow-workflow"
python - <<'PY'
import json, urllib.request, urllib.error
o = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕过系统代理
B = "http://127.0.0.1:8071"

def get(path):
    return json.load(o.open(B + path))

def post(path, data):
    req = urllib.request.Request(B + path, data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        return json.load(o.open(req)), 200
    except urllib.error.HTTPError as exc:
        return json.load(exc), exc.code

spec = json.load(open("contracts/examples/spec.normal.json", encoding="utf-8"))
body, code = post("/api/start", spec)
print(code, body["progress"]["status"], body["progress"]["plan_id"], body["progress"]["plan_version"])
PY
```

期望：`200 waiting_human PLAN-SPEC-001 1`

---

## 第 1 步：改一条需求，规格升版（成员 2）

> 依赖：T-07 / T-08（需求工作台）。本步在需求工作台页面上操作，不在编排器页面。

1. 起需求工作台：`cd modules/requirements && python -m ideaflow.server --port 8073 --out output/demo5/requirements`
2. 打开 `http://127.0.0.1:8073/`，选一次运行，在「需求快照」页签把一条 R 条目改两三个字并保存；
3. 观察：

| 检查点 | 期望 |
| --- | --- |
| 规格版本 | 比修改前 +1 |
| 审计 | 自动重跑，页面显示新的 `counts` |
| 旧版本 | `spec.v{旧版本}.json`、`audit.v{旧版本}.json` 仍在 |
| 事件 | 出现 `requirement_revised` 与 `spec_reaudited` |

失败样例（可选）：把条目文本改成空，应返回 `INVALID_TEXT`，版本不变。

---

## 第 2 步：图编辑与计划评审（成员 5 演示，成员 1 补充）

> 依赖：T-05 / T-06（图编辑）、T-12（计划评审可见）。D3 前接口返回 404 属预期。

```bash
# 2.1 加一条边（合法）
python - <<'PY'
import json, urllib.request, urllib.error
o = urllib.request.build_opener(urllib.request.ProxyHandler({}))
B = "http://127.0.0.1:8071"
p = json.load(o.open(B + "/api/progress"))
body, code = None, None
req = urllib.request.Request(B + "/api/graph/edit",
    data=json.dumps({"plan_id": p["plan_id"], "plan_version": p["plan_version"],
                     "ops": [{"op": "add_edge", "from": "T1", "to": "T2", "type": "force"}],
                     "actor": "demo", "reason": "演示加边"}).encode(),
    headers={"Content-Type": "application/json"})
try:
    body = json.load(o.open(req)); code = 200
except urllib.error.HTTPError as exc:
    body = json.load(exc); code = exc.code
print(code, body)
PY
```

| 检查点 | 期望 |
| --- | --- |
| 合法加边 | 200，`plan.version` +1，进度回到 `waiting_human`（`gate_type=plan_approval`） |
| 事件 | 新增 `plan_edited`、`graph_validated` |

```bash
# 2.2 故意成环（非法）
# 把上面的 from/to 换成 {"from": "T3", "to": "T1"}，重跑
```

| 检查点 | 期望 |
| --- | --- |
| 成环 | 409，`error=GRAPH_CYCLE`，`details.cycle` 列出环节点 |
| 完整性 | 事件流没有新增事件，计划版本不变 |

2.3 展开审批面板：能看到计划评审问题（`plan_review.issues`），每条带 `code`、`task_id`、`message`；若缺测试任务，应出现 `TEST_MISSING`。

---

## 第 3 步：真实执行证据与交付三态（成员 3）

> 依赖：T-02（证据与三态）、T-16（生成测试任务）。执行器未接入前，用人工参考实现 + 手写 `evidence.json` 验证接线。

```bash
# 3.1 批准计划，观察是否开始派发
python - <<'PY'
import json, urllib.request
o = urllib.request.build_opener(urllib.request.ProxyHandler({}))
B = "http://127.0.0.1:8071"
p = json.load(o.open(B + "/api/progress"))
req = urllib.request.Request(B + "/api/events",
    data=json.dumps({"type": "human_op", "action": "approve", "operator": "demo",
                     "plan_id": p["plan_id"], "plan_version": p["plan_version"]}).encode(),
    headers={"Content-Type": "application/json"})
o.open(req)
print("approved")
PY
```

| 检查点 | 期望 |
| --- | --- |
| 计划包含测试任务 | `tasks[].kind` 里同时出现 `code` 与 `test`，测试任务 `depends_on` 指向编码任务 |
| 证据字段 | 任务完成后 `tasks[].evidence` 非空，含 `artifact_id`、`exit_code`、`duration_ms` |
| 交付三态 | 任一 `exit_code != 0` → `failed`；全 0 且有证据 → `passed`；无证据 → `unverified` |

3.2 造一个失败任务：把测试命令改成必然失败（例如 `python -c "raise SystemExit(2)"`），重跑该任务，交付清单应显示 `failed` 且能展开看到退出码 2。

---

## 第 4 步：暂停与恢复（成员 4）

> 依赖：T-04。D5 前未实现时，本步可略。

```bash
# 4.1 找一条 running 任务后暂停
python - <<'PY'
import json, urllib.request
o = urllib.request.build_opener(urllib.request.ProxyHandler({}))
B = "http://127.0.0.1:8071"
p = json.load(o.open(B + "/api/progress"))
running = [t["task_id"] for t in p["tasks"] if t["status"] == "running"]
print("running:", running)
if running:
    req = urllib.request.Request(B + "/api/events",
        data=json.dumps({"type": "human_op", "action": "pause",
                         "task_id": running[0], "operator": "demo"}).encode(),
        headers={"Content-Type": "application/json"})
    o.open(req)
    print("paused", running[0])
PY
```

| 检查点 | 期望 |
| --- | --- |
| 暂停 | 该任务不再被派发，`running` 数不变；事件 `task_paused` |
| 恢复 | 发 `action=resume` 后继续推进 |
| 范围 | 失败时错误码说明暂停范围（单任务还是整实例） |

---

## 第 5 步：回放与终态总结（成员 3）

> 依赖：T-09（回放）、T-10（总结）、T-11（展示）。

```bash
python - <<'PY'
import json, urllib.request, urllib.error
o = urllib.request.build_opener(urllib.request.ProxyHandler({}))
B = "http://127.0.0.1:8071"

# 5.1 回放到中途序号
for seq in (3, 5):
    try:
        r = json.load(o.open(f"{B}/api/replay?seq={seq}"))
        print(seq, r["state"]["status"], len(r["state"]["tasks"]))
    except urllib.error.HTTPError as exc:
        print(seq, exc.code, json.load(exc)["error"])

# 5.2 终态总结
try:
    s = json.load(o.open(B + "/api/summary"))
    print("summary:", s["duration"]["seconds"], s["rework"], s["coverage"])
except urllib.error.HTTPError as exc:
    print("summary", exc.code, json.load(exc)["error"])
PY
```

| 检查点 | 期望 |
| --- | --- |
| 回放 | 任意 `seq` 的状态与 `runtime_state.json` 重建结果一致；越界返回 `400 SEQ_OUT_OF_RANGE` |
| 总结 | 终态返回 `duration / gate_waits / rework / failures / coverage / open_items`；未终态返回 `409 NOT_TERMINAL` |
| 零分母 | 需求条数为 0 时 `coverage = "not_applicable"`，不判通过 |

---

## 截图清单（成员 5）

| 文件 | 画面 |
| --- | --- |
| `docs/screenshots/w11-01-plan-review.png` | 审批面板展开计划评审问题 |
| `docs/screenshots/w11-02-graph-error.png` | 成环被拒并指出任务 |
| `docs/screenshots/w11-03-evidence.png` | 检查器里的证据列表（命令与退出码） |
| `docs/screenshots/w11-04-delivery.png` | 交付三态（passed / failed / unverified） |
| `docs/screenshots/w11-05-summary.png` | 终态总结卡片 |

## 备注

- 每一步失败时先看 `GET /api/progress` 的 `last_error`，再看事件流最后三条；
- 演示前确认没有旧实例：重复 `POST /api/start` 会返回 409，需要换 `--out` 目录；
- 契约相关的口径以 `contracts/README.md` 第 16–18 条与设计文档 §6.10 为准。
