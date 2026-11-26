# IdeaFlow · 想法 → 设计提案

把一句不完整的想法，经过多角色讨论、结构化批判与方案整合，产出一份可确认、可追溯、
保留分歧记录的 Proposal。

本项目按《IdeaFlow 多智能体人机协同工作流设计方案 V2 修订版》（第七组）实现，
覆盖讨论闭环、规格链路与真实模型接入。所有能力默认离线可跑，不需要 API Key。

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

## 多 Agent 讨论：六个角色

`pipeline.py` 按「澄清 → 发散 → 批判 → 回应 → 整合 → 签署 → 收敛」七阶段推进。
六个讨论角色互不可见，编号与写入由代码控制：

| ID | 角色 | 关注点 |
| --- | --- | --- |
| `ba` | 需求分析 | 用户、场景、问题与功能需求，指出信息缺口 |
| `user` | 用户视角 | 用户画像、使用场景、痛点真实性、使用路径 |
| `tech` | 技术专家 | 技术可行性、依赖、实现难点与技术风险 |
| `innovation` | 创新顾问 | 替代方案、差异化能力、更小切口 |
| `risk` | 风险评估 | 成本、进度、安全、数据与实施风险 |
| `skeptic` | 质疑者 | 挑战关键假设、寻找逻辑跳跃与反例 |

另有 `synthesizer`（记录员/整合者）与 `host`（主持人），由代码调度，不参与并行发散。

## Web 工作台：输入 → 生成 → 下载

```bash
python -m ideaflow.server --port 8000
# 浏览器打开 http://127.0.0.1:8000/
```

左栏顶部可以切换两种输出：

| 模式 | 输入 | 结果 |
| --- | --- | --- |
| 设计方案 | 想法 + 约束，轮次与阈值在“高级设置” | 11 章提案 |
| 设计规格 | 想法，或直接选演示案例 | 需求快照 → A/B 草案 → 评审 → 规格 → 审查 → 修订 → 人工问题 |

生成任务跑在后台线程，页面轮询阶段日志，不会出现“点了没反应”；同一进程可连续
生成多次，产物分别落在 `runs/` 下，互不覆盖。

左栏可勾选「使用真实模型」：勾选后请求带 `live: true`，无需改启动参数；
服务端未配置 `AI_API_KEY` 会直接返回 `LLM_NOT_CONFIGURED`。真实模型下被跳过的角色
会以黄色警告条显示在结果顶部，并写入 `run.json` 的 `warnings`。

**设计方案**的结果页有四个视图：

| 视图 | 内容 |
| --- | --- |
| 设计文档 | 11 章正文的排版预览，表格、引用、行内代码按文档样式渲染 |
| 候选方案 | 每个方案的适用范围、周期、风险、取舍，以及六个角色的立场 |
| 异议记录 | 级别、状态、分歧类型、后果与回应，未解决的排在前面 |
| 待澄清 | 阻断与非阻断问题，以及当前的处理方式 |

**设计规格**的结果页有四个视图：

| 视图 | 内容 |
| --- | --- |
| 需求快照 | `R/C/Q` 三类条目，带类型、优先级与来源行号 |
| A/B 草案 | 两组独立方案、各自决策与评分，下方是取舍对照与评审分歧 |
| 设计规格 | 同版 Markdown 的排版预览，含需求追溯表、模块接口与验收 |
| 审查与修订 | 覆盖/约束/验收摘要、逐版问题清单、修订记录、人工问题与选项 |

下载与导出：

| 方式 | 说明 |
| --- | --- |
| 下载 .md | 设计方案的 Markdown；规格模式下为同版规格 Markdown |
| 下载 .html | 设计方案的单文件 HTML，双击可看、可打印 |
| 下载规格 .json | 规格模式下导出机读 JSON，与 Markdown 同版本、同哈希 |
| 打印 / PDF | 只打印正文，自动隐藏输入栏、指标卡与标签页 |

