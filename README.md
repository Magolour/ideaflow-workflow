# IdeaFlow 软件工程工作流

团队共用仓库：产品需求 → 设计规格 → PM 任务拆解 → 依赖编排 → 可视化。

## 模块与当前状态

| 路径 | 职责 | 状态 |
| --- | --- | --- |
| modules/requirements | 需求澄清、规格整理与输出 | 已接入第一部分（离线讨论闭环） |
| modules/orchestrator | PM 拆解、依赖分析、调度与事件 API | 已导入现有原型 |
| modules/visualization | 独立可视化界面 | 待接入；原型 UI 暂在 orchestrator/web |
| contracts | 模块接口约定与示例位置 | 初版约定待团队确认 |
| docs | 架构、协作和问题清单 | 已初始化 |

模块目录表示职责边界，目前仍是独立编排服务，没有完成全部模块集成。

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

## 团队开始协作

仓库管理员在 GitHub Settings → Collaborators 邀请成员。成员接受邀请后克隆仓库，从 main 创建自己的功能分支，通过 Pull Request 合并。
具体负责人由团队认领 Issue，不预设姓名。接口变更先讨论 contracts/README.md。
