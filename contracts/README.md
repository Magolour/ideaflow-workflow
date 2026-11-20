# IdeaFlow 公共契约 v2（草案）

- 状态：草案，待组长/字段负责人确认。
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
| `decision.schema.json` | `agent.py` 的 `_record_decision()` | 写入 `decisions.jsonl`，目标类型含 `task` |
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
10. `config`、`artifact`、`delivery` 未纳入本版。

## 校验

```bash
python -m unittest discover -s tests -v
```

`tests/test_contracts.py` 校验 schema 与正常/异常样例；
`tests/test_decision.py` 校验人工决策记录；
`tests/test_integration.py` 校验 spec → plan → 编排器；
`tests/test_generated_artifacts.py` 用需求模块的离线产物校验 snapshot/spec 契约；
`tests/test_api_gaps.py` 校验 openapi 结构，并挂着两条待实现的接口断言（`skip`）。

## 待实现（断言已就位，实现后去掉 skip）

| 缺口 | 契约位置 | 实现方 |
| --- | --- | --- |
| `/api/start` 接受 `spec.json` 并调用 `plan_adapter.py` | `openapi.yaml` 的 `StartRequest` | 编排器（成员 4） |
| `/api/progress` 返回 `decisions` | `openapi.yaml` 的 `Progress.decisions` | 编排器（成员 4） |

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
