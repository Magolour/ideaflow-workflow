# IdeaFlow · 想法 → 设计提案

把一句不完整的想法，经过多角色讨论、结构化批判与方案整合，产出一份可确认、可追溯、
保留分歧记录的 Proposal。

本项目按《IdeaFlow 多智能体人机协同工作流设计方案 V2 修订版》（第七组）实现，
**当前完成第一部分：讨论闭环**。所有能力默认离线可跑，不需要 API Key。

## 现在能做什么

输入一句想法，输出一份 11 章提案，同时落盘黑板快照与事件流：

```bash
python -m ideaflow.cli "做一个校园二手书交易小程序，学生可以发布、搜索教材并线下交易" \
  -c "两周内做出可演示版本" -c "只有两名开发"
```

一次真实运行的统计：12 条主张、6 条假设、7 条异议（阻断已回应）、9 个未决问题；
讨论 1 轮后终止于「事实缺口」，共识判定为「待验证」，并在第 11 章列出待调研事项。

产物在 `runs/<时间>-<想法摘要>/`：

| 文件 | 内容 |
| --- | --- |
| `proposal.md` | 11 章提案：背景、用户、价值、范围、功能、技术、运营、指标、风险、估算、未决问题 |
| `blackboard.json` | 黑板完整快照：论点、假设、异议、方案、立场、决策、版本 |
| `events.jsonl` | 追加式事件流，`seq` 连续递增，可增量回放 |
| `run.json` | 本次运行摘要：计数、共识状态、路径 |

## Web 工作台：输入 → 生成 → 下载

```bash
python -m ideaflow.server --port 8000
# 浏览器打开 http://127.0.0.1:8000/
```

左栏填写想法与已知约束（轮次与阈值放在“高级设置”），右栏展示结果。生成任务跑在
后台线程，页面轮询阶段日志，所以不会出现“点了没反应”；同一进程可连续生成多次，
产物分别落在 `runs/` 下，互不覆盖。

结果页有四个视图：

| 视图 | 内容 |
| --- | --- |
| 设计文档 | 11 章正文的排版预览，表格、引用、行内代码按文档样式渲染 |
| 候选方案 | 每个方案的适用范围、周期、风险、取舍，以及六个角色的立场 |
| 异议记录 | 级别、状态、分歧类型、后果与回应，未解决的排在前面 |
| 待澄清 | 阻断与非阻断问题，以及当前的处理方式 |

下载与导出：

| 方式 | 说明 |
| --- | --- |
| 下载 .md | 原始 Markdown，便于继续编辑、入库或提交评审 |
| 下载 .html | 单文件、样式内联，双击即可打开，含自动生成的目录 |
| 打印 / PDF | 只打印正文，自动隐藏输入栏、指标卡与标签页 |

