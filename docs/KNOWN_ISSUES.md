# 当前限制与待办

> 说明：本文件最初记录首次导入时的状态。2026-11-26 复核后，已解决项移到文末，
> 未解决项保留在上表。作业 3 的能力清单与排期见 `docs/W11_OBSERVABILITY.md`。

## 仍未解决

| 限制 | 现状 | 归属 |
| --- | --- | --- |
| running 只是派发状态，未接入真实执行器 | 仍未接入；任务的"完成"由 AI 回报，交付验收由状态推导（`agent.py:901`），没有命令/退出码等证据 | 成员 4 |
| 重启不会恢复工作流；一次服务只接收一份初始输入 | 仍如此；`runtime_state.json` 只落快照，进程重启后需重新 `POST /api/start`，重复启动返回 409 | 成员 4 |
| 网页跳过提示与后端将 skipped 视为依赖满足的行为不一致 | 仍未对齐口径 | 成员 4/5 |
| 未验证真实模型的拆解质量和跨产品通用性 | 真实模型只跑过校园二手书一个想法；估时与验收文案的质量问题见 `docs/SPEC_QUALITY.md` | 全员 |

## 已解决（2026-11-26 复核）

| 原限制 | 现状 | 证据 |
| --- | --- | --- |
| 上下游模块尚未集成，需求和可视化模块为协作占位 | 已打通：想法 → 讨论 → 规格 → 计划 → 编排 → 交付，真实模型全程跑通 | `output/front-live/`；`docs/MEMBER_1_ACCEPTANCE.md` |
| resource 边参与阻塞调度 | 已修：`resource` 边不参与分层与调度 | `agent.py` 的 `planned_edges`/`topo_layers`；`tests/test_scheduling.py` |
| 原型 normalize_tasks 丢弃描述、估时、规格引用 | 已修：描述、`estimate_days`、`requirement_refs`、`design_refs`、`write_paths` 均保留并透传 | `entry.py` 的 `normalize_tasks`；`tests/test_api_gaps.py` 的来源断言 |
