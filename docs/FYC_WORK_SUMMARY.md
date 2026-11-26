# 个人工作说明：需求分析与规格链路

本文汇总本人在 IdeaFlow 项目中承担的全部任务，覆盖真实模型接入、讨论到规格的桥接、
功能归纳、规格数据质量、Web 工作台与评测。对应分支 `FYC`。

## 1. 负责范围

| 模块 | 工作内容 |
| --- | --- |
| `modules/requirements` | 六角色讨论、LLM 接入、快照、功能清单、设计规格、审查修订、导出 |
| `modules/requirements/ideaflow/web` | 真实模型开关、阶段日志、警告与错误提示 |
| `eval` | 真实冒烟、A/B 评测、运行查看器 |
| 文档 | 模块 README、数据质量说明、本说明 |

不改 `modules/orchestrator` 的执行逻辑，不改 `contracts/v2` 的既有字段定义。

## 2. 交付清单

| 能力 | 主要文件 | 结果 |
| --- | --- | --- |
| OpenAI 兼容 LLM Agent | `agents.py` | 支持 DashScope / 千问 MaaS / DeepSeek，可配 `base_url` |
| 输出容错与重试 | `agents.py` | 字符串数组、空数组、跨角色复核、非文本字段均能归一化或回注重试 |
| 角色级失败隔离 | `pipeline.py` | 单角色失败只记 `warnings`，不中止整条运行 |
| 讨论接入规格 | `specflow.py` | `discuss=True` 时先跑讨论，产物放 `target/discussion/` |
| 快照映射 | `snapshot.py` | 假设、未决问题、阻断异议、非目标、方案范围进快照 |
| 功能归纳 | `features.py`、`agents.py` | 3–12 条功能，Mock 与 LLM 双实现，写 `features.json` |
| 规格从功能生成 | `spec.py` | 一个功能一个模块，名称唯一，上限 12 |
| 数据质量修复 | `spec.py`、`revisions.py` | 功能映射、独立验收、`null` 估时、最小依赖 |
| 审查与导出 | `audit.py`、`spec_doc.py` | 覆盖跳过、空映射保护、同版导出 |
| Web 工作台 | `server.py`、`web/` | 真实模型开关、角色跳过警告、分类型错误提示 |
| 评测工具 | `eval/` | 真实冒烟、A/B 对比、`show_run` 查看器 |

## 3. 关键实现

### 3.1 真实模型接入

- `LLMAgent` 走 OpenAI 兼容 `/chat/completions`，模型、地址、Key 全部由环境变量配置。
- 每个阶段有独立形状提示与温度；校验失败把错误与目标形状回注，最多重试两次。
- `AI_JSON_MODE=0` 适配方不支持 `response_format` 的模型；`AI_EXTRA_BODY` 可关闭 qwen3 思考模式。
- 调用明细写 `ai_calls.jsonl`：阶段、角色、耗时、token、状态。

### 3.2 六角色讨论

讨论按七阶段推进：澄清、发散、批判、回应、整合、签署、收敛。

| 角色 | 关注点 |
| --- | --- |
| 需求分析 | 用户、场景、问题与功能需求 |
| 用户视角 | 画像、场景、痛点与使用路径 |
| 技术专家 | 可行性、依赖与实现风险 |
| 创新顾问 | 替代方案与差异化 |
| 风险评估 | 成本、进度、安全与实施风险 |
| 质疑者 | 挑战假设、寻找反例 |

`记录员/整合者` 与 `主持人` 由代码调度，不参与并行发散。

### 3.3 讨论接入规格

`specflow.run(..., discuss=True)`：

1. 先跑 `pipeline.run`，拿到黑板；
2. 用黑板增强快照；
3. 生成功能清单；
4. 进入 A/B 草案、评审、规格、审查。

默认 `discuss=False`，旧入口不受影响。

### 3.4 功能归纳

- `features.json` 受 `contracts/v2/features.schema.json` 约束：3–12 条、名称 2–24 字、来源真实。
- 功能按关键词匹配快照需求，避免全部落到 `R-01`；匹配不到保留 `F-xx` 并进 `open_items`。
- 规格模块名称等于功能名，同名自动加序号，最多 12 个。
- 每条功能生成独立验收判据，不再共用模板尾巴。
- 拿不到真实估时写 `null`，不再均摊成同一个假数，并在 `open_items` 说明。
- 按功能语义补最小依赖，避免计划图退化成串行链。

## 4. 验收数据

| 项目 | 结果 |
| --- | --- |
| 需求模块测试 | 199 passed |
| 根目录测试 | 113 项，1 项等待契约放开 `estimate_days: number|null` |
| 真实冒烟 `campus-books` | 48 次调用、快照 24 项、10 个模块、契约通过 |
| 一句话需求 `我要做微信` | 6 条功能、6 个模块 |
| 数据质量复现 | 映射覆盖 4 条真实需求；6 条验收互不重复；估时不全相同；依赖图有并行层 |
| A/B 评测 | 多 Agent 覆盖 100%，单 Agent 基线 62.5% |

## 5. 复现命令

```powershell
cd modules\requirements
python -m unittest discover -s tests -v
```

真实模型：

```powershell
$env:AI_BASE_URL="https://maas.qianwenaiapi.com/compatible-mode/v1"
$env:AI_API_KEY="<Key>"
$env:AI_MODEL="qwen-plus"
$env:AI_EXTRA_BODY='{"enable_thinking": false}'

modules\orchestrator\.venv\Scripts\python.exe eval\real_smoke.py --require-real
```

一句话需求与功能清单：

```powershell
cd modules\requirements
python -c "from ideaflow.specflow import run; from ideaflow.agents import MockAgent; run('我要做微信', out_dir='output\\feat', discuss=True, agent=MockAgent())"
python ..\..\eval\show_run.py output\feat
```

## 6. 已知边界

- `estimate_days: null` 需要测试侧把契约放宽为 `number|null`，代码侧已按约定输出。
- `/api/start` 直接接收一句话仍走编排器 `pm_breakdown`，需要成员4 接规格/计划桥。
- 前端规格页暂未暴露“先跑讨论”开关，讨论目前通过 CLI / `real_smoke` / 直接调用触发。

## 7. 关键提交

| 提交 | 内容 |
| --- | --- |
| `9f18dcb` | 功能归纳阶段与规格按功能建模块 |
| `646f63d` | 无需求映射功能补验收 |
| `ca00123` | 修订跳过无映射模块 |
| `ab9def5` | 无 R 引用时导出保护 |
| `519c7f2` | 功能映射、验收、估时、依赖四项数据质量修复 |
| `af880dd` | README 补六角色与功能归纳 |
