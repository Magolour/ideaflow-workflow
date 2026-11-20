# IdeaFlow 公共契约 v2（草案）

- 状态：草案，待组长/字段负责人确认；未提交。
- 原则：贴当前代码，先能产出、能消费；新字段先可选，适配后再转必填。

## 文件与映射

| 文件 | 对应代码 | 说明 |
| --- | --- | --- |
| `idea.schema.json` | CLI/Web 输入 | 原始想法与约束 |
| `snapshot.schema.json` | `modules/requirements` 的 `snapshot.v1.json` | 需求快照，即需求规格 |
| `draft.schema.json` | `drafts/DRAFT-A/B.json` | A/B 候选草案 |
| `spec.schema.json` | `spec.json` / `spec.v1.json` | 设计规格（当前实际字段） |
| `plan.schema.json` | 编排器任务清单（目标态） | 当前任务 6 字段必填，R/M/估时/依赖为可选，适配后收紧 |
| `event.schema.json` | `agent.py` 的 `emit()` | 当前事件字段与动作 |
| `decision.schema.json` | 暂无生产者 | 批准/退回记录，待接入 |
| `enums.schema.json` | 全组 | 状态、动作、角色、优先级 |

## 已知差异（必须先对齐）

1. `spec.json` 的 `spec_id/snapshot_ref` 与早期草案的 `design_id/req_ref` 不同，本版以代码为准；
2. 编排器任务只有 `task_id/name/deliverable/acceptance_items/suggested_role/priority`；
   `requirement_refs/design_refs/estimate_days/depends_on/write_paths/review_required` 暂为可选；
3. 事件 `actor` 是字符串，`object_type` 含 `task/graph/instance`，本版按代码写；
4. 事件没有 `schema_version`，本版设为可选；
5. `decision`、`plan` 还没有生产者，属于目标态；
6. `requirement_spec` 不单独存在，用 `snapshot` 代替；
7. `config`、`artifact`、`delivery` 未纳入本版。

## 变更规则

- 字段变更先改 schema、样例、测试，再改代码；
- 新字段先可选，消费者接入后再转必填；
- 不允许在代码里先加字段、回头补契约。
