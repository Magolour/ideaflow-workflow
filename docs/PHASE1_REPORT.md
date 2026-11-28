# 第一阶段报告：讨论数据传递与讨论过程可视化

## 1. 范围

本阶段只做两件事：

1. 把讨论数据完整传到规格结果页面；
2. 在结果页新增「讨论过程」标签，支持静态回放和筛选。

人工澄清、规格质量增强留到第二阶段。

## 2. 后端改动

| 文件 | 改动 |
| --- | --- |
| `blackboard.py` | 事件增加 `stage`、`round` 字段，保持只追加 |
| `pipeline.py` | 各阶段写入前设置事件上下文，事件可按阶段/轮次筛选 |
| `specflow.py` | 规格 payload 增加 `discussion`，包含角色、阶段、主张、假设、异议、回应、方案、立场、决策、关键意见、分歧、终止与共识 |
| `server.py` | 不做破坏性修改；结果页继续使用 job.result |

`discussion` 结构：

```json
{
  "stage": "proposal",
  "round": 2,
  "stages": [{"key": "clarify", "label": "澄清", "done": true}],
  "roles": [{"id": "ba", "name": "需求分析", "participated": true, "claims": 2}],
  "claims": [], "assumptions": [], "objections": [], "options": [],
  "stances": {}, "decisions": [], "open_questions": [], "reviews": [],
  "divergence": {}, "key_opinions": [],
  "termination": {}, "consensus": {},
  "events": []
}
```

事件字段：`seq / ts / actor / action / object_type / object_id / detail / version / stage / round`。

## 3. 前端改动

文件：`index.html`、`app.js`、`app.css`。

规格结果新增「讨论过程」标签，包含：

1. 角色卡片：参与状态、主张数、异议数、立场数；
2. 七阶段进度条；
3. 讨论时间线，显示序号、角色、动作、阶段、轮次、时间；
4. 观点 CLM 表：编号、角色、依据类型、主张、理由；
5. 异议 OBJ 表：指向、提出者、严重度、分歧分类、内容、回应、状态；
6. 候选方案卡片与六角色立场矩阵；
7. 关键意见处理表：采纳/拒绝/延期/待证据/待人工裁决；
8. 共识状态与停止原因；
9. 按角色、轮次筛选；
10. 「回放讨论」按钮，按时间线逐条高亮，仅使用结构化事件。

未展示模型隐藏推理，只展示黑板与事件中已提交的结构化内容。

## 4. 测试

| 测试 | 结果 |
| --- | --- |
| 需求模块全量 | 206 passed |
| 新增 `tests/test_discussion_payload.py` | 4 passed |
| 事件字段完整性 | 通过 |
| 无讨论时 payload 为 None | 通过 |
| 根目录全量 | 113 项，1 项等待 `estimate_days` 契约放开 |

新增用例：

```text
test_roles_and_stages_are_exposed
test_events_have_traceable_fields
test_claims_objections_and_decisions_are_linked
test_without_discussion_payload_is_none
```

手工验证：

- 启动 `python -m ideaflow.server --port 8015 --out runs/ui-check`；
- 生成一次 Mock 规格；
- `job.result.discussion` 返回 6 个角色、24 条事件、7 个阶段；
- 页面标签可切换，时间线可筛选，回放可逐条高亮。

## 5. 已知边界

- 讨论事件仍是任务完成后一次性返回，实时推送留到下一阶段；
- 讨论页只读，不能在页面回答问题；人工澄清在第二阶段接入；
- 旧运行没有 `discussion` 字段时，标签显示“本次规格没有讨论记录”。

## 6. 下一步

第二阶段：

1. Web 端回答澄清问题；
2. 未回答问题保持 `open`，不标为已确认；
3. 规格 11 章完整性与追溯检查；
4. 讨论与规格决策的双向跳转。
