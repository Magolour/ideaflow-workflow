# IdeaFlow 单 Agent 编排器

服务启动时**不读取、不加载任何本地任务清单**。外部传入任务 JSON 或一句话需求后才建立工作流；后续所有事件都在同一张图上增量迭代，不重新读取上传原文。

自带一个单页网页原型：输入需求 → 大模型拆解 → 自动建图 → 人工干预 → 重算，全部走真实接口，没有前端假数据。

## 一、目录与接口

```text
ideaflow_orchestrator/
├── python/
│   ├── agent.py            # HTTP 服务、编排、状态机与 output 写入
│   ├── requirements.txt
│   └── .env                # 本机模型配置，不发群
├── web/
│   └── index.html          # 网页原型：需求输入、动态图、人工干预
├── json/
│   ├── test_tasks.json     # 独立测试输入，仅供调用方使用
│   ├── ../contracts/legacy/v1.1/contract_package_v1.1.json  # 旧契约归档
│   └── uploaded_tasks.json # POST 成功后保存的上传原文
└── output/chat_miniapp/    # 上传前的数据产物为空
```

**4 个接口 + 1 个网页入口。** 网页入口只做展示和交互，本身不产生 output。

| 分类 | 接口 | 作用 | 对应文件 |
| --- | --- | --- | --- |
| 上游 | `POST /api/start` | 上传任务 JSON 或一句话需求，拆解并建图 | 保存 `json/uploaded_tasks.json`，写入 output |
| 执行与变更 | `POST /api/events` | AI 回报、人工操作、变更、恢复 | 基于当前工作流更新 output |
| 下游 | `GET /api/progress` | 获取进度和依赖；带 `since/limit` 时同时取事件 | 对应 output 中的状态与事件文件 |
| 服务检查 | `GET /api/health` | 检查服务及当前实例状态 | 无输出文件 |
| 网页 | `GET /` | 返回 `web/index.html` | 无输出文件 |

不再提供 `GET /api/start` 和 `GET /api/events`。上传原文仍保存在项目中，但不提供 GET 取回。

下游产物是 **2 个 JSON + 1 个 JSONL**，另有 1 张本地可选 PNG：

- `runtime_state.json`：当前任务与依赖状态。
- `event_stream_example.json`：完整事件流 JSON。
- `events.jsonl`：一行一个事件的追加式日志。
- `task_flow.png`：本地可视化，下游无需获取。

## 二、上下游主线

```text
上游
   │ POST /api/start：任务 JSON 或一句话需求
   ▼
单 Agent 编排器
   │ 大模型拆解/分析 → DAG 校验 → 并行分层 → 派发就绪任务
   │
   ├── AI 执行器：POST /api/events 回报 done / failed
   ├── 人工节点：POST /api/events 提交 approve / reject / skip
   └── 变更与恢复：POST /api/events 增量重排或解除阻塞
   ▼
下游
   └── GET /api/progress：当前状态；带 since/limit 时同时获取事件
```

## 三、启动服务

确保 `python/.env` 已配置真实模型，然后在项目根目录运行：

```powershell
py -3 python/agent.py serve --host 127.0.0.1 --port 8000 --out output/chat_miniapp
```

启动只创建服务与输出目录，**不会读取 `json/test_tasks.json`，也不会自动开始编排**。

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/health"
```

上传前 `started = false`，`/api/progress` 中 `total = 0`，output 没有任务数据。

网页原型：浏览器打开 `http://127.0.0.1:8000/`。若本机代理拦截 `127.0.0.1`，改用 `http://localhost:8000/`，或直接双击打开 `web/index.html`（页面会连回 `127.0.0.1:8000`）。

## 四、上游传入任务

### 方式一：一句话需求

```powershell
$packet = @{ requirement = "做一个聊天小程序，包含登录、会话列表、消息收发和未读提醒" } | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/start" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($packet))
```

PM Agent 会把需求拆成 5-12 个任务，交给同一套依赖分析与建图逻辑。

### 方式二：任务 JSON

```powershell
$json = Get-Content "json/test_tasks.json" -Raw -Encoding UTF8

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/start" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($json))
```

支持契约包、`{"tasks":[...]}` 或纯任务数组。

成功后：

- 原始请求体保存为 `json/uploaded_tasks.json`。
- output 生成 `runtime_state.json`、`event_stream_example.json`、`events.jsonl` 和 `task_flow.png`。
- 每个实例只接收一次清单，重复 `POST /api/start` 返回 `409`。

## 五、执行侧推动工作流

所有动作都通过 `POST /api/events`，返回 `202` 表示已入队，后台处理完成后状态才变化。

### 1. AI 回报

```json
{"type":"ai_report","task_id":"T2","result":"done","detail":"执行完成","priority":"high"}
```

