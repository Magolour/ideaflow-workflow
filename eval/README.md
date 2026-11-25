# 评测（对比多 Agent 流程与单 Agent 基线）

## 用途

回答一个问题：**多角色讨论出来的需求，是否比让模型一次写完更全、更少漏项、依赖更干净。**

## 运行

```bash
# 全部案例，离线（Mock），只验证流程与指标计算
python eval/run_eval.py

# 指定案例
python eval/run_eval.py --case device-booking

# 单 Agent 基线走真实模型（需要 AI_API_KEY）
python eval/run_eval.py --real --mode baseline
```

输出 `eval/report.json` 与 `eval/report.md`。

报告头部包含模型、提交号（含是否有未提交改动）、生成时间与判定规则；
单条案例失败不会中断整份报告，失败原因写进该条结果的 `errors`。

## 两种被测对象

| 模式 | 说明 |
| --- | --- |
| `multi` | 需求模块跑完整流程：快照 → A/B 草案 → 评审 → 规格 → 自审查，再经 `plan_adapter` 出计划 |
| `baseline` | 单次模型调用直接产出需求清单，再一比一建任务 |

## 指标

| 指标 | 口径 |
| --- | --- |
| 需求覆盖率 | `cases/*.json` 里的必含项按关键词命中比例 |
| 严重遗漏 | 必含项中 `severity: severe` 且未命中 |
| 无效依赖 | `depends_on` 指向不存在的任务、自依赖、环 |
| 人工介入 | 快照里的待澄清项 + 升级产生的人工问题 |
| 耗时 | 单次运行墙钟时间 |
| tokens | 仅真实基线返回；Mock 与多 Agent 路径暂记 `-` |

## 案例

| 案例 | 来源 | 说明 |
| --- | --- | --- |
| `campus-books` | `modules/requirements/cases/campus-books.md` | 校园二手书，含范围约束与待澄清项 |
| `device-booking` | `modules/requirements/cases/device-booking.md` | 实验室设备预约，第二套示例产品 |

必含项写在 `eval/cases/<name>.json`，关键词需人工维护；改了案例文本要同步改关键词。

## 重要说明

**Mock 模式下多 Agent 覆盖率 100% 是假象**：需求模块当前用模板把输入逐条转成条目，
等于"抄输入"，自然全中。这张表只证明指标链路通了，不构成对多 Agent 的证明。
真实结论要等 `agents.py` 接上真实模型后，用 `--real` 跑基线、多 Agent 同批对比得出。

## 真实调用冒烟（需求模块）

```bash
python eval/real_smoke.py                    # 跑案例、校验产物契约
python eval/real_smoke.py --case device-booking
python eval/real_smoke.py --require-real     # 仍是 Mock Agent 时返回 2，用于验收门禁
```

检查三件事：快照与规格是否过 `snapshot`/`spec` 契约；`spec.modules` 的
`requirement_ids` 是否都能在快照里找到；`spec.snapshot_ref` 是否与快照编号一致。

同时报告 `ideaflow/agents.py` 里有没有 `MockAgent` 之外的真实实现。
在接入真实模型之前，脚本会明确提示"本次产物来自 MockAgent"，不会把模板结果
当成真实调用结论。

## 查看一次需求运行（调提示词用）

```bash
python eval/show_run.py modules/requirements/runs --baseline 旧运行目录
```

打印快照条目数、功能清单、模块、待确认项、模型调用次数与 token；
`--baseline` 会对比两次运行的功能/模块数量与新增功能，适合反复调提示词。
同一输出目录多次运行时自动取最新一次。

## 检查规格质量（真实产物）

```bash
python eval/check_spec_quality.py modules/requirements/output/real-check2
```

检查六项：磁盘 spec 契约、功能到需求映射、审计覆盖率分母、验收条件、
估时、未决项噪声。出现 FAIL 表示本周"可追溯/自审查"的口径不成立。
