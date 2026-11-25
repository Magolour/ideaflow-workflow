# 规格质量修复：文件契约、映射、审计分母与验收

本文件记录针对 `eval/check_spec_quality.py` 四处 FAIL、两处 WARN 的修复与复验，
可直接作为 PR 说明使用。

## 1. 修复对照

| 检查项 | 修复前 | 修复后 |
| --- | --- | --- |
| 文件契约 | `spec.v*.json` / `spec.json` 带 `artifacts`，不符合 `contracts/v2/spec.schema.json` | 纯规格写 `spec.v*.json` / `spec.json`；导出元数据写 `spec.bundle.json` |
| 功能到需求映射 | 功能只引用 `CLM`，模块 `requirement_ids` 全空 | 先按 claim 的 `requirement_ids` 反查 R，再用 claim/功能正文重叠兜底，最后才退化为 `F-xx` |
| 审计分母 | feature 模式分母变 0，真空通过 | 分母改为功能数；每个功能必须有需求、模块、验收，缺失按 blocking 报 |
| 验收条件 | `requirement_id` 为空，9 条同一模板 | `requirement_id` 填映射到的 R（或 F-id），判据按功能名/描述生成 |
| 估时 | 全部固定 2.0 | 优先取草案工作包中对应需求的估算，其次决策成本；取值按功能分化 |
| 未决项 | 每个未映射功能刷一条「待确认对应需求」 | 合并成一条；推断功能也合并成一条，最多两条 |

## 2. 代码位置

- `modules/requirements/ideaflow/spec_doc.py`：`spec.bundle.json`；`verify_export` 读导出包
- `modules/requirements/ideaflow/spec.py`：`_feature_refs`、`_feature_estimate`、按功能生成验收与合并未决项
- `modules/requirements/ideaflow/features.py`：`claim_requirements`，把 CLM/OPT 映射到 R
- `modules/requirements/ideaflow/contracts.py` / `blackboard.py`：保留 claim 的 `requirement_ids`
- `modules/requirements/ideaflow/audit.py`：feature 模式按功能数计覆盖率，缺模块/验收报 blocking
- `modules/requirements/ideaflow/revisions.py`：只对真实 R 引用做范围裁剪，F-id 不静默改估算
- `modules/requirements/ideaflow/server.py`：下载接口改读 `spec.bundle.json`
- `modules/requirements/tests/test_spec_quality.py`：新增映射、分母、验收、估时、未决项、纯规格测试

## 3. 复验结果

### 3.1 质量检查（9 功能志愿平台，完整 `specflow.run` 路径）

```text
python eval/check_spec_quality.py <run 目录>

PASS 文件契约：spec 磁盘产物符合 contracts/v2
PASS 功能到需求映射：所有模块都有需求引用
PASS 审计覆盖率：覆盖 9/9
PASS 验收条件：9 条均有归属且文案不重复
PASS 估时：4 种取值
PASS 未决项：1 条

结论：全部通过。
```

运行摘要：

```text
快照条目 26 ｜ 功能 9 ｜ 模块 9 ｜ 待确认 1 ｜ 状态 draft
F-01 活动发布 ｜ 来源 CLM-001
F-02 扫码签到 ｜ 来源 CLM-002
F-03 志愿时长 ｜ 来源 CLM-003
F-04 组队报名 ｜ 来源 CLM-004
...
M-01 活动发布 ｜ 需求 R-02
M-02 扫码签到 ｜ 需求 R-03,R-07
M-03 志愿时长 ｜ 需求 R-01,R-04,R-08
...
```

### 3.2 单测

```text
cd modules/requirements && python -m unittest discover -s tests
Ran 199 tests ... OK

python -m unittest discover -s tests
Ran 105 tests ... OK (skipped=12)
```

## 4. 说明

- 真实模型这轮没有重新调用（本机 key 已在验收后回收）；上面的 9 功能运行
  走的是与真实模型相同的 `specflow.run` 路径，只是功能/草案由确定性 fixture
  提供，用于稳定复现四处质量 FAIL。
- 真实运行复验时仍使用：
  `python eval/check_spec_quality.py modules/requirements/output/<运行目录>`
- `spec.v*.json` / `spec.json` 现在只保留规格契约字段；需要导出元数据时读
  `spec.bundle.json`。