接口只有三个，便于替换前端或接编排器：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/generate` | 提交 `{flow, idea, constraints, …}`，`flow` 为 `proposal` 或 `spec`；规格模式可带 `case`；返回 `202 {job_id}` |
| GET | `/api/job?id=` | 轮询任务状态、阶段日志与最终结果 |
| GET | `/api/download?run_id=&format=md\|html` | 下载提案文档 |
| GET | `/api/download?run_id=&artifact=spec-md\|spec-json\|question` | 下载规格 Markdown／JSON／人工问题 |
| GET | `/api/case?name=` | 读取演示案例，供页面填入输入框 |

另有 `/api/health` 健康检查与 `/static/*` 静态资源。服务默认只监听 `127.0.0.1`，
需要局域网访问时显式加 `--host 0.0.0.0`。页面支持演示链接：

```text
/?idea=…&constraint=…&auto=1                              设计方案，直接出结果
/?mode=spec&case=campus-books-tight&auto=1                设计规格，含修订与人工问题
/?mode=spec&case=campus-books&auto=1                      设计规格，直接进入人工决策
/?mode=spec&idea=…&auto=1                                 自由输入，审查通过时为草稿
```

前端没有构建步骤：`http.server` + 手写 Markdown 渲染（`render_html.py`），
不需要 npm，也不需要打包。

## 规格流程：需求快照 → 设计规格

与「讨论提案」并行的第二条链路，按评审要求逐项实现：先把输入固化成快照，
再让两组独立出方案、逐份评审、整合成规格，最后做覆盖／冲突／验收自审查。

默认直接切原文；要体现多 Agent 协作，先跑讨论再增强快照：

```python
from ideaflow.agents import LLMAgent
from ideaflow.specflow import load_case, run

run(load_case("campus-books"), out_dir="runs/discuss",
    discuss=True, discuss_rounds=2, agent=LLMAgent())
```

| 映射 | 说明 |
| --- | --- |
| 原文 R/C | 保持逐字切分与来源行号 |
| 假设 | → `open`，前缀“推断假设：” |
| 未解决待澄清 / 阻断异议 | → `open`；阻断项 `priority=must` |
| 非目标 | → `constraint`（“不做 X”，不带讨论理由） |
| 讨论补充上限 | 原文条数的 2 倍；`must` 不参与截断 |
| 重复项 | 去空白与标点后比对，原文、假设、问题之间去重 |

讨论产物在 `target/discussion/`，模型调用记录合并到规格运行目录的 `ai_calls.jsonl`。

完整链路（紧凑版案例，约 1 秒跑完）：

```bash
python -m ideaflow.spec_cli snapshot --file cases/campus-books-tight.md -o runs/tight-demo --confirm 产品负责人
python -m ideaflow.spec_cli race --dir runs/tight-demo
python -m ideaflow.spec_cli review --dir runs/tight-demo
python -m ideaflow.spec_cli audit --dir runs/tight-demo      # 发现 2 条阻断
python -m ideaflow.spec_cli revise --dir runs/tight-demo     # 裁剪范围，解决期限冲突
python -m ideaflow.spec_cli escalate --dir runs/tight-demo   # 产出 1 个人工问题
```

最终产物在 `runs/tight-demo/`：`snapshot.v1.json`、`drafts/`、`citations.json`、
`review.json`、`spec.v2.json` / `spec.v2.md`、`audit.v1/v2.json`、`revisions.json`、
`human_question.json`、`manifest.json`。

> `audit`、`revise` 在仍有阻断问题时返回 1，这是刻意设计的流水线信号，不是崩溃。

### 功能归纳：从讨论结论到模块

讨论完成后先产出一份功能清单，规格不再直接把原文句子当模块：

```bash
python -c "from ideaflow.specflow import run; run('我要做微信', out_dir='runs/feat', discuss=True)"
```

产物 `features.json` 受 `contracts/v2/features.schema.json` 约束：3–12 条，名称 2–24 字，
每条至少一个真实来源（`CLM/OPT/R`），`inferred` 标明是否为推断。

映射规则：

| 规则 | 实现 |
| --- | --- |
| 功能 → 需求 | 按功能名/描述关键词匹配快照需求，覆盖多条 `R-xx` |
| 匹配不到 | 保留 `F-xx` 写入 `open_items`，不再拿第一条需求顶替 |
| 模块 | 一个功能一个模块，名称唯一，最多 12 个 |
| 验收 | 每个功能独立判据，检索、录入、审批等分别生成 |
| 估时 | 取草案工作包/决策的真实值；取不到写 `null`，并在 `open_items` 说明 |
| 依赖 | 按功能语义补最小依赖；推不出时留空并在 `open_items` 提示串行风险 |

`discuss=False` 时仍走原文需求逐条建模块的旧路径。

### 需求快照

```bash
python -m ideaflow.spec_cli snapshot --file cases/campus-books.md --confirm 产品负责人
```

| 能力 | 说明 |
| --- | --- |
| 三类条目 | 明确要求 `R-xx`、约束 `C-xx`、待澄清 `Q-xx`；默认按措辞判定，`需求：`／`约束：`／`待澄清：` 前缀优先 |
| 来源可追溯 | 每条带 `section / start_line / end_line / excerpt`，行号指向原文真实位置 |
| 版本 | 编辑即升版，旧版 `snapshot.v1.json` 保留；确认只改状态，不升版本 |
| 完整性 | 落盘内容带 `hash`，读回时校验；改任何字段即报 `HASH_MISMATCH` |
| 编号稳定 | 已删除的编号进入 `retired_ids`，不会被后来的条目复用 |

### 两组独立出方案

```bash
python -m ideaflow.spec_cli race --dir runs/spec-demo
```

同一份快照、两套目标函数，互不可见：

| 组 | 目标 | 典型案例结果 |
| --- | --- | --- |
| A 交付优先 | 最短路径跑通主流程，先验证需求是否真实 | 承诺 10.2 天，全部需求有决策，支付降级为线下 |
| B 体验优先 | 关键路径压到 3 步内，体验一次做到位 | 承诺 29.7 天，超出两周期限，且与「不做在线支付」冲突 |

- **隔离**：每组只拿到 `group_context()` 生成的独立副本，里面只有快照条目与本组目标；测试会断言上下文里没有另一组草案。
- **覆盖**：每条明确要求都必须给出本组选择，漏一条即 `DRAFT_COVERAGE`。
- **自报冲突**：草案自己记录 `constraints_checked`，与期限、禁止项冲突时 `complies=false`，不静默忽略。

### 交付物与来源引用

`race` 会把快照、两份草案、引用边和清单一起写进目录：

| 文件 | 内容 |
| --- | --- |
| `snapshot.v1.json` | 需求快照，含每条目的章节与行号 |
| `drafts/DRAFT-A.json` | A 组草案：决策、主张、假设、风险、估算、约束核对 |
| `drafts/DRAFT-B.json` | B 组草案，同上 |
| `citations.json` | 草案 → 快照条目的引用边，以及全部来源位置 |
| `manifest.json` | 文件清单与内容哈希，重复交付结果稳定 |

任取一条结论都能回查原文：

```bash
$ python -m ideaflow.spec_cli trace --dir runs/spec-demo --item R-03
R-03 · 明确要求 · 买家可以在线付款并线下自提
来源：校园二手书小程序 · 第 5 行 · 买家可以在线付款并线下自提。
  DRAFT-A A-DEC-03（决定）：首版不做站内支付，改为线下当面交易并记录成交状态
  DRAFT-B B-DEC-03（决定）：站内支付 + 担保交易，覆盖下单、付款、放款全流程
```

引用边会在交付前校验：指向快照外的条目报 `DANGLING_CITATION`，
某条需求没有被任一草案决策覆盖报 `CITATION_GAP`。

### 案例：两套方案的实际取舍

```bash
python -m ideaflow.spec_cli case
```

案例固定为校园二手书小程序，刻意包含一条与约束冲突的需求
（「在线付款」vs「首版不做在线支付」），用来证明两组分歧不是措辞差异：

```text
需求                  A 交付优先                          B 体验优先                          差额
R-03 买家可以在线付…  首版不做站内支付，改为…（1.0 天）   站内支付 + 担保交易，覆…（7.0 天）  +6.0 天

取舍 4 条：放弃需求换工期 1 条，多投入换体验 3 条
期限对照：A 在限期内 ｜ B 超出 14 天
```

- **关键取舍**：A 放弃 R-03 换取工期，B 保留需求但把整体拖到 29.7 天；
- **其余三条**是同一需求下的体验投入差异（搜索 +2.5 天、收藏 +4 天、发布 +2 天）；
- 取舍记录只写事实与代价，不替人做决定，判断留给评审与人工确认。

### 单组失败与单独重试

赛马不是事务：一组失败不会回滚另一组的结果。

```bash
# 第一轮：B 组失败，A 组结果保留
python -m ideaflow.spec_cli race --dir runs/retry-demo --fail-group B --no-retry

# 只重跑 B，A 不重跑
python -m ideaflow.spec_cli retry --dir runs/retry-demo
```

```text
  A 第 1 次：成功（hash 7051e9a6…）
  B 第 1 次：失败 SIMULATED_FAILURE B 组第 1 次尝试被模拟为失败
  ...
重试失败组：B（已成功的组不重跑）
  B 第 2 次：成功（hash addbdf24…）
  A 组未重跑：hash 7051e9a6…（与失败前一致）
```

- **失败隔离**：`run_race` 把异常收敛到组级，`A` 成功、`B` 失败时 `drafts/DRAFT-A.json` 照常交付；
- **尝试记录**：每次尝试的组、序号、耗时、错误码都写进 `race.json` 的 `attempts`；
- **单独重试**：`retry` 只挑 `failed` 的组，已成功组的产物哈希前后一致（测试断言）；
- **失败注入**：`--fail-group` 只用于演示与回归，真实运行不会注入。

### 逐份评审与规格整合

```bash
python -m ideaflow.spec_cli review --dir runs/spec-demo
```

```text
逐份评审（一次只给一份草案，互不参照）
  DRAFT-A 价值 4.0 ｜ 可行 5.0 ｜ 成本 4.0 → 加权 4.35
  DRAFT-B 价值 5.0 ｜ 可行 1.0 ｜ 成本 1.0 → 加权 2.6
结论：以 DRAFT-A 为基线
整合决策：
  DEC-02 [采纳] R-02 关键词 + 标签 + 价格排序 + 搜索建议 + 空结果推荐
       理由：采纳 DRAFT-B 的 B-DEC-02…；差异 +2.5 天，在剩余预算内
  DEC-03 [基线] R-03 首版不做站内支付… ⚠ 待人工
```

- **评审隔离**：`review_context` 只放快照、一份草案与评分口径，测试断言其中没有另一组的内容；
- **三维评分**：价值／可行／成本，权重 40/35/25，只用来暴露取舍，不直接选方案；
- **整合规则**：以加权高者为基线，只采纳「不违反约束且在剩余预算内」的差异项，本例采纳 B 的搜索、拒绝 B 的支付与收藏；
- **采纳理由**：每条决策带 `draft_ids / claim_ids / requirement_ids`，采纳项写明差异天数与预算余量；
- **规格结构**：4 个模块（每个都有调用方、入参、出参）、R→M 映射、验收项、非目标、待决项。

### 规格自审查

```bash
python -m ideaflow.spec_cli audit --dir runs/spec-demo
```

```text
自审查 SPEC-001 v1
覆盖：4/4 有决策 ｜ 4/4 有模块 ｜ 4/4 有验收
约束：3/3 已核对（期限 14 天 vs 承诺 13.1 天）
验收：4/4 可度量

结论：未通过 · 阻断 1 条
  AUD-01 [阻断] 需求被降级或未满足：R-03「买家可以在线付款并线下自提」…
       建议：确认是否放宽约束，或把该需求正式列为非目标
```

| 检查 | 判定规则 | 本例 |
| --- | --- | --- |
| 覆盖 | 每条 R 有决策、模块、验收；R→M 双向一致 | 4/4 通过 |
| 冲突 | 期限 < PERT 承诺、人力撑不住并行度、包含禁止项 | 0 冲突 |
| 验收 | 必须含阈值／数量／比例／明确判定方式 | 4/4 可度量 |

- 审查只看结构化字段，不解析措辞，结论可复现；
- 问题带类型、严重级、影响条目和修复建议，写入 `audit.v1.json`；
- 有阻断问题时命令返回 1，可直接用于流水线拦截。

### 同版导出：JSON + Markdown

```bash
python -m ideaflow.spec_cli export --dir runs/spec-demo
```

```text
导出 SPEC-001 v1
  spec.v1.json  （内容哈希 388ab8e7339f…）
  spec.v1.md  （文件哈希 27b16b25e609…）

同版校验：通过 · 版本 1 · 双向引用一致 · 内容哈希 388ab8e7339f…
```

| 文件 | 用途 |
| --- | --- |
| `spec.v{n}.json` | 机读纯规格：决策、模块、接口、映射、验收、待决项，不含导出元数据 |
| `spec.v{n}.md` | 人读：需求追溯表、设计决策、模块接口、验收、非目标、待决问题、估算 |
| `spec.bundle.json` | 导出包：纯规格 + `artifacts`（JSON / Markdown / 内容哈希） |
| `spec.json` / `spec.md` | 最新版别名，便于下游直接读取 |

- 同版判定：`spec_id + version + content_hash`，Markdown 头部与附录都写完整哈希；
- `verify_export` 检查 JSON 完整性、版本、双向引用与哈希是否一致；
- 任一文件被改都会校验失败（测试覆盖 JSON 篡改与 Markdown 篡改两种）。

### 修订与复核

紧凑版案例（`cases/campus-books-tight.md`，9 天期限）会让 v1 出现期限冲突，
用它演示一条完整的「发现问题 → 修订 → 复核」：

```bash
python -m ideaflow.spec_cli revise --dir runs/tight-demo
```

```text
发现问题（v1）：
  AUD-02 [阻断] 估算与期限冲突：承诺 10.2 天，超出期限 9 天
修订 REV-001：期限 9 天：裁剪 收藏夹 1.0→0.5 天、教材发布 2.0→1.0 天
  R-04 1.0 → 0.5 天 ｜ 只在列表页记录收藏，不做收藏夹页面
  R-01 2.0 → 1.0 天 ｜ 只保留书名、价格与联系方式三个字段，去掉图片与草稿
复核（v2）：仍有阻断 ｜ 已解决 1 条 ｜ 剩余 1 条
  AUD-01 [阻断] 需求被降级或未满足
新承诺 8.6 天
```

- **裁剪策略**：从价值最低的模块开始简化（收藏 → 发布），并改掉方案描述，不是把工期数字改小；
- **重算估算**：改完统一走 `effort_for` 重算 PERT，不允许手改总工期；
- **修订记录**：`REV-001` 写明目标问题、改前改后的版本／哈希／承诺、每条改动、复核的已解决与剩余项；
- **复核诚实**：期限冲突消失，但 R-03 的需求冲突仍在，状态仍是未通过，交给人工问题处理。

### 无法自动解决时的人工问题

```bash
python -m ideaflow.spec_cli escalate --dir runs/tight-demo
```

```text
需要人工决策 HUMAN-Q-01 · 首版是否必须支持「买家可以在线付款并线下自提」（R-03）？
背景：
  - R-03 明确要求在线付款，当前决策是「首版不做站内支付…」，实际上没有满足。
  - 约束 C-03「首版不做在线支付」与它互斥；已尝试自动裁剪范围，仍无法同时满足。
选项：
  A 保留需求，放宽约束 C-03（7.0 天）  代价：工期增加约 6.0 天，需为 C-03 申请例外
  B 列为下一版，首版按现有方案交付（1.0 天）  代价：首版无法演示，需要书面确认范围
  C 折中：保留入口但只记录意向（4.0 天）  代价：流程完整但不满足真实场景
影响：R-03 ｜ C-03 ｜ AUD-01
建议：B（仅建议，不代替决定）
```

- **只产出一个问题**：优先处理「需求被降级」这类价值冲突，其余阻断问题列在影响范围；
- **选项可执行**：每个选项带工期代价、后果和它能解决的问题编号；
- **状态可识别**：规格转为 `awaiting_human`；状态与升级信息不参与内容哈希，正文仍是同一版；
- **导出带警示**：Markdown 第 9 章「需人工决策」+ 文件头提示，JSON 带 `escalation` 字段。

实现进度：

| # | 能力 | 状态 |
| --- | --- | --- |
| 1 | 需求快照：编号、版本、三类条目、来源行号 | ✅ 已完成 |
| 2 | 两组 Agent 基于同一快照独立出方案 | ✅ 已完成 |
| 3 | 交付快照、A/B 草案及来源引用 | ✅ 已完成 |
| 4 | 案例展示两方案的实际取舍 | ✅ 已完成 |
| 5 | 一组失败后可单独重试 | ✅ 已完成 |
| 6 | 分别评审、记录分歧与采纳理由、整合成规格 | ✅ 已完成 |
| 7 | 规格自审查：覆盖、冲突、验收 | ✅ 已完成 |
| 8 | 导出同版规格 JSON 与 Markdown | ✅ 已完成 |
| 9 | 发现问题 → 修订 → 复核记录 | ✅ 已完成 |
| 10 | 无法自动解决时产出具体人工问题 | ✅ 已完成 |
| 5 | 一组失败后可单独重试 | 待做 |
| 6 | 分别评审、记录分歧与采纳理由、整合成规格 | 待做 |
| 7 | 规格自审查：覆盖、冲突、验收 | 待做 |
| 8 | 导出同版规格 JSON 与 Markdown | 待做 |
| 9 | 「发现问题 → 修订 → 复核」记录 | 待做 |
| 10 | 无法自动解决时产出具体人工问题 | 待做 |

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
python -m unittest discover -s tests -v     # 199 项测试
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
├── agents.py       # Agent 接口 + 离线 MockAgent + OpenAI 兼容 LLMAgent + OpenAI 兼容 LLMAgent
├── convergence.py  # 分歧分类、终止条件、关键意见、复核与伪共识
├── pipeline.py     # 七阶段编排与落盘
├── proposal.py     # 11 章 Proposal 渲染
├── estimate.py     # PERT 三点估算
├── render_html.py  # Markdown → HTML，预览与离线文档共用
├── server.py       # Web 工作台：生成任务、轮询接口、下载
├── storage.py      # 原子写、稳定序列化与内容哈希
├── snapshot.py     # 需求快照：三类条目、编号、版本、来源
├── constraints.py  # 期限、人力、禁止事项的确定性抽取
├── drafts.py       # A/B 两组草案：独立上下文、覆盖校验
├── artifacts.py    # 交付：草案落盘、引用边、清单与哈希
├── tradeoffs.py    # A/B 取舍记录：同一需求上的选择与代价
├── race.py         # 组级失败隔离、尝试记录与单独重试
├── reviews.py      # 逐份评审：三维评分、分歧与待决项
├── spec.py         # 设计规格：决策、模块、映射、验收、整合规则
├── audit.py        # 自审查：覆盖、约束冲突、验收口径
├── spec_doc.py     # 规格导出：Markdown 渲染与同版校验
├── revisions.py    # 修订：范围裁剪、改前改后与复核记录
├── escalate.py     # 升级：把冲突变成带选项的人工问题
├── specflow.py     # 规格流程编排：一次跑完七个阶段
├── demo.py         # 固定案例：快照 → 草案 → 取舍，一次跑完
├── spec_cli.py     # 规格流程命令行
├── web/            # 前端页面（原生 HTML/CSS/JS，无构建）
└── cli.py          # 命令行入口
tests/
├── test_artifacts.py
├── test_audit.py
├── test_constraints.py
├── test_drafts.py
├── test_escalate.py
├── test_export.py
├── test_llm_agent.py
├── test_llm_drafts.py
├── test_llm_isolation.py
├── test_pipeline.py
├── test_race.py
├── test_reviews.py
├── test_revisions.py
├── test_snapshot.py
├── test_specflow.py
├── test_tradeoffs.py
└── test_web.py
```

## 真实模型（可选）

默认仍是离线模板；加 `--live` 才调用模型。接口保持 `agent.respond(stage, role, context)`，
调用方不区分模板与模型。

```bash
export AI_BASE_URL="https://<your-endpoint>/v1"
export AI_API_KEY="..."
export AI_MODEL="..."

python -m ideaflow.cli "做一个校园二手书交易小程序" --live
python -m ideaflow.spec_cli case --live
```

千问（DashScope 的 OpenAI 兼容模式）示例：

```powershell
$env:AI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:AI_MODEL="qwen-plus"          # 或 qwen-max / qwen3-max
$env:AI_EXTRA_BODY='{"enable_thinking": false}'   # qwen3 系列建议关闭思考
```

注意：

- `AI_BASE_URL` 要带 `/v1`，代码会再拼 `/chat/completions`；
- 千问部分模型不支持 `response_format`，若返回 400，加 `AI_JSON_MODE=0`；
- qwen3 系列默认思考模式，`content` 可能为空，用 `AI_EXTRA_BODY` 关闭思考。

| 能力 | 说明 |
| --- | --- |
| 结构化重试 | 输出不是合法 JSON 或缺字段时，把校验错误回注给模型重试，仍失败则报 `LLM_BAD_OUTPUT` |
| 双阶段校验 | `synthesize` 与 `sign` 直接用现有契约校验，避免脏数据进入黑板 |
| 审计 | 每次调用写 `ai_calls.jsonl`：阶段、角色、模型、耗时、token、状态 |
| 失败语义 | 缺配置报 `LLM_NOT_CONFIGURED`；网络/超时重试后报 `LLM_UNAVAILABLE` |

真实调用冒烟与评测：

```bash
python eval/real_smoke.py --case device-booking --require-real
python eval/run_eval.py --case device-booking --real
```

## 边界与后续

默认路径是**离线模拟**：内容由模板生成，流程、校验、编号、状态机、收敛判定都是真实逻辑。
这不是“假装有模型”，而是让流程在没有 Key 的环境里也能演示，并作为真实模型的回归基线。

| 部分 | 内容 | 状态 |
| --- | --- | --- |
| 一 | 讨论闭环：澄清、发散、批判、整合、签署、Proposal | ✅ 已完成 |
| 二 | 收敛引擎：分歧分类、终止条件、整合复核、伪共识检测 | ✅ 已完成 |
| 三 | 盲评与加权评分、阻断项返修、关键路径与里程碑 | 待做 |
| 四 | 真实模型接入（OpenAI 兼容）、结构校验回注、ai_call 审计 | ✅ 已完成（需配置 Key） |
| 五 | Web 工作台：输入、生成进度、文档预览、下载与打印 | ✅ 基础版完成（人工确认节点待做） |

暂不做：数据库、多租户、真实代码生成与部署。契约变更须升版本并同步文档与样例。
