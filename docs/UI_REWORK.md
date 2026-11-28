# IdeaFlow 工作台 UI/UX 重构说明

本文件记录 2026-11 这一轮 IdeaFlow 需求工作台的界面重构：分析方法、设计规范、
页面与组件结构、接口变化、测试结果和尚未完成的能力。

## 1. 现有架构分析

- 前端：`modules/requirements/ideaflow/web/` 下的原生 HTML / CSS / JS，无构建步骤；
  `index.html` 是主工作台，`console.html` 是运行历史与追溯控制台。
- 后端：`ideaflow/server.py`，Python 标准库 HTTP 服务，读取 `runs` 下的运行产物。
- 工作流：`pipeline.py`（讨论/提案）与 `specflow.py`（需求快照 → A/B 草案 → 评审 →
  规格 → 自审查 → 修订/人工问题 → 导出）。
- 数据：`/api/generate` 创建 Job；`/api/job` 轮询；`/api/job/events` SSE；
  `/api/runs`、`/api/run` 提供历史运行；`/api/download` 导出。
- 本轮保留全部现有 API 和业务语义，仅新增模型连接测试接口 `/api/llm/test`。

## 2. 设计规范

| 用途 | 值 |
| --- | --- |
| 页面背景 | `#F7F8FA` |
| 卡片背景 | `#FFFFFF` |
| 主文字 | `#20242C` |
| 次要文字 | `#737B88` |
| 主色 | `#4F6EF7` |
| 成功 / 警告 / 错误 | `#16A36A` / `#E6A23C` / `#DC5252` |
| 边框 | `#E6E8EC` |
| 圆角 | 10–16px |
| 按钮高度 | 36–42px |
| 字体 | Inter、PingFang SC、Microsoft YaHei、system-ui |

颜色和圆角统一收敛到 `app.css` 顶部的 CSS 变量；按钮、表单、状态标签和卡片复用
同一套 `.btn-*`、`.field`、`.tag`、`.metric` 样式。

## 3. 页面与组件

### 主工作台

- 顶栏：Logo、当前项目名、服务状态、历史项目、模型设置、使用说明、本地用户。
- 左侧工作流：四阶段导航（需求输入 / 需求澄清 / 设计方案 / 设计规格），
  状态覆盖进行中、已完成、待生成、待确认和存在问题。
- 首页：标题、说明、产品想法输入、示例、字数统计、折叠约束、开始分析。
- 需求澄清：原始想法、分类问题、推荐说明、回答/跳过/暂时无法确定、确认生成。
- 设计方案：A/B 方案双栏对照、取舍表、评审分歧、右侧讨论摘要与最终决策；
  讨论过程单独成页，含角色、阶段、时间线筛选/回放、CLM/OBJ/ASM/DEC 黑板。
- 设计规格：三栏文档布局（目录 / 正文 / 审查），18 个正文章节与需求追踪附录；
  审查问题可定位到正文；支持 Markdown、JSON、HTML 和打印 PDF 导出。
- 设置：独立 modal，API 供应商、Base URL、模型、密码类型 Key、连接测试；
  Key 只保存在本次会话内存，不写入运行产物。

### 历史控制台

- 统一浅色主题；自动加载最近一次运行；保留运行列表、讨论、追溯、规格、
  时间线和待办视图。

## 4. 主要代码改动

- `web/index.html`：新的应用外壳、首页、澄清、结果、规格、设置 modal。
- `web/app.css`：浅色设计 Token、工作流导航、首页、双栏设计页、三栏规格页、
  设置弹窗、响应式与打印样式。
- `web/app.js`：四阶段导航、模式切换、结构化约束、澄清分类与答案、设置 modal、
  模型连接测试、规格目录、审查定位、HTML 导出。
- `web/console.html`：运行历史页浅色主题、自动打开最近运行、返回工作台入口。
- `server.py`：新增 `POST /api/llm/test`；保留原有接口。
- `spec_doc.py`：规格文档扩展到 18 个正文章节，增加用户需求、数据结构、API、
  核心流程、异常、安全权限和测试计划。
- `spec.py`：功能验收判据补充可度量的时间/条目阈值。

## 5. 接口变化

新增：

```text
POST /api/llm/test
{ "llm": { "base_url": "...", "model": "...", "api_key": "..." } }
→ 200 { "ok": true, "model": "...", "questions": 3, "message": "..." }
```

其余接口保持不变。`llm` 配置只接受 `base_url / api_key / model`，不落盘、不回显。

## 6. 测试与验证

```text
cd modules/requirements && python -m unittest discover -s tests
Ran 221 tests ... OK

python -m unittest discover -s tests
Ran 113 tests ... OK

node --check modules/requirements/ideaflow/web/app.js
OK
```

浏览器验证（Edge headless，截图见 `docs/ui/`）：

- 首页正常加载、CTA 可用。
- `/?mode=spec&case=campus-books-tight&auto=1` 真实走通生成流程。
- 澄清页可加载分类问题并输入答案。
- 设计方案页 A/B 对照与右栏讨论摘要正常。
- 讨论页时间线、角色、黑板正常。
- 规格页目录、正文、审查面板与导出入口正常。
- 设置 modal 可打开，新增连接测试按钮。
- 历史控制台自动加载最近运行。

## 7. 尚未完成与原因

- 章节级编辑/保存、单章重新生成：当前后端没有章节级写入接口；为避免做成本地假保存，
  本轮只提供目录跳转、审查定位和整份文档导出。
- DOCX 导出：项目没有 DOCX 依赖，未引入重量级依赖；已提供 Markdown、JSON、HTML
  和浏览器打印 PDF。
- 历史版本切换：运行目录保留版本文件，但下载接口未提供版本参数；本轮在审查面板
  展示修订记录，版本级切换仍由运行控制台查看。
- 前端仍沿用无构建的原生 `app.js` 单文件（现有测试锚点依赖其中的函数名）；页面
  区块和样式已分层，但组件级 JS 拆分需要同步迁移测试，本轮未强行拆分。
- 规格章节按现有数据生成 18 个正文章节；没有依据的安全、异常或用户指标不编造数值，
  以“未声明/待确认”呈现。

## 8. 截图

- `docs/ui/before-home.png` / `docs/ui/after-home.png`
- `docs/ui/before-spec.png` / `docs/ui/after-spec.png`
- `docs/ui/after-clarify.png`
- `docs/ui/after-design.png`
- `docs/ui/after-settings.png`
- `docs/ui/after-console.png`
