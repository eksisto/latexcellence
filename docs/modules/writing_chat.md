# 写作助手模块 `writing_chat/`

## 设计概述

写作助手 Chat 提供与 AI 的多轮对话界面，帮助用户讨论 LaTeX 文档内容、获取写作建议、并直接修改指定段落。

### 核心差异（与 typo_check 等逐段检查模块的关键区别）

| 维度 | typo_check / expression_opt | writing_chat |
| ---- | -------------------------- | ------------ |
| 交互模式 | 单向：用户触发 → LLM 检查 → 返回结果 | 双向：多轮对话，支持追问和深入讨论 |
| LLM 输出 | 每次返回单段落结构化结果 | 双轮协议：RS1 流式 Markdown 回复 / RS2 结构化编辑建议 |
| Marker 保护 | 是（输入与输出均带 marker） | 分层处理：纯对话无 marker；编辑操作走 marker pipeline |
| 状态管理 | 无状态（每次请求独立） | 有状态：会话按项目路径隔离持久化 |
| 前端模式 | 复用 `useParagraphCheck` hook | 独立 Chat 页面 + `useWritingChat` hook |

### 分层 Marker 策略（与 AGENTS.md 最高优先级约束的调和）

AGENTS.md 规定"与 LLM 交互时 LaTeX 结构必须替换为 marker"。本模块按场景分层处理：

- **纯对话 (RS1)**：通过 `LatexParser.strip_to_plain_text` 把段落剥离成纯文本（**不带 marker**）喂给 LLM，方便 LLM 理解论文内容进行自然讨论。
- **编辑操作 (RS2)**：复用 `expression_opt` 的 marker pipeline：`strip_to_plain_text` 得到 marker + 纯文本 → LLM 只能修改纯文本部分 → `restore_from_markers` 还原为完整 LaTeX 源码 → `project_manager.apply_change` 写回，确保 `\textbf`/`\cite`/`\ref` 等结构不被破坏。

### 双轮协议

| 轮 | 触发 | 协议 | 输出 |
| -- | ---- | ---- | ---- |
| RS1 | 用户发送消息 | SSE 流式 `chat_stream_with_usage` | Markdown 自由回复 |
| RS2 | 用户在 RS1 回复上点击"生成编辑建议"按钮 | 结构化 JSON `retry_with_validation` | `LlmEditResult` → `EditOp` |

RS2 不会直接写回 `.tex`，仅生成 `EditOp` 附挂在 assistant 消息上；用户再点"应用到 .tex"按钮才会调用 `project_manager.apply_change`。

## `models.py` — Pydantic 模型

```python
class EditOp(BaseModel):
    file_path: str
    paragraph_index: int
    original_latex: str   # 原始段落 LaTeX 源码
    new_latex: str        # 还原 marker 后的新 LaTeX 源码
    diff_text: str = ""   # 后端生成的 unified diff（基于纯文本）
    applied: bool = False

class ChatMessage(BaseModel):
    id: str
    role: str              # "user" / "assistant" / "summary"
    content: str
    tokens: int = 0
    edit_op: Optional[EditOp] = None
    edit_reason: str = ""  # RS2 生成编辑建议时的 reason 文本
    compressed: bool = False
    snapshot: Optional[list["ChatMessage"]] = None   # 压缩前的原始消息快照
    referenced_file: Optional[str] = None
    referenced_paragraph: Optional[int] = None
    created_at: float = 0.0

class LlmEditResult(BaseModel):       # RS2 LLM 输出 schema
    paragraph_index: int
    original: str
    optimized: str
    reason: str = ""

class ChatSendRequest(BaseModel):     # RS1 请求
    session_id: Optional[str] = None
    file_path: str
    paragraph_index: Optional[int] = None
    user_message: str
    assistant_prompt: Optional[str] = None  # 当前激活助手的提示词内容

class GenerateEditRequest(BaseModel): # RS2 请求
    session_id: str
    file_path: str
    paragraph_index: int
    instruction: str

class ApplyEditRequest(BaseModel):     # 应用编辑请求
    session_id: str
    edit_message_id: str
    new_latex: Optional[str] = None   # 用户手动编辑后的内容（可选，优先级高于 edit_op.new_latex）

class RenameSessionRequest(BaseModel): # 会话重命名
    session_id: str
    title: str

class LlmSummaryResult(BaseModel):    # 压缩摘要 LLM JSON 输出
    summary: str
```

