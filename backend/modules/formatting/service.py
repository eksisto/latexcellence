"""
源码格式化业务逻辑。

负责编排以下流程：
    1. 通过 project_manager 提取段落
    2. 将段落的**原始 LaTeX 源码**（非剥离后的纯文本）交给 LLM 进行格式化优化
    3. LLM 返回格式化后的 LaTeX 源码 + 修改说明
    4. 审核确认后直接写回源文件

关键区别（与 typo_check / expression_opt 的不同）：
    - 不使用 Marker 保护策略。格式化需要对 LaTeX 结构本身进行调整
      （间距、环境对齐、缩进），因此 LLM 看到的是原始 LaTeX 源码。
    - preview 仍计算 plain_text，仅供前端 ParagraphContext 展示上下文。
    - apply 时无需还原 marker，corrected 字段即为可写回的完整 LaTeX。

支持 \\input 解析：
    当 resolve_input=True 时，递归展开主文件中的 \\input/\\include 引用，
    将所有子文件纳入处理范围，段落按文件分组返回。
"""

from typing import Any, Optional

from pydantic import BaseModel

from backend.core.config import get_config
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.core.base_paragraph_check import BaseParagraphCheckService
from backend.utils.json_utils import retry_with_validation


# ── Pydantic models for LLM response schema ────────────────────────────────

class FormattingChangeItem(BaseModel):
    """单条排版修改建议——对应 DEVELOPMENT.md §2.1 自动排版协议"""
    type: str = "spacing"
    description: str = ""
    original: str
    corrected: str


class FormattingResult(BaseModel):
    """LLM 自动排版输出"""
    paragraph_index: int
    original: str
    corrected: str
    changes: list[FormattingChangeItem] = []


# ── system prompt ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a LaTeX code formatter and formatting expert. "
    "Your task is to improve the formatting of the given LaTeX source code "
    "without changing its content or meaning.\n\n"
    "Focus on these types of improvements:\n"
    "- spacing: normalize paragraph spacing (\\parskip, \\parindent, \\vspace), "
    "blank lines between paragraphs, vertical spacing around environments\n"
    "- environment_alignment: ensure consistent use of math environments "
    "(equation/align/gather), consistent \\begin/\\end formatting, "
    "align table columns, normalize environment options\n"
    "- code_formatting: fix indentation, align ampersands (&) in align/tabular, "
    "normalize spacing around commands, consistent line breaks\n"
    "- consistency: ensure consistent style throughout the document\n\n"
    "CRITICAL RULES — VIOLATING THESE MAKES THE OUTPUT UNUSABLE:\n"
    "1. DO NOT change the document content, meaning, or mathematics.\n"
    "2. DO NOT add, remove, or rewrite any natural language text.\n"
    "3. DO NOT modify the actual content of math formulas or their results.\n"
    "4. Only adjust formatting: spacing, indentation, line breaks, "
    "environment consistency, and alignment.\n"
    "5. Preserve all LaTeX commands and their arguments — only their "
    "surrounding whitespace and line layout may change.\n"
    "6. If no formatting improvement is needed, the corrected field must be "
    "byte-identical to the original field.\n"
    "7. Respond with JSON only, no markdown fences, no extra text.\n\n"
    "JSON format (strictly follow this schema):\n"
    "{\n"
    '  "paragraph_index": <int>,\n'
    '  "original": "<the exact original LaTeX source>",\n'
    '  "corrected": "<the formatted LaTeX source>",\n'
    '  "changes": [\n'
    '    {\n'
    '      "type": "spacing|environment_alignment|code_formatting|consistency",\n'
    '      "description": "<Chinese brief description of the formatting change>",\n'
    '      "original": "<the original LaTeX fragment>",\n'
    '      "corrected": "<the formatted LaTeX fragment>"\n'
    '    }\n'
    '  ]\n'
    "}"
)


