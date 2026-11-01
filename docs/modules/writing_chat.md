# 写作助手模块 `writing_chat/`

## 设计概述

多轮对话界面，帮助用户讨论 LaTeX 文档内容、获取写作建议、直接修改指定段落。

### service 包拆分

`WritingChatService` 对外仍从 `backend/modules/writing_chat/service` 导入。内部按职责拆为多个 mixin 子模块，避免单文件超过 1000 行：

| 文件 | 职责 |
| --- | --- |
| `state.py` | 会话级写锁、工具确认、澄清请求等待状态 |
| `sessions.py` | 会话 CRUD 与手动压缩 |
| `conversation.py` | RS1 对话流、工具编排、引用校验 |
| `edits.py` | RS2 编辑建议、应用与消息删除 |
| `compaction.py` | 上下文超限判断、兜底裁剪与 LLM 摘要压缩 |
| `_deps.py` | 共享依赖（LLM 客户端）统一出口，便于测试 patch |

### 核心差异

| 维度 | typo_check / expression_opt | writing_chat |
| --- | --- | --- |
| 交互模式 | 单向触发 → 返回结果 | 双向多轮对话 |
| LLM 输出 | 单段落结构化结果 | RS1 流式 Markdown + Function Calling / RS2 分隔符协议编辑建议（无 JSON） |
| Marker 保护 | 是 | 否；对话和编辑都直接使用原始 LaTeX |
| 状态管理 | 无状态 | 有状态：按项目路径隔离持久化 |

### 双轮协议 + Function Calling

- **RS1**：用户发送消息 → SSE 流式 `chat_stream_with_tools` → 实时输出文本 + 检测工具调用。RS1 中 LLM 可发起 Function Calling 调用联网搜索、学术研究、项目文件浏览、段落编辑和请求澄清工具，工具结果回传 LLM 后继续流式输出最终回复
- **RS2**：用户在 RS1 回复上点击"生成编辑建议" → LLM 按分隔符协议（`<<<LATEX>>>...<<<END>>>`）输出修改后的完整 LaTeX 段落，可附带 `<<<REASON>>>...<<<END>>>` 说明 → 后端提取、校验并附挂 `EditOp`。LaTeX 不经过 JSON 字符串传输，反斜杠原样保留，从根上避免 JSON 转义静默损坏。`original` 由后端从段落原文填充，不再要求 LLM 回显

RS2 不直接写回 `.tex`，用户再点"应用到 .tex"才调用 `project_manager.apply_change`。

## Pydantic 模型

- `EditOp` — 编辑操作（file_path, paragraph_index, original_latex, new_latex, diff_text, applied）
- `ChatMessage` — 聊天消息（id, role, content, tokens, edit_op, compressed, snapshot, tool_calls, tool_call_id 等）
  - `role` 中 tool 角色用于持久化工具调用结果
  - `tool_calls`（仅 assistant 角色）：LLM 请求调用的工具列表（OpenAI tool_calls 格式）
  - `tool_call_id`（仅 tool 角色）：关联到触发该结果的工具调用 ID
  - `evidence_sources`：带 `evidence_id`、`document_id`、URL、heading path 和 block ID 的证据片段；不依赖完整网页缓存即可恢复
  - `page_reads`：页面读取摘要与 outline，包含已读取章节、抽取策略、缓存命中和类型化失败状态
  - `cited_evidence_ids` / `unverified_evidence_ids`：回答中的证据引用及校验结果
- RS2 无独立 LLM 输出模型：LLM 按分隔符协议输出 `<<<LATEX>>>...<<<END>>>`（必选）+ `<<<REASON>>>...<<<END>>>`（可选）纯文本块。提取出的修改段落经 `latex_json_guard.py` 的 `validate_latex_field(relaxed=True)` 宽松校验（放行 `\t` / `\r` 等合法 LaTeX 空白），失败时回喂 LLM 重试（最多 3 次）
- `ChatSendRequest` / `GenerateEditRequest` / `ApplyEditRequest` / `RenameSessionRequest` / `ConfirmToolRequest` / `CancelToolRequest` / `ClarifyRequest` / `CancelClarifyRequest` — 请求体