## `context.py` — `ConversationContext` 会话上下文管理器

### 磁盘持久化策略

- 会话按**项目路径**隔离存储：`{sessions_dir}/{scope}/{uuid}.json`
- `scope` = 项目根目录绝对路径的 SHA256 哈希前 16 位（同一路径→相同 scope→共享会话）
- `session_id` 格式：`{scope}:{uuid}`，加载/删除时从 id 中切出 scope 定位文件
- 无项目时使用 `_noproject` 作为兜底 scope
- 加载用 `safe_read` + `ChatMessage.model_validate` 重建（包括嵌套 `snapshot` 列表）
- `list_sessions(scope)` 列出指定 scope 下的会话，`scope=""` 时列出全部；按 `last_updated_at` 降序
- `rename(new_title)` 更新会话标题并落盘，`list_sessions()` 优先返回 `title` 字段，缺失时从首条用户消息截取

### Token 计数与压缩

- `cumulative_tokens` 跟踪"当前 LLM 调用实际消耗的 total_tokens"（来自 LLM `usage.total_tokens`），随每次发送更新为最新值
- 每次发送前若 `cumulative_tokens >= compression_threshold_tokens`（默认 200000），触发 `_maybe_compress`：把凡包含 ≥4 条消息的历史给 LLM 生成 `LlmSummaryResult.summary`，`replace_with_summary` 用一条 `summary` 消息（role="summary", compressed=True）替换全部历史，被替换的原消息深拷贝保存到 `snapshot` 字段
- `_maybe_compress` 失败时不影响主聊天流，仅记日志

### 撤回 / 删除

- `undo_message(id)`：若该消息是 summary，则丢弃 summary 还原 `snapshot` 中的原始消息；否则等同 `delete_message`
- `delete_message(id)`：从 messages 移除，同步下调 `cumulative_tokens`（不低于 0），summary 消息删除时其 snapshot 一并丢弃

### 上下文锚点

`ConversationContext.build_file_context_text(file_path, paragraph_index, workspace_id)` 通过 `get_project_manager_for_workspace` 与 `LatexParser.strip_to_plain_text` 构造纯文本上下文：

- `paragraph_index=None`：全文（最多 12 段，超 12 段追加 `... (N paragraphs omitted) ...`）
- 指定 `paragraph_index`：当前段及前后各 1 段（共 3 段）

## `service.py` — `WritingChatService` 核心业务逻辑

**单例**：`get_writing_chat_service()` 全局唯一实例。

### 核心方法

| 方法 | 功能 |
| ---- | ---- |
| `send(req, workspace_id)` (async iterator) | RS1 主聊天流：加载/创建会话（新会话自动绑定当前项目 scope）→ 追加 user 消息 → 压缩判定 → `chat_stream_with_usage` → SSE 推送 `token`/`compressed`/`done`/`error` 事件 → 落盘 assistant 消息（含 usage tokens）；`assistant_prompt` 注入系统提示词，不写入用户消息体 |
| `generate_edit(req, workspace_id)` (awaitable) | RS2 编辑流：`strip_to_plain_text` → marker 一致性校验通过则 `restore_from_markers` 还原 → 构造 unified diff → 附挂 `EditOp` 到最近一条 assistant 消息 → 落盘 |
| `apply_edit(req, workspace_id)` | 调用 `get_project_manager_for_workspace(workspace_id).apply_change(...)` → 把消息的 `edit_op.applied` 置 True 持久化；若请求携带 `new_latex`（用户手动编辑内容），优先使用该值而非 `edit_op.new_latex` |
| `delete_message(req)` | 委托给 `ConversationContext.delete_message` 并落盘 |
| `undo_message(req)` | 委托给 `ConversationContext.undo_message` 并落盘 |
| `rename_session(session_id, title)` | 委托给 `ConversationContext.rename(title)` 并落盘 |
| `list_sessions(workspace_id)` / `load_session(id)` / `delete_session(id)` | 会话 CRUD；`list_sessions` 通过 workspace_id 获取 scope 后按项目过滤 |

### System Prompt 分三类

