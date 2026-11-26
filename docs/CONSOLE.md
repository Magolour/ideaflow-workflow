# 需求控制台：运行历史与追溯视图

控制台是需求侧的可视化入口，随 `ideaflow.server` 一起托管，和「生成工作台」共用同一批运行目录。

## 启动

```powershell
cd modules\requirements
python -m ideaflow.server --port 8000 --out runs
```

浏览器打开：

```text
http://127.0.0.1:8000/console
```

`--out` 指向运行产物根目录；控制台会扫描该目录下所有运行。

## 页面结构

| 页签 | 内容 | 数据来源 |
| --- | --- | --- |
| 概览 | 共识/规格状态、主张/异议/功能/模块/待办计数、缺口提示 | `blackboard.json`、`spec.json` |
| 讨论 | 六角色主张、异议挂在被批主张下、回应与立场 | `blackboard.json` |
| 追溯 | CLM → F → M 的 DAG，点击节点看来源与估时 | 三者关联 |
| 规格 | 模块、需求、估时、依赖、验收、待确认 | `spec.json` |
| 时间线 | 按 `seq` 展示事件 | `events.jsonl` |
| 待办 | 阻断问题、未映射功能、缺估算集中列出 | 以上聚合 |

左侧栏列出运行记录，显示想法摘要、流程类型、状态、功能/模块数；点击即加载。
底部「新建运行」支持输入想法、选择「规格」或「提案」、勾选真实模型，提交后轮询任务日志并自动打开最新运行。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/runs` | 运行列表：id、流程、状态、功能数、模块数、更新时间 |
| GET | `/api/run?id=<run>` | 单次运行聚合：board、features、spec、events、flow、snapshot、audit |
| GET | `/api/health` | 服务状态与运行目录 |
| POST | `/api/generate` | 新建讨论/规格任务，返回 `job_id` |
| GET | `/api/job?id=<job>` | 任务状态、阶段日志、错误、结果 |
| POST | `/api/spec/answer` | 回答规格升级问题 |

控制台按 1.2 秒轮询 `/api/job`；运行完成后刷新侧栏并加载最新运行。
单次运行视图不做前端推断，缺失字段显示「—」或「未提供」。

## 目录约定

```text
runs/<run>/
  spec.json               规格
  features.json           功能清单
  snapshot.json           快照
  discussion/<run>/       讨论
    blackboard.json
    events.jsonl
  flow.json               运行摘要
```

讨论目录可能不存在（例如 `discuss=False` 的规格），此时讨论页为空，其余页仍可用。

## 与 HexaGraph 控制台的差异

| 项 | HexaGraph | 本控制台 |
| --- | --- | --- |
| 前端 | React + Vite + AntD | 单文件 HTML，无构建 |
| 图 | React Flow + dagre | 手写 SVG 分层 |
| 实时 | SSE + 历史补偿 | 1.2 秒轮询任务日志 |
| 范围 | 项目、预算、变更、接管 | 需求讨论、追溯、规格、待办 |

保留单文件部署是为了答辩现场可直接运行；数据协议与视图划分参考了 HexaGraph。
