"""
写作助手模块的 Pydantic 数据模型。

设计要点：
    - ChatMessage：单条对话消息，含可选 edit_op（编辑操作）与 snapshot（被压缩前的原消息列表快照）
    - edit_op 用于"双轮协议"中的第二轮结构化请求结果
    - snapshot 用于支持删除压缩消息时还原原始对话

模型分两类：
    1. 业务持久化模型（ChatMessage / EditOp）——会被 JSON 序列化落盘
    2. API 请求/响应模型——router 层使用，校验入参
"""

from typing import Any

from pydantic import BaseModel, Field

# ── 持久化的消息数据 ────────────────────────────────────────────────────────


class EditOp(BaseModel):
    """
    编辑操作结构。

    当 assistant 回复触发"生成编辑"第二轮请求后，LLM 输出的修改结果
    （分隔符协议提取的 new_latex + reason）会被封装为 EditOp 存储到
    该 assistant 消息上；前端待用户点击"应用"后才调用
    project_manager.apply_change 写回源文件。
    """

    file_path: str
    paragraph_index: int
    original_latex: str
    new_latex: str
    diff_text: str = ""
    applied: bool = False


class EditorSuggestion(BaseModel):
    """
    编辑器助手建议（editor-plan §7.2，阶段四）。

    从 assistant 回复的 Markdown 围栏代码块中提取：
        - kind="snippet"：```latex 围栏片段，供前端"插入光标处 / 替换选区"
        - kind="diff"：```diff 围栏 unified diff，供前端逐卡审阅应用到缓冲区

    建议只随消息持久化用于会话历史展示；应用动作由前端改 CM6 缓冲区，
    不经后端落盘（方案 §5.2）。
    """

    kind: str  # "snippet" | "diff"
    language: str = ""
    content: str
    index: int = 0


class ScholarlyResultSummary(BaseModel):
    """持久化在 assistant 消息上的学术结果摘要。"""

    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int = 0
    journal: str = ""
    citation_count: int = 0
    doi: str = ""
    is_oa: bool = False
    oa_url: str = ""
    work_type: str = ""


class SearchResultSummary(BaseModel):
    """持久化在 assistant 消息上的 web_search 结果摘要（与 tool_result 事件同构）。"""

    index: int = 0
    title: str = ""
    snippet: str = ""
    url: str = ""


class EvidenceSourceSummary(BaseModel):
    """可独立于网页缓存持久化的证据片段。"""

    evidence_id: str
    document_id: str = ""
    source_url: str
    title: str = ""
    heading_path: list[str] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)
    text: str = ""
    retrieval_score: float = 0.0


class PageSectionSummary(BaseModel):
    """页面大纲中的单个章节。"""

    section_id: str
    title: str = ""
    level: int = 1
    heading_path: list[str] = Field(default_factory=list)


class PageReadSummary(BaseModel):
    """Writing Chat 中一次页面读取的可恢复摘要。"""

    url: str
    title: str = ""
    document_id: str = ""
    outline: list[PageSectionSummary] = Field(default_factory=list)
    read_section_ids: list[str] = Field(default_factory=list)
    extraction_strategy: str = ""
    cache_hit: bool = False
    partial: bool = False
    error_code: str = ""
    error_message: str = ""


class ChatMessage(BaseModel):
    """
    单条对话消息。

    字段说明：
        role: "user"/"assistant"/"summary"/"tool"
            - user: 用户输入
            - assistant: LLM 回复（可能带有 tool_calls）
            - summary: 上下文压缩生成的摘要消息（compressed=True）
            - tool: 工具调用结果
        content: 消息文本（user/assistant 为 Markdown，summary 为压缩后摘要，
                 tool 为工具执行结果文本）
        tokens: 该消息贡献给累计 token 计数的近似值（首次发送时填入；
                summary 的 tokens = 原压缩快照的 tokens 总和的替代估计）
        edit_op: 仅 assistant 消息可能携带；表示该回复附带的编辑操作建议
        compressed: 是否是压缩产生的 summary 消息
        snapshot: 若 compressed=True，保存压缩前被替换的原始消息列表，
                  用于删除该 summary 时还原上下文
        referenced_file / referenced_paragraph: 用户在该消息中选择的上下文锚点
        tool_calls: 仅 assistant 消息携带，LLM 请求调用的工具列表
                    （OpenAI tool_calls 格式，list[dict]）
        tool_call_id: 仅 tool 角色消息携带，关联到触发该结果的工具调用 ID
    """

    id: str
    role: str
    content: str = ""
    tokens: int = 0
    edit_op: EditOp | None = None
    compressed: bool = False
    snapshot: list["ChatMessage"] | None = None
    referenced_file: str | None = None
    referenced_paragraph: int | None = None
    created_at: float = 0.0
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    scholarly_results: list[ScholarlyResultSummary] = Field(default_factory=list)
    scholarly_query: str = ""
    scholarly_total: int = 0
    search_results: list[SearchResultSummary] = Field(default_factory=list)
    search_query: str = ""
    deep_read_pages: int = 0
    rewritten_queries: list[str] = Field(default_factory=list)
    citation_note: str = ""  # 引用校验备注（存在未在搜索结果中出现的链接时非空）
    unverified_citations: list[str] = Field(default_factory=list)
    evidence_sources: list[EvidenceSourceSummary] = Field(default_factory=list)
    page_reads: list[PageReadSummary] = Field(default_factory=list)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    unverified_evidence_ids: list[str] = Field(default_factory=list)
    # 编辑器助手建议（仅 editor/send 产生的 assistant 消息携带）
    suggestions: list[EditorSuggestion] = Field(default_factory=list)
    # 消息级建议应用标记：用户在前端把该消息的任一建议应用到缓冲区后置位
    suggestion_applied: bool = False