- `_CHAT_SYSTEM_PROMPT`：定义写作助手角色，注入文件段落上下文锚点（纯文本，无 marker）
- `_EDIT_SYSTEM_PROMPT`：与 expression_opt 相同的 marker 不可变强约束 + 指令
- `_SUMMARY_SYSTEM_PROMPT`：压缩摘要任务，要求保留关键决策/UI 引用/未决编辑

### SSE 事件结构（`send` 产出）

```json
{ "type": "token", "content": "..." }
{ "type": "compressed", "session_id": "..." }   // 本轮触发压缩
{ "type": "done", "session_id": "...", "message_id": "...", "tokens": 12345 }
{ "type": "error", "message": "..." }
```

## `router.py` — API 路由

Prefix: `/api/writing-chat`, Tag: `writing_chat`

| 方法 | 路径 | 功能 |
| ---- | ---- | ---- |
| POST | `/send` | SSE 流式发送消息，逐 token 推送；`assistant_prompt` 注入系统提示词 |
| POST | `/generate-edit` | 基于 RS1 指令生成 RS2 结构化编辑建议（含 diff） |
| POST | `/apply-edit` | 把某条 assistant 消息附带的 edit_op 写回 .tex |
| POST | `/message/delete` | 删除指定消息 |
| POST | `/message/undo` | 撤回消息（summary 还原 snapshot；普通等同删除） |
| `PATCH` | `/sessions/{session_id}/rename` | 重命名会话标题 |
| GET | `/sessions` | 列出当前项目 scope 下的会话（需 `workspace_id`） |
| GET | `/sessions/{id}` | 加载单个会话完整 messages |
| `DELETE` | `/sessions/{id}` | 删除会话文件 |
| GET | `/assistants` | 列出所有助手提示词 |
| GET | `/assistants/{name}` | 加载指定助手提示词内容 |
| POST | `/assistants` | 创建或更新助手提示词 |
| `DELETE` | `/assistants/{name}` | 删除助手提示词 |
| POST | `/assistants/active` | 设置当前激活的助手提示词 |

## `prompt_manager.py` — 助手提示词管理

存储策略与 `expression_opt` 相同：`{custom_prompts_dir}/{name}.txt` 单文件存储，`_active.txt` 记录当前激活助手名称。通过 `WritingChatConfig.custom_prompts_dir`（默认 `prompts/writing_chat/`）配置存放目录。

| 方法 | 功能 |
| ---- | ---- |
| `list_prompts()` | 列出所有助手提示词（`.txt` 文件） |
| `load_prompt(name)` | 加载指定提示词内容 |
| `save_prompt(name, content)` | 保存/覆盖提示词 |
| `delete_prompt(name)` | 删除提示词 |
| `set_active(name)` | 设置当前激活的助手提示词（写入 `_active.txt`） |
| `get_active_prompt_content()` | 获取激活提示词的内容 |

## `core/llm_client.py` 扩展

新增 `chat_stream_with_usage(messages, ...)`：返回 `AsyncIterator[StreamChunk]`，每个 `StreamChunk` 含 `content` 或 `usage`（互斥）。末次 chunk 携带 `UsageInfo` 用于上层累计。

底层用 `stream_options={"include_usage": True}` 让 OpenAI 兼容 API 在流末尾推一个仅含 usage 的 chunk；若第三方 base_url 不支持该参数，捕获异常后降级（usage=None，service 端通过本轮 assistant 消息累计 fallback）。

新增数据类：

```python
@dataclass class UsageInfo: prompt_tokens, completion_tokens, total_tokens
@dataclass class StreamChunk: content: Optional[str], usage: Optional[UsageInfo]
```

## 配置项

`config.json` → `features.writing_chat`：

| 字段 | 默认值 | 说明 |
| ---- | ------ | ---- |
| `enabled` | `true` | 功能开关 |
| `compression_threshold_tokens` | `200000` | 触发上下文压缩的累计 token 阈值 |
| `sessions_dir` | `"writing_chat_sessions/"` | 会话磁盘持久化目录（相对项目根目录） |
| `custom_prompts_dir` | `"prompts/writing_chat/"` | 助手提示词存放目录（相对项目根目录） |
| `summary_temperature` | `0.2` | 生成压缩摘要时的 LLM 温度 |
| `summary_max_tokens` | `1024` | 压缩摘要最大生成 token 数 |

