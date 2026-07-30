"""
写作助手 Chat 模块的 Pydantic 数据模型。

设计要点：
    - ChatMessage：单条对话消息，含可选 edit_op（编辑操作）与 snapshot（被压缩前的原消息列表快照）
    - edit_op 用于"双轮协议"中的第二轮结构化请求结果
    - snapshot 用于支持撤回压缩消息时还原原始对话

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

    当 assistant 回复触发"生成编辑"第二轮请求后，LLM 输出的 LlmEditResult
    会被封装为 EditOp 存储到该 assistant 消息上；前端待用户点击"应用"
    后才调用 project_manager.apply_change 写回源文件。
    """

    file_path: str
    paragraph_index: int
    original_latex: str
    new_latex: str
    diff_text: str = ""
    applied: bool = False


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
                  用于撤回该 summary 时还原上下文
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


ChatMessage.model_rebuild()


# ── 第二轮结构化 LLM 输出 schema ────────────────────────────────────────────


class LlmEditResult(BaseModel):
    """
    生成编辑时 LLM 的 JSON 输出 schema。

    LLM 直接操作原始 LaTeX 源码（writing_chat 为特例，不用 marker 剥离），
    optimized 字段返回修改后的完整 LaTeX 段落。
    """

    paragraph_index: int
    original: str
    optimized: str
    reason: str = ""


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
    """删除会话中的某条消息。"""

    session_id: str
    message_id: str


class UndoMessageRequest(BaseModel):
    """撤回某条消息（summary 消息则还原快照）。"""

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


# ── LLM 压缩摘要输出 schema ─────────────────────────────────────────────────


class LlmSummaryResult(BaseModel):
    """压缩摘要 LLM JSON 输出 schema。"""

    summary: str


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
