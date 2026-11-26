# IdeaFlow 公共契约 v2

- 状态：生效（v2.0）。字段变更需先改 schema、样例与断言，再改代码。
- v2.1 增补（2026-11-26）：任务 `kind`、`evidence` / `summary` 两个 schema、交付三态 `unverified`、`Progress` 的 `mode/phase/plan_review/spec`，以及四个新接口。
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
| `features.schema.json` | 讨论收敛后归纳 | 功能清单（F-xx），模块划分的输入 |
| `evidence.schema.json` | 执行侧写入 `artifacts/ART-xxxx/evidence.json` | 任务执行证据：命令、退出码、输出摘要、耗时、环境、哈希 |
| `summary.schema.json` | 终态写 `summary.json`，`GET /api/summary` 读取 | 运行总结：周期、门禁等待、返工、失败、覆盖、未决 |
| `enums.schema.json` | 全组 | 状态、动作、角色、优先级、任务类型、运行模式 |

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
12. 交付记录的 `plan_id` 可选，兼容旧版任务清单入口的交付记录；
13. `Progress.spec_version` 取值约定为 `v{spec.version}`（如 v1），跨模块对接口径；
14. `snapshot` 条目来源新增可选 `actor` / `round`，用于追溯讨论提出者与轮次；
    讨论补充条目上限为原文条目 3 倍（must 优先保留），防止真实模型把快照撑爆；
15. 模块的 `estimate_days` 允许为 `null`，表示拿不到估算：须同时写入 `open_items`，
    下游按"缺失"处理（计划评审不报 `TASK_TOO_LARGE`），不得用均摊值冒充估算。
16. `TaskState.kind` 与 `plan.tasks[].kind` 取值 `code/test/review/doc`，缺省按 `code`；
    测试任务编号用 `T{n}T`、评审任务用 `T{n}R`，`plan` 的 `task_id` 模式已放宽为 `^T-?\d+[A-Za-z]?$`。
17. `delivery.items[].acceptance` 增加 `unverified`：没有证据不得判 `passed`；
    判定优先级为 failed → passed → unverified（见 `evidence.schema.json`）。
18. `Progress` 新增只读字段 `mode`、`phase`、`plan_review`、`spec`（`edges` 已实现，本版补文档）；
    新增接口 `PATCH /api/spec/item`、`POST /api/graph/edit`、`GET /api/replay`、`GET /api/summary`，
    错误体统一为 `{error, message, details}`，错误码大写加下划线。
19. `plan_review.issues` 的 `code` 目前是自由字符串，已知取值：`MODULE_WITHOUT_TASK`、`DEPENDENCY_INVALID`、`DEPENDENCY_CYCLE`、`OUT_OF_SPEC_REQUIREMENT`、`OUT_OF_SPEC_DESIGN_REF`、`OUT_OF_SPEC_INTERFACE`、`TASK_TOO_LARGE`、`MODULE_DAYS_EXCEEDED`；T-16 落地后新增 `TASK_SPLIT`（拆分发生时）与 `TEST_MISSING`（缺测试任务）。新增 code 先在这里登记。

## 校验

```bash
python -m unittest discover -s tests -v
```

`tests/test_contracts.py` 校验 schema 与正常/异常样例；
`tests/test_decision.py` 校验人工决策记录；
`tests/test_integration.py` 校验 spec → plan → 编排器；
`tests/test_generated_artifacts.py` 用需求模块的离线产物校验 snapshot/spec 契约；
`tests/test_api_gaps.py` 校验 OpenAPI 结构、启动接口、计划审批、定向退回和交付；
`tests/test_scheduling.py` 校验 resource 边不阻塞调度；
`tests/test_eval_metrics.py` 校验评测指标计算；
`tests/test_failure.py` 覆盖重试转人工、重复启动、变更包非法（重复编号/成环/悬空边）；
`tests/test_graph.py` 覆盖拓扑分层、环检测与依赖边校验；
`tests/test_feature_synthesis.py` 覆盖功能清单契约、命名、来源与模块对应。

## 编排回归范围

| 行为 | 契约位置 | 实现方 |
| --- | --- | --- |
| 任务完成登记产物 | `artifact.schema.json` | 执行侧（成员 1/4） |
| 全部终态后进入 `delivery_pending` 并生成交付记录 | `delivery.schema.json` | 执行侧（成员 1/4） |
| 交付确认后实例 `completed` | `Progress.delivery` | 执行侧（成员 1/4） |
| `resource` 边不阻塞调度与分层 | `openapi.yaml` 的 `Edge.type` | 编排器（成员 4） |
| 任务失败开门后实例保持 `waiting_human` | `openapi.yaml` 的 `Progress.status` | 编排器（成员 4） |
| 一句话到交付的完整链路（桥 + spec 入口 + 执行闭环） | `openapi.yaml` 的 `/api/start` | 成员 1/4 |
| 计划评审：模块覆盖、任务粒度、依赖合法性与成环 | 新增 `plan_review.review_plan(spec, plan)` | 编排器（成员 4） |

