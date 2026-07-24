# 写作助手模块 `writing_chat/`

## 设计概述

多轮对话界面，帮助用户讨论 LaTeX 文档内容、获取写作建议、直接修改指定段落。

### 核心差异

| 维度 | typo_check / expression_opt | writing_chat |
| --- | --- | --- |
| 交互模式 | 单向触发 → 返回结果 | 双向多轮对话 |
| LLM 输出 | 单段落结构化结果 | 双轮协议：RS1 流式 Markdown / RS2 结构化编辑建议 |
| Marker 保护 | 是 | 分层：纯对话无 marker；编辑走 marker pipeline |
| 状态管理 | 无状态 | 有状态：按项目路径隔离持久化 |

### 双轮协议

- **RS1**：用户发送消息 → SSE 流式 `chat_stream_with_usage` → Markdown 自由回复
- **RS2**：用户在 RS1 回复上点击"生成编辑建议" → 结构化 JSON → `LlmEditResult` → `EditOp`

RS2 不直接写回 `.tex`，用户再点"应用到 .tex"才调用 `project_manager.apply_change`。

## Pydantic 模型

- `EditOp` — 编辑操作（file_path, paragraph_index, original_latex, new_latex, diff_text, applied）
- `ChatMessage` — 聊天消息（id, role, content, tokens, edit_op, compressed, snapshot 等）
- `LlmEditResult` — RS2 LLM 输出（paragraph_index, original, optimized, reason）
- `ChatSendRequest` / `GenerateEditRequest` / `ApplyEditRequest` / `RenameSessionRequest` — 请求体
- `LlmSummaryResult` — 压缩摘要 LLM 输出（summary）

## 会话上下文管理 `context.py`

**磁盘持久化**：按项目路径 SHA256 前 16 位隔离存储，`session_id` 格式 `{scope}:{uuid}`。

**Token 压缩**：累计 token 超过阈值（默认 200000）时触发压缩：将 ≥4 条消息的历史由 LLM 生成摘要，替换为一条 summary 消息，原消息保存到 `snapshot` 字段。

**撤回/删除**：summary 消息撤回时还原 snapshot；普通消息等同删除。

**上下文锚点**：`build_file_context_text` 构造纯文本上下文（全文最多 12 段，或当前段 ±1 段）。

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `send(req, workspace_id)` | RS1 主聊天流：加载/创建会话 → 追加消息 → 压缩判定 → chat_stream_with_usage → SSE 推送 |
| `generate_edit(req, workspace_id)` | RS2 编辑流：strip_to_plain_text → marker 还原 → diff → 附挂 EditOp |
| `apply_edit(req, workspace_id)` | 调用 apply_change 写回 .tex，标记 edit_op.applied |
| `delete_message` / `undo_message` / `rename_session` | 消息操作 |
| `list_sessions` / `load_session` / `delete_session` | 会话 CRUD |

System Prompt 分三类：聊天角色、编辑操作、压缩摘要。

## API 路由

Prefix: `/api/writing-chat`, Tag: `writing_chat`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/send` | SSE 流式发送消息 |
| POST | `/generate-edit` | 生成 RS2 编辑建议 |
| POST | `/apply-edit` | 编辑写回 .tex |
| POST | `/message/delete` / `/message/undo` | 消息操作 |
| PATCH | `/sessions/{id}/rename` | 重命名会话 |
| GET | `/sessions` | 列出会话 |
| GET | `/sessions/{id}` | 加载会话 |
| DELETE | `/sessions/{id}` | 删除会话 |
| GET/POST/DELETE | `/assistants[/{name}]` | 助手提示词 CRUD |
| POST | `/assistants/active` | 设置激活助手 |

## 助手提示词管理 `prompt_manager.py`

与 expression_opt 相同的存储策略：`{custom_prompts_dir}/{name}.txt`，`_active.txt` 记录当前激活。

## `llm_client.py` 扩展

新增 `chat_stream_with_usage`：返回 `AsyncIterator[StreamChunk]`（content / usage 互斥），末次 chunk 携带 `UsageInfo`。

## 配置项

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| compression_threshold_tokens | 200000 | 触发上下文压缩的 token 阈值 |
| sessions_dir | writing_chat_sessions/ | 会话持久化目录 |
| custom_prompts_dir | prompts/writing_chat/ | 助手提示词目录 |
| summary_temperature | 0.2 | 摘要 LLM 温度 |
| summary_max_tokens | 1024 | 摘要最大 token 数 |

## 前端 `useWritingChat` hook

封装：项目恢复、会话管理、SSE 流式发送、深度思考解析（parseThinking + processLoadedMessages）、生成编辑、应用编辑（支持手动微调 customText）、删除/撤回消息、助手管理。

**子 hooks**：`writing-chat/sessions.js`（会话管理）、`writing-chat/assistants.js`（助手管理）、`writing-chat/captcha.js`（验证码处理）、`writing-chat/sse.js`（SSE 事件处理）、`writing-chat/utils.js`（工具函数）。