接口只有三个，便于替换前端或接编排器：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/generate` | 提交 `{idea, constraints, max_rounds, threshold, answers}`，返回 `202 {job_id}` |
| GET | `/api/job?id=` | 轮询任务状态、阶段日志与最终结果 |
| GET | `/api/download?run_id=&format=md\|html` | 下载文档；`run_id` 必须来自本进程 |

另有 `/api/health` 健康检查与 `/static/*` 静态资源。服务默认只监听 `127.0.0.1`，
需要局域网访问时显式加 `--host 0.0.0.0`。页面支持
`/?idea=…&constraint=…&auto=1` 预填并直接生成，方便演示和分享。

前端没有构建步骤：`http.server` + 手写 Markdown 渲染（`render_html.py`），
不需要 npm，也不需要打包。

## 已实现的设计要点

| 文档要求 | 实现位置 |
| --- | --- |
| 六角色独立发散，互不可见 | `pipeline.py` 每次传入全新上下文；`tests` 断言上下文无他人论点 |
| 强制结构化表态，反对须附理由 | `contracts.validate_role_output` + `validate_stances` |
| 依据类型四分（事实／推演／价值／假设） | `contracts.EVIDENCE_TYPES`，写入每条论点 |
| 立场四态，未评审不等于支持 | `blackboard.record_stance`，有条件必须写条件 |
| 编号由代码分配，模型不参与 | `contracts.new_id`，`CLM/ASM/OBJ/OPT/DEC/Q` 六套编号 |
| 第 11 章不可省略 | `proposal.py` 固定输出未决问题、未采纳意见、立场变化 |
| 阻断异议不得被高分掩盖 | `blackboard.consensus_state`，有阻断项即 `no_consensus` |
| 三点估算与缓冲 | `estimate.pert`，E=(O+4M+P)/6，承诺取 E+1σ |
| 状态由系统管理，不靠聊天记录推断 | 阶段机 + 黑板 + 事件流三段分离 |
| 人工裁决可追溯 | `--resolve OBJ-006=说明`，记录裁决人与理由 |
| 分歧三分（事实／价值／权衡） | `convergence.classify_objection`，事实缺口转待调研事项 |
| 关键意见处理表 | `convergence.key_opinions`，采纳／拒绝／延期验证／待人工裁决 |
| 终止条件显式记录 | `convergence.evaluate_termination`，轮次上限、连续无新增、事实缺口、价值取舍 |
| 整合复核 | 原角色复核自己的主张，遗漏或歪曲按阻断项处理 |
| 伪共识检测 | 全票通过时强制质疑者给出反例，反例未处理不得判通过 |

## 与仓库内其他模块的关系

本模块负责整条流程的第一段：想法 → 讨论 → Proposal。产物交给后续阶段消费：

- `proposal.md` 第 4—5 章给出范围与功能清单，可作为任务拆解的输入；
- `blackboard.json` 的 `decisions`、`open_questions` 与 `objections` 保留待确认项，
  下游不得把未决问题当作已确认结论；
- 与 `modules/orchestrator` 的 `POST /api/start` 对接在后续部分完成，
  当前仍以规格文本作为交接边界。

## 快速开始

```bash
python -m unittest discover -s tests -v     # 30 项测试
python -m ideaflow.cli --help
python -m ideaflow.server --help
```

可选参数：

```bash
# 回答首轮澄清问题（未回答按“未知，按假设继续”处理）
python -m ideaflow.cli "想法" --answer "Q-001=由产品负责人验收"

# 由人裁决异议，观察共识状态变化
python -m ideaflow.cli "想法" --resolve "OBJ-006=已确认首版非目标清单"

# 调整讨论轮次上限与共识阈值
python -m ideaflow.cli "想法" --max-rounds 3 --threshold 0.6

# 机器可读摘要
python -m ideaflow.cli "想法" --json
```

## 目录

```text
ideaflow/
├── contracts.py    # 编号、依据类型、立场四态、结构校验
├── blackboard.py   # 黑板、事件流、共识基础判定
├── roles.py        # 8 张角色卡：关注点 / 必答问题 / 禁区
├── agents.py       # Agent 接口 + 离线 MockAgent
├── convergence.py  # 分歧分类、终止条件、关键意见、复核与伪共识
├── pipeline.py     # 七阶段编排与落盘
├── proposal.py     # 11 章 Proposal 渲染
├── estimate.py     # PERT 三点估算
├── render_html.py  # Markdown → HTML，预览与离线文档共用
├── server.py       # Web 工作台：生成任务、轮询接口、下载
├── web/            # 前端页面（原生 HTML/CSS/JS，无构建）
└── cli.py          # 命令行入口
tests/
├── test_pipeline.py
└── test_web.py
```

## 边界与后续

当前是**离线模拟**：内容由模板生成，流程、校验、编号、状态机、收敛判定都是真实逻辑。
这不是“假装有模型”，而是让流程在没有 Key 的环境里也能演示，并作为真实模型的回归基线。

| 部分 | 内容 | 状态 |
| --- | --- | --- |
| 一 | 讨论闭环：澄清、发散、批判、整合、签署、Proposal | ✅ 已完成 |
| 二 | 收敛引擎：分歧分类、终止条件、整合复核、伪共识检测 | ✅ 已完成 |
| 三 | 盲评与加权评分、阻断项返修、关键路径与里程碑 | 待做 |
| 四 | 真实模型接入（OpenAI 兼容）、结构校验回注、ai_call 审计 | 待做 |
| 五 | Web 工作台：输入、生成进度、文档预览、下载与打印 | ✅ 基础版完成（人工确认节点待做） |

暂不做：数据库、多租户、真实代码生成与部署。契约变更须升版本并同步文档与样例。