已解除挂起（断言现在生效，改坏了会红）：
想法入口经规格、`/api/start` 接受 `spec.json`、`/api/progress` 返回 `decisions`、
计划来源透传、计划自带 `depends_on` 时不再调模型、讨论阶段接入规格、
需求归纳（功能清单生成、命名、来源、模块与功能对应）。

## 计划审批与定向退回

- 四种入口生成或载入计划后统一进入 `status=waiting_human` 和 `waiting_on.gate_type=plan_approval`。任务清单入口也分配计划号，计划的 `spec_ref` 为 `null`。
- 页面用 `Progress.plan_id` 和 `plan_version` 展示当前待批计划，通过 `/api/events` 提交 `human_op`、`action=approve` 及这两个字段。兼容命令行的简写批准会在入队时绑定当前待批版本。批准写入 `target_type=plan` 的决策后开始派发。
- 退回使用 `action=return`，必须传当前计划引用、`return_to=plan|spec` 和非空 `reason`。退回计划沿用当前规格，计划版本递增。退回规格须提交同一 ID 的、已复审通过的下一版 `spec`，规格与计划版本均递增。旧版文件和退回原因保留，新计划重新等待批准。
- `Progress.can_return_spec` 告知页面当前是否有可退回的规格正文。计划批准、定向退回和交付确认的结果从 `decisions` 读取。

## 入口约定

`/api/start` 保留四个入口，都算正式支持：

| 入口 | 请求体特征 | 用途 |
| --- | --- | --- |
| 想法 | `requirement` | 全流程入口，必须经需求模块生成规格后再分解，**不允许绕开规格** |
| 任务清单 | `tasks` | 夹具、前端联调、回归测试，**长期保留** |
| 计划 | `plan_id` + `tasks` | 载入既定计划，批准后派发 |
| 设计规格 | `modules` | 课堂作业 2 的规定入口，由 `plan_adapter.py` 转计划 |

任务清单入口不因规格入口上线而删除：它不依赖上游模型，是前端和测试唯一能稳定复现的输入。

只有一条主链路：想法 → 讨论 → 规格 → 计划 → 编排。编排器不再接受"一句话直接拆任务"，
旧的 `pm_breakdown()` 捷径废弃；规格入口与想法入口最终都落在同一份 spec 上。

作业 2 的演示可在网页粘贴规格 JSON，启动后在计划审批面板批准当前版本。

## 规格审查门禁

想法和外部规格入口在拆计划前检查 `review`。它绑定规格 ID、版本、正文哈希与快照引用，且不参与规格正文哈希。`review_ref` 仍指向 A/B 草案评审。请只从可信的需求模块或规格提供方接收规格，哈希用于一致性校验。

| 规格审查结果 | API 行为 |
| --- | --- |
| `confirmed` / `approved`，审查无阻断 | 可拆计划，之后进入计划审批 |
| `awaiting_human` | 409 `SPEC_HUMAN_REQUIRED`，附问题和审查 |
| `blocked` 且无人工问题 | 422 `SPEC_BLOCKED_NO_QUESTION`，附审查 |
| 缺审查，或正文、版本、快照、审查信息不一致 | 409，并用错误码标明原因 |

规格入口返回 409 或 422 时不创建编排实例。需求模块保留运行产物，复审通过后提交新规格 JSON 重新启动。外部历史规格进入编排前需要获得当前版本的审查。想法入口兼容旧需求模块的产物格式，可从本次运行的 `audit.json` 读取审查结果。

需求工作台的 `POST /api/spec/answer` 接受 `run_id`、`spec_ref`（ID、版本、哈希）、`question_id`、`option`、`answer` 和 `actor`。成功后返回新版 `spec`、`audit` 和当前 `question`。工期冲突的 A 选项修改期限快照，B 选项应用现有裁剪规则。两种修订均重新审查，旧版和人工回答保留。旧引用或重复回答返回 409，需要人工修改或无法继续裁剪的选择返回 422。需求工作台提供可直接交给编排器的 `artifact=spec-input` 下载。

## 变更规则

- 字段变更先改 schema、样例、测试，再改代码；
- 新字段先可选，消费者接入后再转必填；
- 不允许在代码里先加字段、回头补契约。
