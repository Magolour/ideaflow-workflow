# 模块边界与接口（草案）

需求模块输出设计规格文本，至少包含产品目标、功能、约束、验收标准；编排器目前接受 POST /api/start 的 {"requirement":"设计规格全文"}，并非单独的 spec 文件上传接口。

编排器也接受 {"tasks":[...]}、任务数组及原型已有契约包。不要把上游任务 JSON 误称为设计规格。
任务字段：task_id、name、deliverable、acceptance_items（数组）、suggested_role（ai/human/ai+human）、priority（high/medium/low）。

可视化读取 GET /api/progress?since=0&limit=200；使用 tasks、edges、layers、execution_order、waiting_on、percent 和 event_log。使用 next_since 增量读取，接口返回 has_more 时继续获取。
依赖边字段：from、to、type（force/data/resource）、reason。

交互命令使用 POST /api/events：ai_report、human_op、change。202 只表示入队，需随后查询状态和 last_error。

当前服务一进程一实例，POST /api/start 只能成功一次。独立前端建议经反向代理保持同源；原型 CORS 为 *，不能直接作为公网安全部署方案。

修改字段语义须在 PR 同时更新文档和示例，并请上下游负责人确认。现有详细载荷以 modules/orchestrator/README.md 和源码为准。
