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

from typing import Any, ClassVar

from pydantic import BaseModel

from backend.core.base_paragraph_check import (
    BaseParagraphCheckService,
    has_unlisted_changes,
    normalize_changes_contract,
)
from backend.core.config import get_config
from backend.core.prompts import get_prompt
from backend.utils.latex_json_guard import LatexTextLite

# ── Pydantic models for LLM response schema ────────────────────────────────


class TypoChangeItem(BaseModel):
    """LLM 返回的单条修改建议"""

    type: str = "typo"
    original: str
    corrected: str
    position: int | None = None
    explanation: str = ""


class TypoCheckResult(BaseModel):
    """LLM 错字检查输出——对应 DEVELOPMENT.md §2.1 JSON 协议"""

    # 轻量内容校验：只拦控制字符 / 垃圾占位符，不查配平（避免残余结构误报）
    corrected: LatexTextLite
    changes: list[TypoChangeItem] = []


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

    # LLM 槽位路由（multi-model §6）：逐段高频调用，适合快速模型
    llm_role: ClassVar[str] = "typo_check"

    def __init__(self):
        super().__init__("typo_check")

    # ── 公共 API（保持 router.py 兼容）─────────────────────────────────────

    async def check_typos(
        self,
        file_path: str,
        paragraph_indices: list[int] | None = None,
        resolve_input: bool = False,
        workspace_id: str = "",
        language: str = "zh",
    ) -> dict[str, Any]:
        """对段落进行错字检查（非流式）。"""
        self._language = language
        return await self._run_batch_check(
            file_path, paragraph_indices, resolve_input, workspace_id
        )

    async def check_typos_stream(
        self,
        file_path: str,
        resolve_input: bool = False,
        workspace_id: str = "",
        language: str = "zh",
    ):
        """错字检查流式版本（SSE）。"""
        self._language = language
        async for event in self._run_stream_check(
            file_path, resolve_input, workspace_id
        ):
            yield event

    # ── Hook 实现 ───────────────────────────────────────────────────────────

    def _get_system_prompt(self) -> str:
        return get_prompt(
            "typo_check", "system", language=getattr(self, "_language", "zh")
        )

    def _build_user_content(self, text: str) -> str:
        return get_prompt("typo_check", "user", text=text)

    def _get_model_cls(self):
        return TypoCheckResult

    def _get_llm_params(self) -> dict[str, Any]:
        cfg = get_config()
        return {
            "temperature": 0.3,
            "max_tokens": max(cfg.llm.max_tokens, 4096),
        }

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
        # changes[] 的 original/corrected 还原 marker → 前端干净展示
        # corrected 字段保留 marker → apply_changes 依赖 marker_map 写回
        changes = normalize_changes_contract(
            [c.model_dump() for c in validated.changes],
            output_text=validated.corrected,
            plain_text=plain_text,
        )
        unlisted_changes = has_unlisted_changes(
            changes, validated.corrected, plain_text
        )
        changes, _ = self._restore_change_markers(changes, file_path, idx)
        return {
            "paragraph_index": idx,
            "original": original_text,
            "corrected": validated.corrected,
            "changes": changes,
            "unlisted_changes": unlisted_changes,
            "text": original_text,
            "plain_text": plain_text,
            "file_path": file_path,
        }
