# 可视化模块（成员 5：工作台与可视化）

`../orchestrator/web/index.html` 是工作流唯一的操作界面：输入入口、审查详情、人工放行/驳回、
任务依赖图、任务来源与产物、决策记录、交付确认、导出，全部在这一个自包含页面里完成。
本目录放**可视化自己的东西**：契约夹具与验收说明，不重复实现业务状态。

## 一、本模块交付什么

| 交付物 | 路径 | 说明 |
| --- | --- | --- |
| 工作台页面 | `modules/orchestrator/web/index.html` | 单文件，无构建、无外部依赖；由 `agent.py` 直接托管在 `/` |
| 契约夹具 | `modules/visualization/progress_fixture.py` | 按 `contracts/v2` 返回**字段齐全**的 Progress，用于验证交付确认/决策/来源/产物 |
| 回归断言 | `tests/test_visualization.py` | 13 条：页面锚点 6 条 + 夹具过契约 7 条 |
| 验收截图 | `docs/screenshots/member5/*.png` | 建图、任务详情、交付确认、决策记录、计划入口五张 |

## 二、契约 v2 里分给成员 5 的五项，对应在哪

| 契约要求（`契约v2合并通知.md`） | 页面实现 | 截图 |
| --- | --- | --- |
| 计划号 / 规格版本徽标 | 顶栏 `#bPlan`（plan_id，缺失时显示“无计划号”）、`#bSpec`（`v{spec.version}`）、另有 `#bGraph`、`#bList` | 1、3、4 |
| 按 layers 并排依赖图 | `renderGraph()`：每个 layer 一列，列头写「第 N 层 · 并行 k」；`force` 实线、`data` 虚线、`resource` 点线（不阻塞） | 1、2 |
| 任务详情（交付物、验收项、来源、产物） | 右栏「任务详情」：交付物、验收项、`requirement_refs`/`design_refs`（来源）、`artifact_ids`（产物）、`depends_on` 与下游 | 2 |
| 决策记录 | 右栏「决策记录」：优先读 `Progress.decisions`；字段未透传时按 `human_gate_exited` 事件推导并**明确标注**，字段与 `decision.schema.json` 对齐 | 4 |
| 交付确认按钮 | 左侧「③ 交付确认」：`status=delivery_pending` 时列交付清单（验收结论、产物编号）并给「确认交付 / 退回」；退回必须填理由 | 3 |

另外，模块说明里要求的四项也在：

- **运行时间线**：右栏「运行时间线」按阶段分组（建图 / 规格 / 计划 / 执行 / 人工介入 / 变更与修订 / 交付），
  每条带角色（系统 / AI / 人·xxx / 发生时间）与实质内容，可筛「全部 / 关键 / 人工 / 变更与修订 / 失败」；
  顶部「修订链」把一次评审意见（`change_submitted`）与它的重排、恢复任务串起来，并给出**可点击的受影响任务**。
- **暂停时显示原因与可执行操作**：`waiting_on` 非空时左侧「② 待人工处理」显示门类型、对象、进入时间、
  已等待时长、原因解释，以及放行 / 跳过 / 驳回三个按钮；`delivery_confirm` 门改为「确认交付 / 退回」。
- **刷新后恢复当前记录**：页面启动时 `since=0` 全量拉一次事件与快照，选中任务、当前页签、筛选项存在
  localStorage，刷新后原样恢复（不依赖前端缓存业务数据）。
- **导出**：`导出计划`（JSON，含 tasks/edges/layers/decisions/delivery）与 `导出 Markdown`（可直接进交付物）。

## 三、状态与数据口径（不做前端推断）

- 唯一数据源是 `GET /api/progress`：快照字段 + `edges` + （带 `since/limit` 时）`event_log`。
- 页面每 1.5 秒轮询增量，`next_since` 作游标；刷新后从 0 重拉，保证事件不丢不重。
- 所有人工操作都走 `POST /api/events`，页面不在本地先改状态（无乐观更新）。
- `layers` 缺失或与任务数不一致时，前端按 `edges` 求最长路径兜底（与后端 `topo_layers` 同口径，
  `resource` 边不参与分层），仅用于显示，不回写后端。

## 四、现在能演什么、还等谁

后端尚未透传的字段（见 `contracts/README.md` 的「待实现」表）已在页面上逐处标注，**不会静默显示错**：

| 字段 | 现状 | 页面行为 | 等谁 |
| --- | --- | --- | --- |
| `Progress.decisions` | 未透传 | 用 `human_gate_exited` 事件推导，顶部黄色提示「等成员4 透传」 | 成员 4 |
| `Progress.delivery` | 未实现 | `delivery_pending` 时提示“交付记录未返回”，按钮仍可用 | 成员 1/4 |
| `TaskState.requirement_refs/design_refs` | 未透传 | 显示“未提供”并提示由 `plan_adapter` 透传 | 成员 4 |
| `TaskState.artifact_ids` | 未实现 | 显示“尚未登记产物” | 成员 1/4 |
| `delivery_pending` 状态 | 未实现（现在全终态直接 `completed`） | 完成态显示“本次未生成交付记录” | 成员 1/4 |

字段一通，页面立即按契约渲染，**不需要改前端**（`tests/test_visualization.py` 的夹具就是用齐全字段跑的）。

## 五、怎么跑

```bash
# 1) 真实链路（mock 模型，不烧 token）
python modules/orchestrator/python/agent.py serve --port 8000 --out output/demo --mock
#    浏览器打开 http://127.0.0.1:8000/
#    左侧「示例」按钮填入 4 个任务，点「开始编排」

# 2) 可视化契约夹具（字段齐全的场景：交付确认 + 决策 + 来源 + 产物）
python modules/visualization/progress_fixture.py --port 8012
#    浏览器打开 http://127.0.0.1:8012/
#    点「确认交付」→ 实例 completed，决策记录多一条 target_type=delivery

# 3) 回归
python -m unittest tests.test_visualization -v
```

## 六、验收对照（模块验收：从网页完成一次全流程，能定位意见→修订与任务来源，刷新后恢复）

| 验收动作 | 操作 | 观察点 |
| --- | --- | --- |
| 全流程 | 示例 / 粘贴 spec / 粘贴 plan → 开始编排 → 放行人工门 → AI 回报 → 交付确认 | 状态栏 `created → running → waiting_human → delivery_pending → completed` |
| 意见 → 修订 | 右栏「运行时间线」顶部「修订链」 | 一条 `CR-xxx`：意见原文 → 修订结果（重排/恢复任务）→ 受影响任务（可点） |
| 任务来源 | 点任一节点 → 「任务详情」 | 交付物、验收项、需求条款 R-xx、设计模块 M-xx、产物 ART-xx、前置/下游 |
| 刷新恢复 | F5 | 事件数、选中任务、当前页签、筛选项全部还原 |

## 七、已知限制

1. 退回「规格 / 计划」的入口由成员 1 定义（契约原文：退回入口由成员1 定），页面暂未自造接口；
   定完后照 `humanOp()` 接两个按钮即可。规格阶段的评审详情在需求模块自带页面，不在此重复实现。
2. 页面是纯静态单文件，没有前端框架；若要接多实例或权限，再拆组件（当前规模不需要）。
3. 时间线一次最多渲染最近 220 条事件，超出部分在「事件流」里仍可看全（避免 DOM 过大）。
4. 节点标题截断 14 字，全文在鼠标悬停的 `title` 与右侧详情里。
5. 深链 `/#task=T4&tab=detail` 可复现某次评审现场，方便截图与答辩跳转。
