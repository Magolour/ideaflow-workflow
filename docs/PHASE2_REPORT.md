# 第二阶段报告：人工澄清与规格质量

## 1. 范围

在第一阶段讨论可视化基础上，补齐：

1. 生成前的人工澄清；
2. 未答问题保持假设，不自动确认；
3. 规格质量检查；
4. 讨论与规格的跳转。

## 2. 人工澄清

### 接口

`POST /api/clarify`

```json
{
  "idea": "做一个校园二手书交易小程序",
  "constraints": ["两周内上线"],
  "live": false
}
```

返回：

```json
{
  "questions": [
    {"id": "Q-001", "text": "这个想法最终由谁验收？", "blocking": true}
  ]
}
```

只跑需求分析角色的首轮澄清，不创建运行目录、不写盘。Mock 与真实模型都可用。

### 生成时带入答复

`POST /api/generate` 的规格请求新增 `answers`：

```json
{
  "flow": "spec",
  "idea": "做一个校园二手书交易小程序",
  "answers": {"Q-001": "由学生会验收"}
}
```

`specflow.run(..., answers=...)` 把答复传给 `pipeline.run`：

- 有答复：问题 `owner=human`，写入一条人工决策；
- 无答复：`owner=human:assumed`，结论为“未知，按假设继续”；
- 不把假设改写成已确认需求。

### 前端

规格模式左侧新增「先澄清（可选）」：

1. 点击后调用 `/api/clarify`；
2. 展示 `Q-xxx` 与输入框；
3. 可留空，留空按假设继续；
4. 点「生成设计规格」时自动带上已填答复。

## 3. 规格质量

### 文档内容

`spec_doc.py` 在原有章节内补齐：

| 要求 | 位置 |
| --- | --- |
| 背景与问题定义 | `1.1` |
| 目标用户与使用场景 | `1.2` |
| 项目目标与非目标 | `1.3` |
| 功能需求及优先级 | `2.1` |
| 业务流程与规则 | `3.1` |
| 总体架构 | `4.1` |
| 模块职责 | `4.2` |
| 接口、输入输出与数据 | `4.3` |
| 非功能需求与技术约束 | `4.4` |
| 验收标准 | 第 5 章 |
| 风险、估算与未决问题 | 第 7、8 章 |

采用子节而不是重排 1—11 章，保持旧文档链接与测试锚点兼容。

### 审查新增

| 检查 | 严重度 |
| --- | --- |
| 决策缺少理由 `DECISION_NO_REASON` | blocking |
| 决策缺少需求/观点/方案来源 `DECISION_NO_SOURCE` | blocking |
| 接口缺少输入或输出 `INTERFACE_INCOMPLETE` | major |
| 模块缺少估算且没有待确认记录 `ESTIMATE_MISSING` | major |

原有覆盖、约束冲突、验收可度量、映射一致性检查保留。

## 4. 讨论与规格跳转

讨论过程标签顶部新增「查看最终设计规格 →」，点击切到「设计规格」标签。
事件委托挂在结果容器，不只挂 banner。

## 5. 测试

| 项 | 结果 |
| --- | --- |
| 需求模块全量 | 209 passed |
| `tests/test_clarify.py` | 3 passed |
| 根目录全量 | 113 项，1 项等待 `estimate_days` 契约放开 |
| `/api/clarify` 手工 | 返回 Q-001/Q-002 |
| 带答复生成 | Q-001 `resolution=由学生会验收`，`owner=human` |

新增用例：

```text
test_questions_are_blocking_and_identifiable
test_answers_are_recorded_as_human_decisions
test_unanswered_questions_stay_assumptions
```

## 6. 已知边界

- 澄清只在生成前收集；生成后不能改答，需要重新生成；
- `estimate_days: null` 的契约放宽仍由测试侧统一处理；
- 11 项内容是子节，不是 11 个顶级章节；
- 讨论与决策的双向逐条跳转只做到页签级，未做“点 CLM 跳到对应 DEC”。

## 7. 后续

1. 逐条跳转：CLM → 对应 DEC / 模块 / 验收；
2. 生成后补答澄清并重跑，而不是重新创建运行；
3. 把 11 项提升为正式章节并同步导出测试。