class FormattingService(BaseParagraphCheckService):
    """
    源码格式化服务。

    用法：
        svc = FormattingService()
        preview = svc.preview_paragraphs("main.tex")
        results = await svc.check_formatting("main.tex")
        svc.apply_changes("main.tex", [{"paragraph_index": 0, "new_text": "..."}])

    支持 \\input 解析：
        preview = svc.preview_paragraphs("main.tex", resolve_input=True)
        results = await svc.check_formatting_stream("main.tex", resolve_input=True)
    """

    use_marker_protection = False

    def __init__(self):
        super().__init__("formatting")

    # ── 公共 API（保持 router.py 兼容）─────────────────────────────────────

    async def check_formatting(
        self,
        file_path: str,
        paragraph_indices: Optional[list[int]] = None,
        resolve_input: bool = False,
        workspace_id: str = "",
    ) -> dict[str, Any]:
        """对段落进行源码格式化检查（非流式）。"""
        return await self._run_batch_check(
            file_path, paragraph_indices, resolve_input, workspace_id
        )

    async def check_formatting_stream(
        self, file_path: str, resolve_input: bool = False, workspace_id: str = ""
    ):
        """源码格式化流式版本（SSE）。"""
        async for event in self._run_stream_check(file_path, resolve_input, workspace_id):
            yield event

    # ── 覆写：无 marker 保护 + 空输出检查 ──────────────────────────────────

    async def _check_one_paragraph(
        self,
        llm,
        slot: int,
        idx: int,
        text: str,
        original_text: str,
        file_path: str,
    ) -> dict[str, Any]:
        """
        调用 LLM 对单段 LaTeX 源码进行格式化优化，含 Pydantic 校验 + 重试。

        与基类的区别：无 marker 保护，增加空输出检查。
        """
        messages = [
            {"role": "system", "content": self._get_system_prompt()},
            {
                "role": "user",
                "content": self._build_user_content(text),
            },
        ]

        try:
            validated = await retry_with_validation(
                chat_fn=llm.chat,
                messages=messages,
                model_cls=self._get_model_cls(),
                max_retries=3,
                logger_name=self._logger.name,
                **self._get_llm_params(),
                response_format={"type": "json_object"},
            )

            corrected = validated.corrected

            if not corrected.strip():
                self._logger.warning(
                    f"Paragraph {idx} in {file_path}: "
                    f"empty corrected output, falling back"
                )
                return self._make_fallback(
                    idx, original_text, original_text, file_path
                )

            return self._build_success_result(
                idx, validated, original_text, file_path, text
            )

        except ValueError as e:
            self._logger.warning(
                f"Paragraph {idx} in {file_path}: "
                f"validation retries exhausted — {e}"
            )
            return self._make_fallback(idx, text, original_text, file_path)
        except Exception as e:
            self._logger.error(
                f"Paragraph {idx} in {file_path}: LLM call failed — {e}"
            )
            return self._make_fallback(idx, text, original_text, file_path)

    # ── 覆写：无需 marker 还原 ─────────────────────────────────────────────

    def apply_changes(
        self,
        file_path: str,
        changes: list[dict[str, Any]],
        workspace_id: str = "",
        operation_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        将用户确认的格式修改写回源文件。

        与 typo_check/expression_opt 不同：无需还原 marker，
        corrected 字段即为完整的 LaTeX 源码，直接写回即可。
        operation_id 参数保留以保持签名一致，但不使用。
        """
        pm = get_project_manager_for_workspace(workspace_id)
        applied = 0

        for change in changes:
            idx = change["paragraph_index"]
            new_text = change["new_text"]
            target_file = change.get("file_path", file_path)

            pm.apply_change(target_file, idx, new_text)
            applied += 1
            self._logger.info(
                f"Applied formatting change to paragraph {idx} in {target_file}"
            )

        return {"applied": applied, "total": len(changes)}

    # ── Hook 实现 ───────────────────────────────────────────────────────────

    def _get_system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    def _build_user_content(self, text: str) -> str:
        return (
            f"Please improve the formatting of the following LaTeX source.\n\n"
            f"LATEX SOURCE:\n{text}"
        )

    def _get_model_cls(self):
        return FormattingResult

    def _get_llm_params(self) -> dict[str, Any]:
        return {"temperature": 0.2, "max_tokens": 4096}

    def _get_batch_size(self) -> int:
        return get_config().features.formatting.batch_size

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
            "file_path": file_path,
        }