- `task_id` 必须是当前 `running` 的任务，否则拒绝并写入 `last_error`。
- `result` 为 `failed` 时，前两次自动重试，第三次失败转人工。

### 2. 人工操作

```json
{"type":"human_op","action":"approve","operator":"demo","priority":"high"}
```

- `approve` 确认当前人工任务完成，立即派发下游。
- `reject`、`skip` 必须填写 `reason`。
- `reject` 会把该任务及下游未开始任务一并置为 `blocked`。
- 人工门不会阻塞同层其他 AI 任务并行执行。

### 3. 变更（自然语言）

```json
{"type":"change","change_id":"CR-001","change_type":"task","summary":"增加消息撤回能力并补充测试","priority":"high"}
```

服务以当前内存中的任务、状态和依赖做影响分析，新增任务与依赖合并到上一轮工作流；已完成和已跳过任务保持原状。

### 4. 人工改任务与依赖

在变更包里带上完整的 `tasks` 和 `edges`，直接替换当前图：

```json
{
  "type": "change",
  "change_id": "CR-002",
  "change_type": "task",
  "summary": "人工调整：改名并拆掉串行依赖",
  "tasks": [
    {"task_id":"T1","name":"需求梳理（已修订）","deliverable":"需求文档","acceptance_items":["评审通过"],"suggested_role":"ai","priority":"high"},
    {"task_id":"T2","name":"人工校验","deliverable":"校验记录","acceptance_items":["人工确认"],"suggested_role":"human","priority":"high"}
  ],
  "edges": [{"from":"T1","to":"T2","type":"force","reason":"人工补充"}],
  "priority": "high"
}
```

校验规则：端点必须存在、禁止自依赖、禁止环。已终态任务即使不在 `tasks` 里也会保留；未终态任务被删除则从图中移除。校验失败时整次变更不落地，错误写入 `last_error`。

### 5. 恢复阻塞

```json
{"type":"change","change_id":"CR-003","reopen":["T12","T13","T14"],"summary":"恢复被阻塞任务","priority":"high"}
```

把 `blocked` / `failed` 任务重置为 `pending` 并重新调度，已完成任务不受影响。要恢复下游，需同时把上游阻塞任务一起列入。

## 六、下游获取状态与事件

### 当前进度与依赖

不带查询参数时只返回状态，不返回事件：

```powershell
$state = Invoke-RestMethod "http://127.0.0.1:8000/api/progress"

$state | Select-Object status,total,done,running,pending,percent,waiting_on
$state.tasks
$state.edges
$state.layers
$state.execution_order
```

### 完整或增量事件

带 `since/limit` 时，响应在状态与依赖之外再包含 `event_log`、`next_since`、`has_more`：

```powershell
$batch = Invoke-RestMethod "http://127.0.0.1:8000/api/progress?since=0&limit=200"
$batch.event_log
$cursor = $batch.next_since

$nextBatch = Invoke-RestMethod "http://127.0.0.1:8000/api/progress?since=$cursor&limit=200"
$nextBatch.event_log
```

`since=0` 从第一条事件开始；`next_since` 是下次游标；`has_more` 表示是否还有更多。`since` 不能为负，`limit` 必须是大于 0 的整数，否则返回 `400`。GET 不会修改上传原文或 output。

## 七、状态机规则

| 状态 | 含义 |
| --- | --- |
| `pending` | 依赖未满足，等待派发 |
| `running` | AI 任务执行中，只接受该状态的回报 |
| `waiting_human` | 实例级状态，`waiting_on` 指向当前人工节点 |
| `done` / `skipped` | 终态，视为已完成，不再重置 |
| `blocked` | 被驳回或上游阻塞，需 `reopen` 恢复 |
| `failed` | 重试额度用尽的 AI 任务，转人工 |

要点：

- 就绪判据：所有入边的上游任务都处于终态。
- 人工节点同时最多一个，但不影响其他 AI 任务并行。
- 全部任务进入终态时实例为 `completed`；存在 `blocked` / `failed` 且无待执行任务时为 `blocked`。
- 变更、人工改图、恢复都会递增 `graph_id`，并触发 `replan_done` 事件。

## 八、网页原型

`web/index.html` 是单文件页面，接口与页面同源，不需要额外构建。

| 区域 | 对应接口 |
| --- | --- |
| 需求输入 → 生成任务并编排 | `POST /api/start` |
| AI 回报（自动填 running 任务） | `POST /api/events` |
| 放行 / 跳过 / 驳回 | `POST /api/events` |
| 变更（自然语言 / 手动改 tasks+edges） | `POST /api/events` |
| 恢复阻塞 | `POST /api/events` |
| 依赖图、状态卡、事件流 | `GET /api/progress?since=N&limit=200` |

图的节点位置来自 `layers`，连线来自 `edges`，颜色来自 `status`，每 1.5 秒轮询一次，前端不存任何业务数据。
