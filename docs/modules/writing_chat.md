# 写作助手模块 `writing_chat/`

## 设计概述

多轮对话界面，帮助用户讨论 LaTeX 文档内容、获取写作建议、直接修改指定段落。

### 核心差异

| 维度 | typo_check / expression_opt | writing_chat |
| --- | --- | --- |
| 交互模式 | 单向触发 → 返回结果 | 双向多轮对话 |
| LLM 输出 | 单段落结构化结果 | RS1 流式 Markdown + Function Calling / RS2 分隔符协议编辑建议（无 JSON） |
| Marker 保护 | 是 | 否；对话和编辑都直接使用原始 LaTeX |
| 状态管理 | 无状态 | 有状态：按项目路径隔离持久化 |

### 双轮协议 + Function Calling

- **RS1**：用户发送消息 → SSE 流式 `chat_stream_with_tools` → 实时输出文本 + 检测工具调用。RS1 中 LLM 可发起 Function Calling 调用联网搜索、学术研究、项目文件浏览、段落编辑和请求澄清工具，工具结果回传 LLM 后继续流式输出最终回复
- **RS2**：用户在 RS1 回复上点击"生成编辑建议" → LLM 按分隔符协议（`<<<LATEX>>>...<<<END>>>`）输出修改后的完整 LaTeX 段落，可附带 `<<<REASON>>>...<<<END>>>` 说明 → 后端提取、校验并附挂 `EditOp`。LaTeX 不经过 JSON 字符串传输，反斜杠原样保留，从根上避免 JSON 转义静默损坏；`original` 由后端从段落原文填充，不再要求 LLM 回显

RS2 不直接写回 `.tex`，用户再点"应用到 .tex"才调用 `project_manager.apply_change`。

## Pydantic 模型

- `EditOp` — 编辑操作（file_path, paragraph_index, original_latex, new_latex, diff_text, applied）
- `ChatMessage` — 聊天消息（id, role, content, tokens, edit_op, compressed, snapshot, tool_calls, tool_call_id 等）
    - `role` 中 tool 角色用于持久化工具调用结果
    - `tool_calls`（仅 assistant 角色）：LLM 请求调用的工具列表（OpenAI tool_calls 格式）
    - `tool_call_id`（仅 tool 角色）：关联到触发该结果的工具调用 ID
- RS2 无独立 LLM 输出模型：LLM 按分隔符协议输出 `<<<LATEX>>>...<<<END>>>`（必选）+ `<<<REASON>>>...<<<END>>>`（可选）纯文本块。提取出的修改段落经 `latex_json_guard.py` 的 `validate_latex_field(relaxed=True)` 宽松校验（放行 `\t` / `\r` 等合法 LaTeX 空白），失败时回喂 LLM 重试（最多 3 次）
- `ChatSendRequest` / `GenerateEditRequest` / `ApplyEditRequest` / `RenameSessionRequest` / `ConfirmToolRequest` / `CancelToolRequest` / `ClarifyRequest` / `CancelClarifyRequest` — 请求体

## 会话上下文管理 `context.py`

**磁盘持久化**：按项目路径 SHA256 前 16 位隔离存储，`session_id` 格式 `{scope}:{uuid}`。

**Token 压缩**：累计 token 超过阈值（默认 200000）时触发压缩：将 ≥4 条消息的历史由 LLM 生成摘要，替换为一条 summary 消息，原消息保存到 `snapshot` 字段。

**删除消息**：summary 消息删除时还原 snapshot（恢复原始对话）；普通消息直接移除。

**上下文锚点**：`build_file_context_text` 构造纯文本上下文（全文最多 12 段，或当前段 ±1 段）。

**工具消息持久化**：`append_tool_messages()` 将 Function Calling 工具调用循环产生的 assistant（含 tool_calls）和 tool 角色消息持久化到上下文，确保工具调用历史在会话间可追溯。