## 会话上下文管理 `context.py`

**磁盘持久化**：按项目路径 SHA256 前 16 位隔离存储，`session_id` 格式 `{scope}:{uuid}`。

**会话标题**：`list_sessions()` 在会话未手动重命名时，从第一条 user 消息提取前 40 个字符作为标题。空会话返回空字符串，由前端按当前用户语言显示“无标题”类兜底文案，后端不硬编码语言占位符。

**Token 压缩**：发送消息前检查累计 token 是否超过触发值，超限时先压缩旧历史，**再追加本轮 user 消息**，保证新指令始终以真实 user 角色进入 LLM。触发值默认按模型上下文窗口动态推导（`window - max_tokens - headroom - keep_recent - summary_budget`），`compression_threshold_tokens` 非 0 时作为固定覆盖。

压缩只作用于旧历史：`split_for_compression()` 从尾部向前保留最近若干轮（`compression_keep_recent_turns` 与 `compression_keep_recent_tokens` 取先满足者），切分点不会落在 assistant+tool_calls 与其 tool 结果对之间。被压缩的旧消息替换为一条 summary 消息并保存到 `snapshot` 字段（嵌套快照：删除外层 summary 可逐层还原），最近轮次原样保留。

摘要请求输入剔除 tool 原始结果与 assistant+tool_calls 对，只保留对话文本。摘要 token 数优先取真实 `usage.completion_tokens`，无 usage 时按字符降级估算。摘要 LLM 失败/超时/空摘要时回退为确定性裁剪（保留最近 N 轮 + 丢弃旧工具对 + 插入简短说明），不阻塞对话。

工具循环中单轮请求若触发上下文超限，会按序恢复：先裁剪旧工具对，仍超限则持久化已完成的历史工具轮次并压缩（不动当前正在执行的轮次），再重试同一轮。恢复失败时推送 SSE `error` 并保留用户消息（不静默删除）。

**上下文窗口探测**：`LLMClient.get_context_window()` 按“显式配置 `llm.context_window_tokens` → API `/models` 探测（`context_length` / `inputTokenLimit` / `outputTokenLimit` / `max_model_len` / `context_window` / `max_context_length`）→ 超限错误信息学习 → 内置模型表 → 默认 200000”的优先级获取窗口，结果按 `(profile_id, base_url, model)` 缓存（按模型档案隔离，防跨档案串味），`reset()`（配置热重载清池）时失效。`llm.models_probe_url` 可指定直连 vLLM / OpenAI 兼容 `/models` 地址，绕过不透传 `/models` 的网关。仅当探测地址与主服务同源时才携带 API key，跨域探测不发送密钥。

**手动压缩**：`POST /api/writing-chat/sessions/{id}/compact` 复用自动压缩逻辑（不追加 user 消息），前端会话页提供“压缩会话”按钮（streaming 或草稿会话中禁用；压缩成功后才重新拉取会话，消息过少/压缩失败时不发起二次请求）。

**删除消息**：summary 消息删除时还原 snapshot（恢复原始对话）；普通消息直接移除。

**上下文锚点**：`build_file_context_text` 构造 XML 信封格式的文件上下文（`<documents>` → `<document>` → `<document_content>` → `<paragraph index="N">`），包含源文件路径、段落总数和截断状态元数据。指定段落时以该段为锚点取 ±2 范围内的段落；未指定时采用 head/middle/tail 采样策略（各 3 段），确保全文覆盖面。截断时在 `<context_note>` 中告知模型上下文不完整，建议使用工具检索额外段落。

