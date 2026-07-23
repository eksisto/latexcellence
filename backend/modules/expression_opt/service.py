"""
表达优化业务逻辑。

负责编排以下流程：
    1. 通过 project_manager 提取段落
    2. 通过 latex_parser 剥离 LaTeX 命令为纯文本
    3. 调用 LLM 逐段优化表达（含 Pydantic 校验 + 重试）
    4. 支持用户自定义提示词
    5. 审核确认后还原 marker 并写回源文件

支持 \\input 解析：
    当 resolve_input=True 时，递归展开主文件中的 \\input/\\include 引用，
    将所有子文件纳入处理范围，段落按文件分组返回。
"""

from typing import Any

from pydantic import BaseModel

from backend.core.config import get_config
from backend.core.base_paragraph_check import BaseParagraphCheckService
from backend.modules.expression_opt.prompt_manager import get_prompt_manager


# ── Pydantic models for LLM response schema ────────────────────────────────

class ExpressionChangeItem(BaseModel):
    """单条表达优化建议"""
    type: str = "rewrite"
    original: str
    optimized: str
    reason: str = ""


class ExpressionOptResult(BaseModel):
    """LLM 表达优化输出——对应 DEVELOPMENT.md §2.1 JSON 协议"""
    paragraph_index: int
    original: str
    optimized: str
    changes: list[ExpressionChangeItem] = []


# ── base system prompt ─────────────────────────────────────────────────────

_BASE_SYSTEM_PROMPT = (
    "You are a LaTeX document writing coach specializing in academic English. "
    "Your task is to improve the clarity, conciseness, and academic tone of "
    "the given text.\n\n"
    "GUIDELINES:\n"
    "- Make sentences more concise without losing meaning\n"
    "- Use precise academic vocabulary\n"
    "- Improve logical flow and transitions\n"
    "- Fix awkward or unnatural phrasing\n"
    "- Preserve the original author's intent and key terminology\n\n"
    "CRITICAL RULES — VIOLATING THESE MAKES THE OUTPUT UNUSABLE:\n"
    "1. The text contains markers like __CMD_0000__ or __MATH_0000__ "
    "which represent LaTeX commands and math formulas. "
    "These markers are NOT part of human-readable text.\n"
    "2. ABSOLUTELY DO NOT modify, delete, split, merge, or reorder any marker. "
    "Every marker must appear in the optimized text EXACTLY as in the original.\n"
    "3. Only modify plain text portions (words, sentences, punctuation).\n"
    "4. If no improvement is needed, the optimized field must be identical "
    "to the original field.\n"
    "5. Respond with JSON only, no markdown fences, no extra text.\n\n"
    "JSON format (strictly follow this schema):\n"
    "{{\n"
    '  "paragraph_index": <int>,\n'
    '  "original": "<the exact original text with all markers unchanged>",\n'
    '  "optimized": "<the improved text with all markers unchanged>",\n'
    '  "changes": [\n'
    '    {{\n'
    '      "type": "rewrite",\n'
    '      "original": "<the original text fragment>",\n'
    '      "optimized": "<the improved text fragment>",\n'
    '      "reason": "<brief explanation of the improvement>"\n'
    '    }}\n'
    '  ]\n'
    "}}\n\n"
    "CUSTOM INSTRUCTIONS FROM USER:\n"
    "{custom_prompt}\n"
    "Above custom instructions take priority over the general guidelines."
)


class ExpressionOptService(BaseParagraphCheckService):
    """
    表达优化服务。

    用法：
        svc = ExpressionOptService()
        preview = svc.preview_paragraphs("main.tex")
        results = await svc.optimize("main.tex")
        svc.apply_changes("main.tex", [{"paragraph_index": 0, "new_text": "..."}])

    支持 \\input 解析：
        preview = svc.preview_paragraphs("main.tex", resolve_input=True)
        results = await svc.optimize_stream("main.tex", resolve_input=True)
    """

    _output_field = "optimized"

    def __init__(self):
        super().__init__("expression_opt")

    # ── 自定义提示词 ────────────────────────────────────────────────────────

    def _build_system_prompt_with_custom(self) -> str:
        """构建完整的 system prompt（含用户自定义提示词）。"""
        pm = get_prompt_manager()
        custom = pm.get_active_prompt_content()
        if custom:
            return _BASE_SYSTEM_PROMPT.format(custom_prompt=custom)
        cfg = get_config()
        default = cfg.features.expression_opt.default_prompt
        return _BASE_SYSTEM_PROMPT.format(
            custom_prompt=default or "No specific instructions."
        )

    # ── 公共 API（保持 router.py 兼容）─────────────────────────────────────

    async def optimize(
        self,
        file_path: str,
        paragraph_indices: list[int] | None = None,
        resolve_input: bool = False,
        workspace_id: str = "",
    ) -> dict[str, Any]:
        """对段落进行表达优化（非流式）。"""
        return await self._run_batch_check(
            file_path, paragraph_indices, resolve_input, workspace_id
        )

    async def optimize_stream(
        self, file_path: str, resolve_input: bool = False, workspace_id: str = ""
    ):
        """表达优化流式版本（SSE）。"""
        async for event in self._run_stream_check(file_path, resolve_input, workspace_id):
            yield event

    # ── Hook 实现 ───────────────────────────────────────────────────────────

    def _get_system_prompt(self) -> str:
        return self._build_system_prompt_with_custom()

    def _build_user_content(self, text: str) -> str:
        return (
            f"Please improve the academic expression of the following text.\n\n"
            f"TEXT:\n{text}"
        )

    def _get_model_cls(self):
        return ExpressionOptResult

    def _get_llm_params(self) -> dict[str, Any]:
        return {"temperature": 0.3, "max_tokens": 2048}

    def _get_batch_size(self) -> int:
        return get_config().features.expression_opt.batch_size

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
            "optimized": validated.optimized,
            "changes": [c.model_dump() for c in validated.changes],
            "text": original_text,
            "plain_text": plain_text,
            "file_path": file_path,
        }