**消息转换**：`to_openai_messages()` 已扩展以支持 tool 角色和 assistant+tool_calls 格式，将 `ChatMessage` 正确地转换为 OpenAI messages API 格式。

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `send(req, workspace_id)` | RS1 主聊天流：加载/创建会话 → 追加消息 → 压缩判定 → 注入 Function Calling 工具 → chat_stream_with_tools（实时产出文本 + 检测工具调用）→ 工具调用循环 → 后置流式 → SSE 推送 |
| `generate_edit(req, workspace_id)` | RS2 编辑流：把原始 LaTeX 交给 LLM → 分隔符协议提取 + 宽松校验（失败回喂重试，最多 3 次）→ diff → 附挂 EditOp |
| `apply_edit(req, workspace_id)` | 调用 apply_change 写回 .tex，标记 edit_op.applied |
| `confirm_pending_tool(pending_id, modified_args)` | 前端确认敏感工具调用（如 edit_paragraph），可选修改参数 |
| `cancel_pending_tool(pending_id)` | 前端取消待定工具调用 |
| `submit_clarification(clarify_id, answer)` | 前端回复 LLM 的澄清请求 |
| `cancel_clarification(clarify_id)` | 前端取消待定的澄清请求 |
| `delete_message` / `rename_session` | 消息操作 |
| `list_sessions` / `load_session` / `delete_session` | 会话 CRUD |

System Prompt 分三类：聊天角色、编辑操作、压缩摘要。

### Function Calling 工具编排

`send()` 方法在 RS1 中通过 `_get_enabled_tools()` 动态注入工具 schema：

1. **请求澄清**（内置）：`request_clarification` — 向用户提问以补充信息或做出选择（始终可用，无条件注入）
2. **联网搜索**（`SearchManager`）：`web_search` — 搜索互联网
3. **项目浏览**（`ProjectToolsManager`）：`list_files` / `read_file_content` / `get_section_structure` / `search_across_files` — 浏览项目文件
4. **段落编辑**（`EditToolsManager`）：`edit_paragraph` / `undo_last_edit` — 直接修改 .tex 文件
5. **学术研究**（`ResearchManager`）：`search_scholarly_works` / `get_work_details` / `find_related_works` / `get_full_text_or_sections` / `search_research_data_resources` — OpenAlex 论文检索、详情、引文追踪和 OA 定位

`send()` 全程使用 `chat_stream_with_tools` 流式调用 LLM。文本 token 一到即推送给前端，工具调用在流末尾累积完整后一并解析。当检测到 `tool_call` 时，service 执行以下流程：

```
chat_stream_with_tools（实时流式文本 + 末尾 tool_calls）
    ↓ 推送 SSE token（文本实时显示）
    ↓ 检测 tool_calls
┌─ 若为 request_clarification:
│    ↓ 推送 SSE clarification_request（含 question + 可选 options）
│   等待前端用户回复（asyncio.Event, 120s 超时）
│    ↓ 用户提交回答
│  回答作为 tool result 回传 LLM
│    ↓ 新一轮 chat_stream_with_tools
└─ 其他工具:
     推送 SSE tool_status
     执行工具（ToolRegistry.execute）
       ↓
     敏感操作检查 → 推送 SSE tool_pending → 等待前端确认/取消
       ↓ (确认后)
     工具结果回传 LLM
       ↓ 新一轮 chat_stream_with_tools（可再次检测工具或直接输出）
LLM 最终回复
```

**`request_clarification` 工具参数**：

| 参数 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `question` | string | 是 | 向用户提出的问题，需清晰具体 |
| `options` | string[] | 否 | 候选项列表，提供时用户可选择或自由输入 |

**敏感操作保护**：`edit_paragraph` 和 `undo_last_edit` 被视为敏感操作。当 `features.function_calling.edit_tools.require_confirmation = true` 时，执行前推送 `tool_pending` SSE 事件，等待前端弹窗确认（超时 120 秒）。用户确认或取消后，结果回传 LLM 继续对话。

**工具消息持久化**：工具调用循环结束后，所有 tool/assistant 消息通过 `append_tool_messages()` 持久化到会话上下文，确保工具调用历史在后续轮次中可追溯。

## API 路由