**工具消息持久化**：`append_tool_messages()` 将 Function Calling 工具调用循环产生的 assistant（含 tool_calls）和 tool 角色消息持久化到上下文，确保工具调用历史在会话间可追溯。tool 角色消息是内部上下文（仅用于回传 LLM），前端聊天区不直接展示（`isVisibleChatMessage` 过滤），工具活动由 `ToolStatusBar` 指示器呈现。

**多轮工具调用与内容一致性**：工具调用循环中，`round_idx > 0` 的每一轮在流开始前推送 `round_start`，前端收到后重置流式消息的累积文本。无工具调用的一轮内容即最终落盘回复，不再重复生成。工具调用循环上限为 6 轮（`_MAX_TOOL_ROUNDS`），支持"搜索 → 读页 → 再搜索"的 agentic 检索。仅当轮数用尽、最后一轮仍请求工具调用时，才在额外生成的最终回复流前再推送一次 `round_start`。工具证据按 `_MAX_TOOL_CONTEXT_CHARS`（60000 字符）滚动预算裁剪：只裁剪已收齐结果的旧"assistant tool_calls + tool 结果"对，始终保留当前轮（正在执行或刚完成的轮次），避免多轮搜索撑爆上下文或拆散同一轮的多工具调用。`parseThinking` 同时会剥离正文中残留的完整 thinking 块。web_search 结果（`search_results` / `search_query` / `deep_read_pages` / `rewritten_queries`）随 assistant 消息一并落盘，刷新后搜索卡片与实时展示一致。

**EvidenceBundle、引用校验与 untrusted 防护**：结构化 `open_page`、`read_page_sections` 和 `find_in_page` 返回的证据片段会按 `features.writing_chat.evidence_*` 预算去重、裁剪，并以稳定 `evidence_id` 组成 `EvidenceBundle`。System Prompt 要求 URL 使用工具返回的原值，并在事实句后附 `[EVIDENCE document_id#chunk_id]`。回复落盘前同时校验 Markdown URL 和 evidence ID，结果写入 assistant 消息并通过 `citation_note` SSE 事件提示。压缩摘要会保留证据附录和页面大纲。搜索结果与页面正文经 `format_for_llm()` 统一以 `UNTRUSTED DATA` 包装头注入，提示模型网页内容是数据而非指令。

**网络访问 v2 实施状态**：阶段 0-5 的契约、搜索/抽取/浏览器/学术能力与端到端证据链已经接入。`web_search.strategy` 独立控制搜索聚合，`web_search.page_reading.strategy` 控制读页链路：默认 `legacy` 使用兼容路径。`aggregate` / `structured` 分别启用 v2 搜索与结构化读取。浏览器回退默认关闭。学术全文结果会区分实际获得、缺失章节和抽取置信度，不会把摘要冒充全文。完整架构说明见 [网络访问架构](../writing-chat-web-access-architecture.md)，灰度和回滚步骤见 [运维说明](../web-access-operations.md)。

**消息转换**：`to_openai_messages()` 支持 tool 角色和 assistant+tool_calls 格式，将 `ChatMessage` 转换为 OpenAI messages API 格式。

## Service 核心方法

| 方法 | 功能 |
| --- | --- |
| `send(req, workspace_id)` | RS1 主聊天流：加载/创建会话 → 压缩判定（先压缩旧历史，保证本轮 user 以真实 user 角色进入）→ 追加消息 → 注入 Function Calling 工具 → chat_stream_with_tools（实时产出文本 + 检测工具调用）→ 工具调用循环（超限自动恢复）→ 后置流式 → SSE 推送 |
| `compact_session(session_id)` | 手动压缩：复用 `_maybe_compress`，不追加 user 消息 |
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
2. **联网搜索**（`SearchManager`）：`web_search` 返回标题/摘要/URL；`open_page` 读取大纲、相关证据或受限全文；结构化模式可继续调用 `read_page_sections`，`find_in_page` 基于 BM25/MMR 返回可追溯证据；legacy 模式保留原 DeepReader 行为
3. **项目浏览**（`ProjectToolsManager`）：`list_files` / `read_file_content` / `get_section_structure` / `search_across_files` — 浏览项目文件
4. **段落编辑**（`EditToolsManager`）：`edit_paragraph` / `undo_last_edit` — 直接修改 .tex 文件
5. **学术研究**（`ResearchManager`）：`search_scholarly_works` / `get_work_details` / `find_related_works` / `get_full_text_or_sections` / `search_research_data_resources` — 多 Provider 联邦检索、详情、引文追踪和 OA 定位；默认只启用 OpenAlex

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

