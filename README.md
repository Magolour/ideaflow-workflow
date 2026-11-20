# IdeaFlow 软件工程工作流

团队共用仓库：产品需求 → 设计规格 → PM 任务拆解 → 依赖编排 → 可视化。

## 模块与当前状态

| 路径 | 职责 | 状态 |
| --- | --- | --- |
| modules/requirements | 需求澄清、规格整理与输出 | 离线讨论闭环可跑；待换真实模型 |
| modules/orchestrator | 任务拆解、依赖分析、调度与事件 API | 原型可跑；接口缺口见下方清单 |
| modules/visualization | 独立可视化界面 | 未建；原型 UI 在 orchestrator/web |
| contracts/v2 | 公共契约（9 份 schema + openapi）与样例 | **已生效**，字段变更需先改 schema/样例/断言 |
| eval | 多 Agent 与单 Agent 的评测骨架 | 可跑通；真实对比待模型接入 |
| docs | 架构、协作和问题清单 | 已初始化 |

主链路（唯一一条）：一句话想法 → 讨论 → 设计规格 → 任务分解 → 依赖编排 → 执行 → 交付确认。
编排器的 `pm_breakdown`（一句话直接拆任务）已废弃。

需求模块已可按「一句想法 → 多角色讨论 → 11 章 Proposal」离线运行，
入口与边界见 modules/requirements/README.md。

## 启动现有原型（PowerShell）

```powershell
cd modules/orchestrator
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r python/requirements.txt
Copy-Item python/.env.example python/.env
.\.venv\Scripts\python.exe python/agent.py serve --mock --host 127.0.0.1 --port 18080 --out output/demo_mock
```

打开 http://127.0.0.1:18080/ 。通过服务访问网页，避免双击 HTML 连接到默认 8000 端口。
离线演示可粘贴 json/test_tasks.json；一句话在 mock 模式只生成一个占位任务。
真实模型：在本机 .env 填写自己的配置，去掉 --mock；不要提交密钥。
详细接口见模块 README，协作规则见 CONTRIBUTING.md。

## 当前要做的事

1. 验收清单：`contracts/README.md` 的「待实现」表，共 11 条，每条对应
   `tests/` 里一个 `@unittest.skip`。实现后删掉 skip，跑绿即算过。
2. 契约与样例：`contracts/v2/`、`contracts/examples/`；入口约定与已知差异见
   `contracts/README.md`。
3. 已知限制：`docs/KNOWN_ISSUES.md`。
4. 自测：

```bash
python -m unittest discover -s tests
cd modules/requirements && python -m unittest discover -s tests
python eval/run_eval.py
```

分支从最新 `main` 建，例如 `feat/orchestrator-spec-entry`；不要从旧分支拉，
`contracts/v2` 只存在于 `main`。

## 团队开始协作

仓库管理员在 GitHub Settings → Collaborators 邀请成员。成员接受邀请后克隆仓库，从 main 创建自己的功能分支，通过 Pull Request 合并。
具体负责人由团队认领 Issue，不预设姓名。接口变更先讨论 contracts/README.md。