Prefix: `/api/writing-chat`, Tag: `writing_chat`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/send` | SSE 流式发送消息（含 Function Calling 工具调用循环） |
| POST | `/generate-edit` | 生成 RS2 编辑建议 |
| POST | `/apply-edit` | 编辑写回 .tex |
| POST | `/confirm-tool` | 确认待定工具调用（可选修改参数） |
| POST | `/cancel-tool` | 取消待定工具调用 |
| POST | `/clarify` | 提交对 LLM 澄清请求的回复 |
| POST | `/cancel-clarify` | 取消待定的澄清请求 |
| POST | `/message/delete` | 删除消息（summary 消息还原其快照） |
| PATCH | `/sessions/{id}/rename` | 重命名会话 |
| GET | `/sessions` | 列出会话 |
| GET | `/sessions/{id}` | 加载会话 |
| DELETE | `/sessions/{id}` | 删除会话 |
| GET/POST/DELETE | `/prompts[/{name}]` | 助手提示词 CRUD |
| POST | `/prompts/active` | 设置激活助手 |

## 助手提示词管理 `prompt_manager.py`

与 expression_opt 相同的存储策略：`{custom_prompts_dir}/{name}.txt`，`_active.txt` 记录当前激活。

## `llm_client.py` 扩展

writing_chat 是 `chat_stream_with_tools` 方法的主要消费者。该方法继承 `chat_stream_with_usage` 的流式协议，额外支持 Function Calling 工具检测：
- 流中同时产出 `content`（实时文本）和 `tool_calls`（末尾累积完整后 yield）
- `StreamChunk` 新增 `tool_calls` 字段

## SSE 事件

`send()` 接口在 RS1 流式中可能推送以下事件：

| 事件类型 | 说明 |
| --- | --- |
| `message` | 流式文本块 |
| `tool_call` | LLM 发起工具调用 |
| `tool_status` | 工具执行中状态指示 |
| `tool_pending` | 敏感工具等待用户确认（含 pending_id、tool、args） |
| `tool_cancelled` | 工具被用户取消 |
| `clarification_request` | LLM 向用户提问，等待回复（含 clarify_id、question、options） |
| `tool_result` | 工具执行结果 |
| `tool_result.scholarly_results` | 学术检索或引文追踪的前端摘要，含稳定 ID、作者、年份、期刊、DOI、被引数和 OA 状态 |
| `captcha_challenge` | DDG 验证码挑战 |
| `search_results` | 联网搜索结果 |
| `done` | 流式结束（含 token 用量、session_id） |
| `error` | 错误信息 |

## 配置项

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| compression_threshold_tokens | 200000 | 触发上下文压缩的 token 阈值 |
| sessions_dir | writing_chat_sessions/ | 会话持久化目录 |
| custom_prompts_dir | prompts/writing_chat/ | 助手提示词目录 |
| summary_temperature | 0.2 | 摘要 LLM 温度 |
| summary_max_tokens | 50000 | 摘要最大 token 数 |
| features.function_calling.scholarly_research.enabled | false | 学术研究工具总开关 |
| features.function_calling.scholarly_research.max_results | 20 | 单次工具返回结果上限 |
| features.function_calling.scholarly_research.timeout_seconds | 30 | OpenAlex 请求超时 |
| features.function_calling.scholarly_research.providers.openalex.api_key | 空 | OpenAlex API Key；设置接口返回脱敏值 |

## 前端 `useWritingChat` hook

封装：项目恢复、会话管理、SSE 流式发送、深度思考解析（parseThinking + processLoadedMessages）、生成编辑、应用编辑（支持手动微调 customText）、删除消息（summary 消息还原快照）、工具调用确认、澄清请求、助手管理。

**工具确认流程**：
- `pendingTool` state 存储待确认的工具调用
- `confirmPendingTool(modifiedArgs)` 调用 `/confirm-tool` 接口，可选修改工具参数后确认
- `cancelPendingTool()` 调用 `/cancel-tool` 接口取消

**澄清请求流程**：
- `pendingClarification` state 存储 LLM 的待回复问题（含 clarify_id、question、options）
- `submitClarification(answer)` 调用 `/clarify` 接口提交用户回答
- `cancelClarification()` 调用 `/cancel-clarify` 接口取消

SSE 事件处理器处理 `tool_pending`、`tool_cancelled`、`clarification_request`，并把 `scholarly_results` 存入 assistant 消息。`MessageBubble` 使用紫色折叠卡展示学术记录、文献类型、被引数、开放获取状态和 DOI 链接。

**WritingChatPage 工具弹窗**：
- `ToolConfirmDialog` — 敏感工具确认，使用 shadcn/ui Dialog，根据工具类型显示对应图标和文案
- `ClarifyDialog` — 澄清请求弹窗，展示问题、可选项 pill 按钮和自由输入文本框

**子 hooks**：`writing-chat/sessions.js`（会话管理）、`writing-chat/assistants.js`（助手管理）、`writing-chat/captcha.js`（验证码处理）、`writing-chat/sse.js`（SSE 事件处理）、`writing-chat/utils.js`（工具函数）。
