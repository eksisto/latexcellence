"""
LaTeX 代码审查业务逻辑。

负责编排以下流程：
    1. 通过工具（latexindent.pl / 内置格式化器）对 LaTeX 源码进行机械格式化
    2. 将格式化后的真实 LaTeX 源码交给 LLM 进行智能审查（不使用 marker 保护：
       LLM 建议只读展示、不写回文件，new_text 完全由工具生成）
    3. LLM 输出审查建议（坏味道、过时用法、最佳实践等）
    4. 审核确认后将格式化结果写回源文件，LLM 建议仅展示不自动写回

关键区别（与 typo_check / expression_opt / 旧 formatting 的不同）：
    - 无 marker 保护：LLM 直接审查真实 LaTeX 结构，可准确指出坏味道
      （marker 遮挡代码曾导致 LLM 依据周围文本凭空臆测建议）
    - 单数据线：工具格式化后的 formatted 同时作为 LLM 审查输入与写回结果
    - Token 消耗低（LLM 只出建议列表，不输出完整源码）
    - 源码修改由工具完成，LLM 仅提供建议
"""

import asyncio
import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, model_validator

from backend.core.config import get_config
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.core.base_paragraph_check import BaseParagraphCheckService
from backend.core.prompts import get_prompt
from backend.utils.json_utils import retry_with_validation
from backend.utils.latex_parser import MARKER_PATTERN
from backend.utils.latex_json_guard import LatexText
from backend.utils.latexindent_utils import (
    is_latexindent_available,
    format_with_latexindent,
)
from backend.utils.builtin_formatter import format_latex

# ── Pydantic models for LLM response schema ────────────────────────────────


class CodeReviewSuggestion(BaseModel):
    """单条代码审查建议"""

    type: Literal[
        "code_smell",
        "modernization",
        "best_practice",
        "portability",
        "consistency",
    ]
    severity: Literal["info", "warning", "error"] = "info"
    description: str
    original: LatexText
    suggested: LatexText

    @model_validator(mode="after")
    def _reject_markers(self):
        """兜底拦截 marker 样式占位符泄漏到建议字段。

        code_review 输入已无 marker；若输出仍出现 __CMD_0000__ 等 token，
        说明 LLM 在臆造/回显占位符，会污染前端展示（徽章/锚点/采纳逻辑）。
        校验失败会进入 retry_with_validation 重试，要求精确引用源码片段；
        若源码中无真实问题，应返回 needs_review=false。
        """
        for field in ("description", "original", "suggested"):
            value = getattr(self, field)
            if value and MARKER_PATTERN.search(value):
                raise ValueError(
                    f"suggestion.{field} contains a marker-like token "
                    "(e.g. __CMD_0000__); quote the exact source fragment instead. "
                    "If the source has no genuine issue, set needs_review to false."
                )
        return self


class CodeReviewResult(BaseModel):
    """LLM 代码审查输出（不再输出 corrected 字段，源码修改由工具完成）"""

    paragraph_index: int
    needs_review: bool = True
    suggestions: list[CodeReviewSuggestion] = []

    @model_validator(mode="after")
    def _check_suggestions_consistency(self):
        if self.needs_review and not self.suggestions:
            raise ValueError("needs_review is true but suggestions is empty")
        if not self.needs_review and self.suggestions:
            raise ValueError("needs_review is false but suggestions is non-empty")
        return self


