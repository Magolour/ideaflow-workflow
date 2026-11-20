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
9. `config`、`artifact`、`delivery` 未纳入本版。

## 校验

```bash
python -m unittest discover -s tests -v
```

`tests/test_contracts.py` 校验 schema 与正常/异常样例；
`tests/test_decision.py` 校验人工决策记录；
`tests/test_integration.py` 校验 spec → plan → 编排器。

## 变更规则

- 字段变更先改 schema、样例、测试，再改代码；
- 新字段先可选，消费者接入后再转必填；
- 不允许在代码里先加字段、回头补契约。
