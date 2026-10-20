# 开源客服项目对照笔记（2026-10-19）

目标：借鉴 Chatwoot、Rasa 和 Dify 的**可核实实现模式**，优先把 DOTS Survivors 现有客服项目做稳。以下只引用项目官方仓库与文档；没有按 GitHub Star 数推断代码质量，也不建议直接搬入其他项目的整套架构。

## 值得落实的五个模式

| 优先级 | 官方实现或产品行为 | 本项目现状与建议 |
| --- | --- | --- |
| P0：账户隔离 | Chatwoot 的会话子资源控制器先从 `Current.account.conversations` 查会话，再做授权；消息只能从已授权的会话关系中查。实时通道区分员工与联系人 token，联系人 token 仅能收到其会话的事件。[会话控制器源码](https://github.com/chatwoot/chatwoot/blob/develop/app/controllers/api/v1/accounts/conversations/base_controller.rb)、[消息控制器源码](https://github.com/chatwoot/chatwoot/blob/develop/app/controllers/api/v1/accounts/conversations/messages_controller.rb)、[WebSocket 文档](https://www.chatwoot.com/hc/user-guide/articles/1677691027-how-to-setup-a-web_socket-connection) | 本项目已在 `app/services/support_store.py` 将玩家会话绑定 `user_id`，`app/api/v1/support.py` 用认证用户解析玩家会话。继续检查**每条**带票据、消息、反馈、文档 ID 的读取与写入路径：先确定当前用户可访问的父资源，再查子资源；员工角色与玩家角色的 SSE 也要分别测试。覆盖“用户 A 用用户 B 的 ID、游标、反馈目标、事件流”的拒绝用例。 |
| P0：历史分页与增量追赶 | Chatwoot 的 `MessageFinder` 默认取最近 20 条，`before` 取更早 20 条，`after` 拉取新消息，限制单次返回量；查找器始终基于当前会话的消息关系。[MessageFinder 源码](https://github.com/chatwoot/chatwoot/blob/develop/app/finders/message_finder.rb) | 本项目玩家消息已经有 `before` 游标及稳定排序，优于一次加载全量。建议补 `after`/`since` 增量接口供断线重连，返回 `has_more` 和下一游标；员工工单列表目前固定最多 200 条，工单上下文可全量加载，应分页。游标必须验证属于当前会话；新消息合并按消息 ID 去重，避免 SSE 重连重复。 |
| P0：实时刷新保留草稿 | Chatwoot 官方指南承诺离开会话或刷新后保留未发送的回复草稿；其 WebSocket 为联系人和员工发送各自有权限的消息与状态事件。[Chatwoot 101](https://www.chatwoot.com/hc/user-guide/en/categories/chatwoot-101)、[WebSocket 文档](https://www.chatwoot.com/hc/user-guide/articles/1677691027-how-to-setup-a-web_socket-connection) | 本项目 `app/static/support/admin.js` 已在工作台重绘前把状态与回复临时保存在 `ticketDrafts` Map，能应对当前页 SSE 重绘；**整页刷新后仍会丢失**。建议按“员工 ID + 工单 ID”保存草稿，短期用浏览器本地存储并在成功发送后删除；若要跨设备同步，再设计受鉴权保护的服务端草稿。事件只触发增量数据更新，不能覆盖正在编辑的输入。 |
| P1：转接是明确状态迁移 | Chatwoot 的 AgentBot 把机器人队列设为 `pending`、人工接管设为 `open`，可从人工队列回到机器人队列；Rasa 的 handoff 示例携带完整 tracker 并暂停机器人，直到恢复。[Chatwoot AgentBot 文档](https://www.chatwoot.com/hc/user-guide/articles/1677497472-how-to-use-agent-bots)、[Rasa handoff 文档](https://legacy-docs-oss.rasa.com/docs/rasa/fallback-handoff/) | 本项目已用活跃工单暂停 AI，并在结单后恢复。建议把状态变化记录为独立事件（时间、操作者、原状态、新状态、原因），让并发领取、人工回复、释放、结单都能审计与重放；保留玩家可见的转接提示。Chatwoot 的审计日志文档标为 Enterprise 功能，此处仅借鉴审计字段模式，不声称其在开源版可用。[审计日志文档](https://www.chatwoot.com/hc/user-guide/articles/1692251809-how-to-use-audit-logs) |
| P1：知识处理的可见状态 | Dify 官方知识流水线把数据源、提取、切分、索引、检索和发布分开，并提供检索测试；文档 API 返回索引状态与错误。知识检索结果包含内容、标题和元数据。[流水线文档](https://github.com/langgenius/dify-docs/blob/main/en/cloud/use-dify/knowledge/knowledge-pipeline/knowledge-pipeline-orchestration.mdx)、[文档 API 源码](https://github.com/langgenius/dify/blob/main/api/controllers/service_api/dataset/document.py)、[检索节点文档](https://github.com/langgenius/dify-docs/blob/main/en/cloud/use-dify/nodes/knowledge-retrieval.mdx) | 本项目已支持上传、解析、切分、向量与关键词检索、草稿发布、PDF 页码引用。建议先补入库任务状态（排队/处理中/成功/失败、错误摘要、重试次数）与发布前检索预览，再考虑新向量数据库。这样失败上传不会表现为“没有命中”，知识维护者能定位解析或 embedding 故障。 |

## 推荐验收顺序

1. **隔离测试先行**：两个玩家和两个员工账号，逐个验证会话、消息游标、反馈、工具调用与 SSE 的授权范围；确保旧匿名 token 迁移后失效。
2. **完成历史同步协议**：分页工单和上下文，给消息增量游标与 `has_more`；测试断网重连、重复事件、分页边界和同时间戳消息。
3. **保护客服输入**：工作台自动刷新时保留每张工单的草稿；刷新页面后恢复，成功发送后清除；发送失败时保留。
4. **补可诊断性**：记录转接状态事件与知识索引失败状态，再在管理端展示。可把这些事件 ID 与现有日志、Langfuse trace 关联。

这些是针对当前代码的工程建议，并非外部项目替本项目验证过的性能或安全结论。对照位置：`app/services/support_store.py`、`app/api/v1/support.py`、`app/static/support/player.js`、`app/static/support/admin.js`、`app/services/support_documents.py`。