**敏感操作保护**：`edit_paragraph` 和 `undo_last_edit` 是敏感操作。当 `features.function_calling.edit_tools.require_confirmation = true` 时，执行前推送 `tool_pending` SSE 事件，等待前端弹窗确认（超时 120 秒）。用户确认或取消后，结果回传 LLM 继续对话。

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
| POST | `/sessions/{id}/compact` | 手动压缩会话 |
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
| `token` | 流式文本块 |
| `tool_call` | LLM 发起工具调用 |
| `round_start` | 新一轮工具轮次（或最终回复流）开始，前端重置流式消息累积内容 |
| `compressed` | 上下文压缩完成（含 session_id） |
| `tool_status` | 工具执行中状态指示 |
| `tool_pending` | 敏感工具等待用户确认（含 pending_id、tool、args） |
| `tool_cancelled` | 工具被用户取消 |
| `clarification_request` | LLM 向用户提问，等待回复（含 clarify_id、question、options） |
| `tool_result` | 工具执行结果 |
| `tool_result.scholarly_results` | 学术检索或引文追踪的前端摘要，含稳定 ID、作者、年份、期刊、DOI、被引数和 OA 状态 |
| `captcha_challenge` | DDG 验证码挑战 |
| `tool_result.search_results` | 联网搜索结果（包含在 `tool_result` 事件中） |
| `citation_note` | 回复中存在未验证引用链接的警示 |
| `web_stage` | 搜索、抓取、抽取、章节读取和证据选择的阶段状态 |
| `done` | 流式结束（含 token 用量、session_id） |
| `error` | 错误信息 |

## 配置项

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| compression_threshold_tokens | 0 | 触发上下文压缩的 token 阈值；0=按模型窗口动态推导，非 0=固定覆盖 |
| compression_headroom_tokens | 30000 | 动态推导触发值时保留的窗口余量 |
| compression_keep_recent_tokens | 20000 | 压缩时原样保留的最近消息 token 预算 |
| compression_keep_recent_turns | 5 | 压缩时原样保留的最近轮数（与 token 预算取先满足者） |
| sessions_dir | data/writing_chat_sessions/ | 会话持久化目录（多用户模式 P6 按当前用户隔离到 `<users_root>/<user_id>/writing_chat_sessions/`，单用户行为不变） |
| custom_prompts_dir | data/prompts/writing_chat/ | 助手提示词目录 |
| summary_temperature | 0.2 | 摘要 LLM 温度 |
| summary_token_budget | 6000 | 摘要最大 token 数（旧配置 `summary_max_tokens` 自动迁移并告警） |
| summary_model | 空 | 独立摘要模型；为空时跟随主模型 |
| evidence_bundle_enabled | true | 是否持久化结构化网页证据并校验证据 ID |
| evidence_max_chars | 24000 | 单轮证据字符预算 |
| evidence_max_items | 12 | 单轮证据片段上限 |
| llm.context_window_tokens | 0 | 模型上下文窗口；0=自动探测，非 0 跳过探测直接使用 |
| llm.models_probe_url | 空 | 可选的 /models 直连探测地址，绕过不透传 /models 的网关 |
| features.function_calling.scholarly_research.enabled | false | 学术研究工具总开关 |
| features.function_calling.scholarly_research.max_results | 20 | 单次工具返回结果上限 |
| features.function_calling.scholarly_research.timeout_seconds | 30 | 学术 Provider 请求超时 |
| features.function_calling.scholarly_research.primary_source | openalex | 兼容保留的主数据源标识；当前联邦搜索会调用所有启用的 Provider，不根据该字段改变顺序 |
| features.function_calling.scholarly_research.providers.*.enabled | 依 Provider 而定 | 各学术数据源开关；默认启用 OpenAlex，其余默认关闭 |
| features.function_calling.scholarly_research.providers.*.api_key | 空 | 各 Provider 的可选 API Key；设置接口返回脱敏值 |
| features.function_calling.web_search.page_reading.strategy | legacy | `legacy` 使用 DeepReader；`structured` 启用结构化抓取、抽取、缓存与章节读取 |
| features.function_calling.scholarly_research.ranking_mode | legacy | `legacy` 保留兼容排序；`rrf` 启用跨 Provider RRF |
| features.function_calling.scholarly_research.full_text_enabled | false | 是否从开放获取页面结构化读取实际章节 |
| features.function_calling.web_search.page_reading.cache_* | 见 config.example.json | 原始/抽取缓存目录、开关、TTL 与单条目大小限制 |
| features.function_calling.web_search.page_reading.max_response_mb | 10 | 单页 HTTP 响应大小硬上限 |
| features.function_calling.web_search.page_reading.extractor_version | html-v1 | 抽取缓存版本键；变更后旧抽取缓存自动失效 |
| features.function_calling.web_search.page_reading.quality_threshold | 0.35 | 低于阈值时返回 `low_quality_extraction` 部分状态 |

