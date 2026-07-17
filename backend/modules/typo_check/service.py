"""
文本校对业务逻辑。

负责编排以下流程：
    1. 通过 project_manager 提取段落
    2. 通过 latex_parser 剥离 LaTeX 命令为纯文本
    3. 调用 LLM 逐段检查错字（含 Pydantic 校验 + 重试）
    4. 审核确认后还原 marker 并写回源文件

支持 \\input 解析：
    当 resolve_input=True 时，递归展开主文件中的 \\input/\\include 引用，
    将所有子文件纳入处理范围，段落按文件分组返回。
"""

from typing import Any, Optional

from pydantic import BaseModel

from backend.core.config import get_config
from backend.core.base_paragraph_check import BaseParagraphCheckService


# ── Pydantic models for LLM response schema ────────────────────────────────

class TypoChangeItem(BaseModel):
    """LLM 返回的单条修改建议"""
    type: str = "typo"
    original: str
    corrected: str
    position: Optional[int] = None
    explanation: str = ""


class TypoCheckResult(BaseModel):
    """LLM 错字检查输出——对应 DEVELOPMENT.md §2.1 JSON 协议"""
    paragraph_index: int
    original: str
    corrected: str
    changes: list[TypoChangeItem] = []


# ── system prompt template ─────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a LaTeX document proofreader. "
    "Your task is to find and fix typos, spelling errors, and grammar mistakes "
    "in the given text.\n\n"
    "CRITICAL RULES — VIOLATING THESE MAKES THE OUTPUT UNUSABLE:\n"
    "1. The text contains markers like __CMD_0001__ or __MATH_0001__ "
    "which represent LaTeX commands and math formulas. "
    "These markers are NOT part of human-readable text.\n"
    "2. ABSOLUTELY DO NOT modify, delete, split, merge, or reorder any marker. "
    "Every marker must appear in the corrected text EXACTLY as in the original.\n"
    "3. Only modify plain text portions (words, sentences, punctuation).\n"
    "4. If there are no errors, the corrected field must be byte-identical "
    "to the original field.\n"
    "5. Respond with JSON only, no markdown fences, no extra text.\n\n"
    "JSON format (strictly follow this schema):\n"
    "{\n"
    '  "paragraph_index": <int>,\n'
    '  "original": "<the exact original text with all markers unchanged>",\n'
    '  "corrected": "<the corrected text with all markers unchanged>",\n'
    '  "changes": [\n'
    '    {\n'
    '      "type": "typo",\n'
    '      "original": "<incorrect text fragment>",\n'
    '      "corrected": "<correct text fragment>",\n'
    '      "position": <optional int, char offset in original>,\n'
    '      "explanation": "<brief reason in English>"\n'
    '    }\n'
    '  ]\n'
    "}"
)


class TypoCheckService(BaseParagraphCheckService):
    """
    错字检查服务。

    用法：
        svc = TypoCheckService()
        preview = svc.preview_paragraphs("main.tex")
        results = await svc.check_typos("main.tex")
        svc.apply_changes("main.tex", [{"paragraph_index": 0, "new_text": "..."}])

    支持 \\input 解析：
        preview = svc.preview_paragraphs("main.tex", resolve_input=True)
        results = await svc.check_typos_stream("main.tex", resolve_input=True)
    """

    def __init__(self):
        super().__init__("typo_check")

    # ── 公共 API（保持 router.py 兼容）─────────────────────────────────────

    async def check_typos(
        self,
        file_path: str,
        paragraph_indices: Optional[list[int]] = None,
        resolve_input: bool = False,
    ) -> dict[str, Any]:
        """对段落进行错字检查（非流式）。"""
        return await self._run_batch_check(
            file_path, paragraph_indices, resolve_input
        )

    async def check_typos_stream(
        self, file_path: str, resolve_input: bool = False
    ):
        """错字检查流式版本（SSE）。"""
        async for event in self._run_stream_check(file_path, resolve_input):
            yield event

    # ── Hook 实现 ───────────────────────────────────────────────────────────

    def _get_system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    def _build_user_content(self, text: str) -> str:
        return (
            f"Please check the following text for typos and errors.\n\n"
            f"TEXT:\n{text}"
        )

    def _get_model_cls(self):
        return TypoCheckResult

    def _get_llm_params(self) -> dict[str, Any]:
        return {"temperature": 0.3, "max_tokens": 2048}

    def _get_batch_size(self) -> int:
        return get_config().features.typo_check.batch_size

    def _build_success_result(
        self,
        idx: int,
        validated,
        original_text: str,
        file_path: str,
        plain_text: str,
    ) -> dict[str, Any]:
        return {
            "paragraph_index": idx,
            "original": validated.original,
            "corrected": validated.corrected,
            "changes": [c.model_dump() for c in validated.changes],
            "text": original_text,
            "plain_text": plain_text,
            "file_path": file_path,
        }
