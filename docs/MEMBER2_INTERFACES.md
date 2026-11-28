# 成员 2 接口：需求编辑与共识矩阵

本文件只描述成员 2 负责的后端接口与数据字段，不涉及页面实现。

## T-07：单条需求编辑

```http
PATCH /api/spec/item
Content-Type: application/json
```

请求：

```json
{
  "run_id": "20261128-120000-spec-campus-books",
  "item_id": "R-01",
  "text": "学生可以发布二手教材，并上传最多三张照片",
  "actor": "成员2",
  "expected_version": 1,
  "expected_hash": "可选，当前规格内容哈希"
}
```

响应：

```json
{
  "run_id": "20261128-120000-spec-campus-books",
  "item": {"id": "R-01", "text": "学生可以发布二手教材，并上传最多三张照片"},
  "spec_id": "SPEC-001",
  "version": 2,
  "hash": "新的规格哈希",
  "snapshot_version": 2,
  "snapshot_hash": "新的快照哈希",
  "status": "draft",
  "audit": {"counts": {"blocking": 0, "total": 0}}
}
```

规则：

- 只允许 `type=requirement` 的条目；
- 只改 `text`，不支持批量；
- `expected_version` 与当前规格版本不一致返回 `409 STALE_VERSION`；
- 旧 `spec.v1.json`、`snapshot.v1.json` 保留，新版本追加；
- 修改后自动重跑审查，状态为 `draft` 或 `blocked`，不自动确认。

错误码：

| HTTP | code | 含义 |
| --- | --- | --- |
| 404 | `UNKNOWN_RUN` | 运行不存在 |
| 404 | `UNKNOWN_ITEM` | 条目不存在 |
| 409 | `STALE_VERSION` | 版本或哈希过期 |
| 422 | `NOT_EDITABLE` | 不是需求条目 |
| 422 | `INVALID_TEXT` | 空文本或超过 500 字 |

页面调用方应在 409 后刷新 `/api/job`，不要自动重试旧版本。

## T-17：共识矩阵

`GET /api/job?id=<job_id>` 的 `result` 中新增可选字段：

```json
{
  "consensus_matrix": {
    "options": [
      {"id": "OPT-A", "key": "A", "name": "最小可交付闭环"}
    ],
    "roles": [
      {"id": "ba", "name": "需求分析"}
    ],
    "cells": [
      {
        "role": "ba",
        "role_name": "需求分析",
        "option": "OPT-A",
        "option_key": "A",
        "stance": "agree",
        "reason": "范围可控",
        "condition": ""
      }
    ],
    "support": [
      {"option": "OPT-A", "ratio": 0.83}
    ]
  }
}
```

说明：

- 只追加字段，不改已有 `options`、`decisions`、`objections`；
- `reason`、`condition` 缺失时返回空字符串；
- 没有候选方案时 `options`、`cells` 为空数组；
- 页面缺失该字段时显示「未提供」，不要自行构造。

## `/api/job` 稳定性

已有字段保持不变：

```text
job.id
job.state
job.flow
job.idea
job.constraints
job.logs[]
job.elapsed_ms
job.error
job.result
```

新增字段只允许追加。成员 5 只依赖以上字段和 `consensus_matrix`，不读取未承诺字段。