## 前端 `useWritingChat` hook

封装：项目恢复、会话管理、SSE 流式发送、深度思考解析（parseThinking + processLoadedMessages）、生成编辑、应用编辑（支持手动微调 customText）、删除消息（summary 消息还原快照）、工具调用确认、澄清请求、助手管理。

**工具确认流程**：

- `pendingTool` state 存储待确认的工具调用
- `confirmPendingTool(modifiedArgs)` 调用 `/confirm-tool` 接口，可选修改工具参数后确认。受 `canWrite` 守卫（viewer 只读，与 `applyEdit` 同权，后端 403 兜底）
- `cancelPendingTool()` 调用 `/cancel-tool` 接口取消

**澄清请求流程**：

- `pendingClarification` state 存储 LLM 的待回复问题（含 clarify_id、question、options）
- `submitClarification(answer)` 调用 `/clarify` 接口提交用户回答
- `cancelClarification()` 调用 `/cancel-clarify` 接口取消

SSE 事件处理器处理 `tool_pending`、`tool_cancelled`、`clarification_request`、`web_stage`，并把 `scholarly_results`、证据片段和页面读取摘要存入 assistant 消息。`MessageBubble` 使用折叠卡展示学术记录、页面 outline、错误分类、证据片段和 evidence ID。可将未读章节生成下一条读取请求。

**会话重载一致性**：页面刷新水合后，`useWritingChat` 清空持久化的 `writingChatMessages` 并以后端会话 JSON 为准重新拉取当前会话（草稿会话除外），避免 localStorage 中的陈旧消息与磁盘 JSON 不一致；删除消息后的重载同样经过 `processLoadedMessages` 解析 thinking。

**WritingChatPage 工具弹窗**：

- `ToolConfirmDialog` — 敏感工具确认，使用 shadcn/ui Dialog，根据工具类型显示对应图标和文案
- `ClarifyDialog` — 澄清请求弹窗，展示问题、可选项 pill 按钮和自由输入文本框

**子 hooks**：`writing-chat/sessions.js`（会话管理）、`writing-chat/assistants.js`（助手管理）、`writing-chat/captcha.js`（验证码处理）、`writing-chat/sse.js`（SSE 事件处理）、`writing-chat/utils.js`（工具函数）。