ChatMessage.model_rebuild()

# ── API 请求/响应模型 ────────────────────────────────────────────────────────


class ChatSendRequest(BaseModel):
    """发起一次流式对话请求。"""

    session_id: str | None = None
    file_path: str
    paragraph_index: int | None = None
    user_message: str
    assistant_prompt: str | None = None
    web_search_enabled: bool | None = (
        None  # None=跟随配置，True=强制启用，False=强制禁用
    )
    language: str = "zh"  # 前端 UI 语言，用于引导 LLM 回复语言


class EditorSendRequest(BaseModel):
    """
    编辑器助手流式对话请求（editor-plan §5.2，阶段四）。

    携带编辑器上下文：当前打开文件、CM6 缓冲区全文（含未保存修改）、
    光标行号与选区。上下文注入量由服务端按 §7.3 自适应裁剪；
    include_full_file 为大文件时用户手动"把全文纳入本次提问"开关。
    """

    session_id: str | None = None
    file_path: str = ""
    buffer: str = ""
    cursor_line: int | None = None
    selection: str = ""
    include_full_file: bool = False
    user_message: str
    language: str = "zh"


class ApplySuggestionRequest(BaseModel):
    """记录编辑器建议的应用结果（消息级标记，editor-plan §5.2）。"""

    session_id: str
    message_id: str


class GenerateEditRequest(BaseModel):
    """基于已有对话中的某条 user 消息生成"编辑操作"建议。"""

    session_id: str
    file_path: str
    paragraph_index: int
    instruction: str = Field(..., description="编辑指令原文（通常取最新 user 消息）")
    language: str = "zh"


class ApplyEditRequest(BaseModel):
    """应用某条 assistant 消息携带的 edit_op。"""

    session_id: str
    edit_message_id: str
    new_latex: str | None = None
    expected_md5: str | None = None


class DeleteMessageRequest(BaseModel):
    """删除会话中的某条消息（summary 消息则还原其快照）。"""

    session_id: str
    message_id: str


class SessionMeta(BaseModel):
    """会话元信息（列表用）。"""

    id: str
    created_at: float
    last_updated_at: float
    message_count: int
    cumulative_tokens: int
    file_path: str | None = None
    title: str = ""


class SessionFull(BaseModel):
    """完整会话数据（含全部 messages）。"""

    id: str
    created_at: float
    last_updated_at: float
    messages: list[ChatMessage]
    cumulative_tokens: int
    file_path: str | None = None


# ── SSE 事件 payload 结构（仅供 service 构造时的类型参考） ──────────────────


class SSEEvent(BaseModel):
    """前端的 SSE 事件载荷。type 决定前端如何处理该事件。"""

    type: str
    data: Any | None = None


# ── 助手提示词管理 ────────────────────────────────────────────────────────


class AssistantSaveRequest(BaseModel):
    """保存（创建或更新）助手提示词。"""

    name: str
    content: str


class AssistantSetActiveRequest(BaseModel):
    """设置当前激活的助手。"""

    name: str = Field(default="", description="空字符串表示取消激活")


class RenameSessionRequest(BaseModel):
    """重命名会话。"""

    session_id: str
    title: str


class ConfirmToolRequest(BaseModel):
    """确认待定的工具调用。"""

    pending_id: str
    modified_args: dict[str, Any] | None = None


class CancelToolRequest(BaseModel):
    """取消待定的工具调用。"""

    pending_id: str


class ClarifyRequest(BaseModel):
    """用户对 LLM 澄清请求的回复。"""

    clarify_id: str
    answer: str


class CancelClarifyRequest(BaseModel):
    """用户取消澄清请求。"""

    clarify_id: str