class CodeReviewService(BaseParagraphCheckService):
    """
    LaTeX 代码审查服务。

    用法：
        svc = CodeReviewService()
        preview = svc.preview_paragraphs("main.tex")
        results = await svc.check_code_review("main.tex")
        svc.apply_changes("main.tex", [{"paragraph_index": 0, "new_text": "..."}])
    """

    use_marker_protection: ClassVar[bool] = False
    _latexindent_available: bool | None = None

    def __init__(self):
        super().__init__("code_review")

    # ── 公共 API ────────────────────────────────────────────────────────────

    async def check_code_review(
        self,
        file_path: str,
        paragraph_indices: list[int] | None = None,
        resolve_input: bool = False,
        workspace_id: str = "",
        language: str = "zh",
    ) -> dict[str, Any]:
        """对段落进行代码审查（非流式）。"""
        self._language = language
        return await self._run_batch_check(
            file_path, paragraph_indices, resolve_input, workspace_id
        )

    async def check_code_review_stream(
        self,
        file_path: str,
        resolve_input: bool = False,
        workspace_id: str = "",
        language: str = "zh",
    ):
        """代码审查流式版本（SSE）。"""
        self._language = language
        async for event in self._run_stream_check(
            file_path, resolve_input, workspace_id
        ):
            yield event

    # ── 工具格式化入口 ──────────────────────────────────────────────────────

    async def _format_source(self, source: str) -> str:
        """统一的格式化入口：latexindent → 内置兜底。"""
        if self._latexindent_available is None:
            self._latexindent_available = await asyncio.to_thread(
                is_latexindent_available
            )
        if self._latexindent_available:
            return await format_with_latexindent(source)
        return await asyncio.to_thread(format_latex, source)

    # ── 覆写：双数据线架构 ──────────────────────────────────────────────────

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
        单段审查实现（无 marker 保护）：

        1. 用原始 LaTeX 调用工具格式化 → formatted
        2. LLM 直接审查格式化后的真实 LaTeX（建议仅展示，不写回源码）
        3. 构造结果（new_text = formatted，LLM 建议仅展示）
        """
        # Step 1: 工具格式化（原始 LaTeX）
        formatted = await self._format_source(original_text)

        # Step 2: 空白段落 → 跳过 LLM
        if not formatted.strip():
            return self._make_no_review_result(idx, formatted, original_text, file_path)

        # Step 3: LLM 审查真实格式化后的 LaTeX（建议只读展示，无写回路径）
        messages = [
            {"role": "system", "content": self._get_system_prompt()},
            {
                "role": "user",
                "content": self._build_user_content(formatted),
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

            if not validated.needs_review:
                return self._make_no_review_result(
                    idx, formatted, original_text, file_path
                )

            return self._build_review_result(
                idx, validated, original_text, formatted, file_path
            )

        except ValueError as e:
            self._logger.warning(
                f"Paragraph {idx} in {file_path}: "
                f"validation retries exhausted — {e}"
            )
            return self._make_fallback(idx, formatted, original_text, file_path)
        except Exception as e:
            self._logger.error(f"Paragraph {idx} in {file_path}: LLM call failed — {e}")
            return self._make_fallback(idx, formatted, original_text, file_path)

    # ── 结果构建 ────────────────────────────────────────────────────────────

    def _make_no_review_result(
        self, idx: int, formatted: str, original_text: str, file_path: str
    ) -> dict[str, Any]:
        """纯 LaTeX 段落无需 LLM 审查。"""
        return {
            "paragraph_index": idx,
            "original": original_text,
            "new_text": formatted,
            "suggestions": [],
            "text": original_text,
            "file_path": file_path,
            "needs_review": False,
        }

    def _build_review_result(
        self,
        idx: int,
        validated: CodeReviewResult,
        original_text: str,
        formatted: str,
        file_path: str,
    ) -> dict[str, Any]:
        """从 LLM 审查结果构建返回 dict。"""
        return {
            "paragraph_index": idx,
            "original": original_text,
            "new_text": formatted,
            "suggestions": [s.model_dump() for s in validated.suggestions],
            "text": original_text,
            "file_path": file_path,
            "needs_review": True,
        }

    def _make_fallback(
        self,
        idx: int,
        text: str,
        original_text: str,
        file_path: str,
        **extra: Any,
    ) -> dict[str, Any]:
        """LLM 失败时的回退结果。"""
        return {
            "paragraph_index": idx,
            "original": original_text,
            "new_text": text,
            "suggestions": [],
            "text": original_text,
            "file_path": file_path,
            **extra,
        }

    # ── 覆写：直接写回格式化后的原始 LaTeX（无需 marker 还原）─────────────

    def apply_changes(
        self,
        file_path: str,
        changes: list[dict[str, Any]],
        workspace_id: str = "",
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        将 latexindent 格式化结果写回源文件。

        new_text 已是原始 LaTeX（无 marker），直接写回。
        LLM 建议仅在前端展示，不自动写回源码。
        """
        from backend.core.project_manager import FileConflictError

        pm = get_project_manager_for_workspace(workspace_id)
        applied = 0
        errors: list[dict[str, Any]] = []

        for change in changes:
            idx = change["paragraph_index"]
            new_text = change["new_text"]
            target_file = change.get("file_path", file_path)
            expected_md5 = change.get("expected_md5")

            try:
                pm.apply_change(target_file, idx, new_text, expected_md5=expected_md5)
                applied += 1
                self._logger.info(
                    f"Applied code review change to paragraph {idx} in {target_file}"
                    f" [op={operation_id or 'legacy'}]"
                )
            except FileConflictError as e:
                self._logger.warning(
                    f"File conflict for paragraph {idx} in {target_file}: {e}"
                )
                errors.append(
                    {
                        "paragraph_index": idx,
                        "file_path": target_file,
                        "reason": "file_conflict",
                        "detail": str(e),
                        "expected_md5": e.expected_md5,
                        "current_md5": e.current_md5,
                    }
                )

        return {"applied": applied, "total": len(changes), "errors": errors}

    # ── Hook 实现 ───────────────────────────────────────────────────────────

    def _get_system_prompt(self) -> str:
        return get_prompt(
            "code_review", "system", language=getattr(self, "_language", "zh")
        )

    def _build_user_content(self, text: str) -> str:
        return get_prompt("code_review", "user", text=text)

    def _get_model_cls(self):
        return CodeReviewResult

    def _get_llm_params(self) -> dict[str, Any]:
        return {"temperature": 0.2, "max_tokens": 4096}

    def _get_batch_size(self) -> int:
        config = get_config()
        feature_cfg = getattr(config.features, "code_review", None)
        if feature_cfg is None:
            return 5
        return feature_cfg.batch_size

    def _build_success_result(
        self,
        idx: int,
        validated,
        original_text: str,
        file_path: str,
        plain_text: str,
    ) -> dict[str, Any]:
        """供基类调用的兜底实现（_check_one_paragraph 已被覆写，通常不会到达此方法）。"""
        new_text = getattr(validated, "new_text", original_text)
        suggestions = getattr(validated, "suggestions", [])
        return {
            "paragraph_index": idx,
            "original": original_text,
            "new_text": new_text,
            "suggestions": [s.model_dump() for s in suggestions] if suggestions else [],
            "text": original_text,
            "file_path": file_path,
        }
