# IdeaFlow 公共契约 v2

- 状态：生效（v2.0）。字段变更需先改 schema、样例与断言，再改代码。
- 原则：贴当前代码，先能产出、能消费；新字段先可选，适配后再转必填。

## 文件与映射

| 文件 | 对应代码 | 说明 |
| --- | --- | --- |
| `idea.schema.json` | CLI/Web 输入 | 原始想法与约束 |
| `snapshot.schema.json` | `modules/requirements` 的 `snapshot.v1.json` | 需求快照，即需求规格 |
| `draft.schema.json` | `drafts/DRAFT-A/B.json` | A/B 候选草案 |
| `spec.schema.json` | `spec.json` / `spec.v1.json` | 设计规格（当前实际字段） |
| `plan.schema.json` | `python/plan_adapter.py` 生成，`agent.py` 消费 | 任务 6 字段必填，R/M/估时/依赖可选 |
| `event.schema.json` | `agent.py` 的 `emit()` | 当前事件字段与动作 |
| `decision.schema.json` | `agent.py` 的 `_record_decision()` | 写入 `decisions.jsonl`，目标类型含 `task/delivery` |
| `artifact.schema.json` | 执行侧产出 | 任务产物：文件、结构化记录或外链 |
| `delivery.schema.json` | 全部任务终态后生成 | 交付记录：每任务的产物与验收结论，人工确认后实例完成 |
| `enums.schema.json` | 全组 | 状态、动作、角色、优先级 |

## 已知差异（必须先对齐）

1. `spec.json` 的 `spec_id/snapshot_ref` 与早期草案的 `design_id/req_ref` 不同，本版以代码为准；
2. 编排器任务只有 `task_id/name/deliverable/acceptance_items/suggested_role/priority` 必填；
   `requirement_refs/design_refs/estimate_days/depends_on/write_paths/review_required` 为可选；
3. 事件 `actor` 是字符串，`object_type` 含 `task/graph/instance`，本版按代码写；
4. 事件没有 `schema_version`，本版设为可选；
5. `decisions.jsonl` 已由 `_human_op` 产出，字段见 `decision.schema.json`；
6. `requirement_spec` 不单独存在，用 `snapshot` 代替；
7. `plan` 的 `depends_on` 必须是任务号（T1），规格里的模块号（M-01）由 `plan_adapter.py` 转换；
8. 时间戳统一为带偏移的 ISO 8601（如 `2026-11-20T11:00:00+08:00`）；
9. 枚举以代码为准：快照条目类型为 `requirement/constraint/open`，规格状态含 `awaiting_human`；
10. `artifact`、`delivery` 已纳入本版，字段按"最小闭环"定义，执行侧实现后可再收紧；
11. `config` 未纳入本版，当前用 `--mock` 参数与 `.env` 代替；
12. 交付记录的 `plan_id` 可选：任务清单入口（夹具）没有计划号时省略；
13. `Progress.spec_version` 取值约定为 `v{spec.version}`（如 v1），跨模块对接口径；
14. `snapshot` 条目来源新增可选 `actor` / `round`，用于追溯讨论提出者与轮次；
    讨论补充条目上限为原文条目 3 倍（must 优先保留），防止真实模型把快照撑爆。

## 校验

```bash
python -m unittest discover -s tests -v
```

`tests/test_contracts.py` 校验 schema 与正常/异常样例；
`tests/test_decision.py` 校验人工决策记录；
`tests/test_integration.py` 校验 spec → plan → 编排器；
`tests/test_generated_artifacts.py` 用需求模块的离线产物校验 snapshot/spec 契约；
`tests/test_api_gaps.py` 校验 openapi 结构，并挂着九条待实现的接口断言（`skip`）；
`tests/test_scheduling.py` 挂着两条 resource 边不阻塞的调度断言（`skip`）；
`tests/test_eval_metrics.py` 校验评测指标计算；
`tests/test_failure.py` 覆盖重试转人工、重复启动、变更包非法（重复编号/成环/悬空边）；
`tests/test_graph.py` 覆盖拓扑分层、环检测与依赖边校验。

## 待实现（断言已就位，实现后去掉 skip）

| 缺口 | 契约位置 | 实现方 |
| --- | --- | --- |
| 想法入口经需求模块生成规格后再分解 | `openapi.yaml` 的 `StartRequest` | 流程集成（成员 1） |
| `/api/start` 接受 `spec.json` 并调用 `plan_adapter.py` | 同上 | 编排器（成员 4） |
| `/api/progress` 返回 `decisions` | `openapi.yaml` 的 `Progress.decisions` | 编排器（成员 4） |
| 任务完成登记产物 | `artifact.schema.json` | 执行侧（成员 1/4） |
| 全部终态后进入 `delivery_pending` 并生成交付记录 | `delivery.schema.json` | 执行侧（成员 1/4） |
| 交付确认后实例 `completed` | `Progress.delivery` | 执行侧（成员 1/4） |
| 计划来源（`requirement_refs`/`design_refs`）透传到进度 | `openapi.yaml` 的 `TaskState` | 编排器（成员 4） |
| `resource` 边不阻塞调度与分层 | `openapi.yaml` 的 `Edge.type` | 编排器（成员 4） |
| 任务失败开门后实例状态：应保持 `waiting_human` 还是转 `blocked`（待团队定口径） | `openapi.yaml` 的 `Progress.status` | 编排器（成员 4） |
| plan 自带 `depends_on` 时直接采用，不再调用模型分析依赖 | `plan.schema.json` 的 `depends_on` | 编排器（成员 4） |
| 一句话到交付的完整链路（桥 + spec 入口 + 执行闭环） | `openapi.yaml` 的 `/api/start` | 成员 1/4 |
| 讨论阶段接入规格（`discuss=True` 时先跑六角色讨论） | `specflow.py` 的 `run(discuss=...)` | 需求模块（成员 2） |

共 13 条：`tests/test_api_gaps.py` 9 条、`tests/test_scheduling.py` 2 条、`tests/test_failure.py` 1 条、`modules/requirements/tests/test_specflow.py` 1 条。
退回规格/计划暂不写断言：入口与状态转移未定义，等成员 1 定完再补。

## 入口约定

`/api/start` 保留四个入口，都算正式支持：

| 入口 | 请求体特征 | 用途 |
| --- | --- | --- |
| 想法 | `requirement` | 全流程入口，必须经需求模块生成规格后再分解，**不允许绕开规格** |
| 任务清单 | `tasks` | 夹具、前端联调、回归测试，**长期保留** |
| 计划 | `plan_id` + `tasks` | 编排器直接执行既定计划 |
| 设计规格 | `modules` | 课堂作业 2 的规定入口，由 `plan_adapter.py` 转计划 |

任务清单入口不因规格入口上线而删除：它不依赖上游模型，是前端和测试唯一能稳定复现的输入。

只有一条主链路：想法 → 讨论 → 规格 → 计划 → 编排。编排器不再接受"一句话直接拆任务"，
旧的 `pm_breakdown()` 捷径废弃；规格入口与想法入口最终都落在同一份 spec 上。

作业 2 的演示走**网页粘贴 spec**。页面 `startRun()` 已会把以 `{` 开头的输入按 JSON 解析后直发 `/api/start`，因此前端无需改造；缺的只是后端识别 `modules` 字段（当前会落到 `normalize_tasks` 报「任务清单为空」）。

## 变更规则

- 字段变更先改 schema、样例、测试，再改代码；
- 新字段先可选，消费者接入后再转必填；
- 不允许在代码里先加字段、回头补契约。