## 前端组件 `WritingChatPage`

- 路由：`/writing-chat`
- 三列布局：左侧会话列表 + 中间文件/段落选择 + 右侧聊天主区
- 消息渲染：`MarkdownRenderer` 组件基于 `react-markdown` + `remark-gfm` + `remark-math` + `rehype-katex`，支持 GitHub 风格 Markdown、数学公式（KaTeX 主题）
- 深度思考折叠框：LLM 输出 `<thinking>...</thinking>` 标签时，前端 `parseThinking()` 解析为 `thinkingContent` + `displayContent`，渲染为可折叠的"深度思考"框（`<details>` 元素）；流式接收时保持展开并显示 spinner，收到 `</thinking>` 后自动折叠；页面刷新时 `processLoadedMessages()` 从 `content` 字段重新提取，保证刷新后思考框不丢失
- 助手消息 hover 出现"生成编辑建议"按钮（需选了段落且该消息无 edit_op）和"撤回"按钮（summary 消息为"撤回还原"）；复制按钮 hover 显示
- 用户消息 hover 出现"撤回"按钮
- 编辑建议卡片：左原文右新文本（LaTeX 源码）+ 可展开的 unified diff + "手动编辑"切换（替换新文本为 textarea）+ "应用到 .tex"按钮（手动编辑时通过 `new_latex` 字段传递编辑后的 customText 给后端）
- 支持任意消息"删除"精简上下文
- 会话列表：双击会话名或点击 pencil 图标重命名（`PATCH /sessions/{id}/rename`）；hover 显示 pencil + trash 图标
- 助手选择器：选择/新建/删除助手提示词（`/assistants` CRUD）；激活助手的提示词通过 `assistant_prompt` 字段注入系统提示词，不写入用户消息体
- 状态持久化：`writingChatCurrentSessionId` 加入 Zustand persist `partialize`（仅保存 session_id 引用，消息数据均由后端磁盘保存）
- 快捷键：Enter 发送（Shift+Enter 换行）

## 前端 `useWritingChat` hook

封装：项目恢复、会话切换/新建/删除/重命名、文件段落切换、消息发送（fetch streaming + 手动 SSE 解析）、深度思考解析（`parseThinking` + `processLoadedMessages`）、生成编辑、应用编辑（支持手动微调 customText，通过 `new_latex` 字段传递给后端确保写入用户编辑内容）、删除/撤回消息、助手管理（列表/新建/删除/激活）、压缩提示与状态同步。页面只传 store slice 配置，禁止在页面重复实现（遵循 AGENTS.md）。

**子 hooks 结构**：
- `writing-chat/sessions.js` — 会话管理逻辑（selectSession / newSession / deleteSession / renameSession / refreshSessions）
- `writing-chat/assistants.js` — 助手管理逻辑（setAssistantActive / saveAssistant / deleteAssistant）
- `writing-chat/captcha.js` — DDG 验证码处理（submitCaptcha / dismissCaptcha）
- `writing-chat/sse.js` — SSE 事件处理器工厂函数（createSSEEventHandler）
- `writing-chat/utils.js` — 工具函数（parseThinking / processLoadedMessages / isDraftSession / uid）

### `parseThinking()` 流式解析

LLM 流式输出中可能包含 `<thinking>...</thinking>` 标签。`parseThinking(rawText)` 每收到一个 token chunk 即对累积文本做正则匹配：
- `<thinking>` 已出现但 `</thinking>` 未到：设置 `thinkingOpen=true, thinkingClosed=false`，UI 展示 spinner
- `</thinking>` 已到达：设置 `thinkingClosed=true`，UI 折叠思考框，`displayContent` 为标签外的正文

### `processLoadedMessages()` 页面刷新恢复

从后端加载会话消息时，消息的 `content` 字段包含原始 `<thinking>` 标签文本，但 `thinkingContent`/`displayContent` 未设置。`processLoadedMessages()` 对每条 assistant 消息调用 `parseThinking` 提取思考内容并设置 `thinkingClosed: true`（默认折叠），然后通过 `setMessages` 触发 Zustand 状态更新以重渲染。
