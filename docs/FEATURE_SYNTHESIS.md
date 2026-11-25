# 需求功能归纳与模块生成：验收记录

本文件记录「讨论后归纳功能清单，再由功能清单生成规格模块」这一轮的实现范围、
验收结果和已知限制，可直接作为 PR 说明使用。

> 后续针对 `eval/check_spec_quality.py` 的文件契约、CLM→R 映射、审计分母、
> 验收/估时/未决项修复见 [`docs/SPEC_QUALITY.md`](SPEC_QUALITY.md)。

## 1. 本轮改动

- 讨论收敛后新增 `features` 阶段：`ideaflow/features.py` 调
  `agent.respond("features", role, context)`，统一校验 3~12 条、名称 2~24 字、
  来源必须是真实编号、`inferred` 必须是布尔值。
- `agents.py` 的 Mock 和 LLM 都实现该阶段；LLM 输出不合格时抛 `ContractError`，
  复用现有的错误回注重试。
- `specflow.py` 在讨论完成后把功能清单写入 run 目录的 `features.json`。
- `spec.py` 的 `build_spec(features=...)` 改为按功能生成模块：模块只来自功能，
  原文需求通过 `source_refs` 追溯，不再逐条变模块。
- `snapshot.py` 把方案范围转成「范围：…」需求条目，并保证不被可选推断假设挤掉。
- `audit.py` 在功能清单模式下只统计被功能引用的需求，避免把未收录需求误判成
  「需求未覆盖」。
- 模块名从需求或功能名生成；同名自动加「（2）（3）」；单份规格最多 12 个模块，
  超出统一报 `ContractError("TOO_MANY_MODULES")`。
- `eval/real_smoke.py` 增加 `features.json` 契约校验，并支持用 `--out` 保留
  真实运行产物。

`contracts/v2`、`modules/orchestrator` 和页面没有改动。

## 2. 验收结果

### 2.1 需求模块测试

```text
cd modules/requirements && python -m unittest discover -s tests
Ran 191 tests ... OK
```

原始要求是 187 条不能退化；本分支新增了功能归纳、范围拆分、模块去重等用例，
加上规格质量用例后共 199 条，全部通过。

### 2.2 根目录测试

```text
python -m unittest discover -s tests
Ran 105 tests ... OK (skipped=12)
```

12 条 skip 都是原有的「待实现」项；`tests/test_feature_synthesis.py` 中的功能
归纳用例均已取消 skip 并通过。

### 2.3 一句话需求拆分

Mock 模式：

```text
POST /api/start {"requirement":"我要做微信"}
accepted True
plan_id PLAN-SPEC-001
spec_version v1
total 6
tasks ['聊天会话','好友通讯录','消息通知','群聊','朋友圈','登录注册']
```

### 2.4 真实模型冒烟（质量修复前）

```text
python eval/real_smoke.py --require-real --out .dsh-real/real2
案例 campus-books：快照 26 项，功能 10 条，模块 10 个，规格状态 awaiting_human
模型：llm ｜ 真实调用 35 次 ｜ 讨论 开启
契约校验：通过
exit=0
```

真实调用 35 次，214,848 token，累计约 2046 秒。`snapshot`、`spec`、
`features` 三份契约均通过。

## 3. 真实 features.json

```text
F-01 教材快速发布          CLM-021,CLM-032,CLM-040              inferred=False
F-02 课程教材白名单选书    CLM-032,CLM-025,CLM-031              inferred=False
F-03 书名别名模糊搜索      CLM-026,CLM-015,CLM-042              inferred=False
F-04 课程现货聚合页        CLM-035,CLM-004,CLM-042              inferred=False
F-05 自提意向与联系方式互换 CLM-027,CLM-040,CLM-051             inferred=False
F-06 商品状态流转与单方成交 CLM-028,CLM-040,CLM-051,CLM-052    inferred=False
F-07 内容安全校验与举报下架 CLM-041,CLM-051,CLM-015             inferred=False
F-08 邀请码准入与核验徽章  CLM-034,CLM-024,CLM-051              inferred=False
F-09 履约记录展示          CLM-023,CLM-031,CLM-051              inferred=True
F-10 教材收藏与状态变更可见 CLM-033,CLM-028,CLM-006,CLM-042    inferred=False
```

全部是功能名，没有「问题定义/关键路径/全部范围/结构边界/剩余预算」这类过程词；
`source_refs` 都是真实 `CLM` 编号。

## 4. show_run 改前 vs 改后

Mock「我要做微信」：

```text
快照条目 9 ｜ 功能 6 ｜ 模块 6 ｜ 待确认 6 ｜ 状态 draft
对比基线：
  功能 0 → 6
  模块 1 → 6
  新增功能：聊天会话、好友通讯录、消息通知、群聊、朋友圈、登录注册
```

真实 campus-books（质量修复前）：

```text
快照条目 26 ｜ 功能 10 ｜ 模块 10 ｜ 待确认 10 ｜ 状态 awaiting_human
模型调用 35 次 ｜ token 214848 ｜ 累计耗时 2046.0s
```

## 5. 已知限制与说明

- 真实 `campus-books` 规格状态是 `awaiting_human`，原因是案例约束，不是功能
  归纳失败：
  - `CONSTRAINT_CONFLICT`：承诺 25.5 天，超出 14 天期限；
  - `CONSTRAINT_RISK`：10 个模块与 2 名开发不匹配。
  按现有规则会升级人工决策；`real_smoke` 的契约校验仍通过。
- 功能只引用 `CLM` 编号时，先用 claim 的 `requirement_ids` 反查 R；查不到再用
  正文重叠兜底；仍查不到才退化为 `F-xx`，同时 audit 按功能数计数。不要再把
  `requirement_ids` 留空。
- 规格文件 `spec.v*.json` / `spec.json` 只保留契约字段；导出元数据在
  `spec.bundle.json`，详见 [`docs/SPEC_QUALITY.md`](SPEC_QUALITY.md)。
- 真实运行产物不提交仓库；需要复现时用
  `python eval/real_smoke.py --require-real --out <目录>`。
- 真实模型的 API key 只在本次进程的环境变量中使用，没有写入 `.env`、日志或
  仓库。

## 6. 复现命令

```powershell
cd modules/requirements
python -m unittest discover -s tests

cd ../..
python -m unittest discover -s tests
python eval/real_smoke.py --require-real --out .dsh-real/real2
```
