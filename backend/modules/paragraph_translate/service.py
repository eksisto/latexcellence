"""
逐段翻译业务逻辑。

负责编排以下流程：
    1. 通过 project_manager 提取段落
    2. 通过 latex_parser 剥离 LaTeX 命令为纯文本
    3. 调用 LLM 逐段翻译为目标语言（含 Pydantic 校验 + 重试）
    4. 审核确认后还原 marker 并写回源文件

支持 \\input 解析：
    当 resolve_input=True 时，递归展开主文件中的 \\input/\\include 引用，
    将所有子文件纳入处理范围，段落按文件分组返回。
"""

from typing import Any, ClassVar

from pydantic import BaseModel

from backend.core.config import get_config
from backend.core.base_paragraph_check import (
    BaseParagraphCheckService,
    has_unlisted_changes,
    normalize_changes_contract,
)
from backend.core.prompts import get_prompt
from backend.utils.latex_json_guard import LatexTextLite

# ── Pydantic models for LLM response schema ────────────────────────────────


class TranslateChangeItem(BaseModel):
    """LLM 返回的单条翻译修改"""

    type: str = "translation"
    original: str
    translated: str
    explanation: str = ""


class ParagraphTranslateResult(BaseModel):
    """LLM 翻译输出"""

    # 轻量内容校验：只拦控制字符 / 垃圾占位符，不查配平（避免残余结构误报）
    translated: LatexTextLite
    changes: list[TranslateChangeItem] = []


class ParagraphTranslateService(BaseParagraphCheckService):
    """
    逐段翻译服务。

    用法：
        svc = ParagraphTranslateService()
        preview = svc.preview_paragraphs("main.tex")
        results = await svc.translate("main.tex", target_language="English")
        svc.apply_changes("main.tex", [{"paragraph_index": 0, "new_text": "..."}])

    支持 \\input 解析：
        preview = svc.preview_paragraphs("main.tex", resolve_input=True)
        results = await svc.translate_stream("main.tex", resolve_input=True)
    """

    _output_field = "translated"
    # LLM 槽位路由（multi-model §6）：逐段高频调用，适合快速模型
    llm_role: ClassVar[str] = "paragraph_translate"

    def __init__(self):
        super().__init__("paragraph_translate")

    # ── 公共 API（保持 router.py 兼容）─────────────────────────────────────

    async def translate(
        self,
        file_path: str,
        paragraph_indices: list[int] | None = None,
        resolve_input: bool = False,
        workspace_id: str = "",
        language: str = "zh",
        target_language: str = "English",
    ) -> dict[str, Any]:
        """对段落进行翻译（非流式）。"""
        self._language = language
        self._target_language = target_language
        return await self._run_batch_check(
            file_path, paragraph_indices, resolve_input, workspace_id
        )

    async def translate_stream(
        self,
        file_path: str,
        resolve_input: bool = False,
        workspace_id: str = "",
        language: str = "zh",
        target_language: str = "English",
    ):
        """翻译流式版本（SSE）。"""
        self._language = language
        self._target_language = target_language
        async for event in self._run_stream_check(
            file_path, resolve_input, workspace_id
        ):
            yield event

    # ── Hook 实现 ───────────────────────────────────────────────────────────

    def _get_system_prompt(self) -> str:
        return get_prompt(
            "paragraph_translate",
            "system",
            language=getattr(self, "_language", "zh"),
            target_language=getattr(self, "_target_language", "English"),
        )

    def _build_user_content(self, text: str) -> str:
        return get_prompt(
            "paragraph_translate",
            "user",
            text=text,
            target_language=getattr(self, "_target_language", "English"),
        )

    def _get_model_cls(self):
        return ParagraphTranslateResult

    def _get_llm_params(self) -> dict[str, Any]:
        cfg = get_config()
        return {
            "temperature": 0.3,
            "max_tokens": max(cfg.llm.max_tokens, 4096),
        }

    def _get_batch_size(self) -> int:
        return get_config().features.paragraph_translate.batch_size

    def _build_success_result(
        self,
        idx: int,
        validated,
        original_text: str,
        file_path: str,
        plain_text: str,
    ) -> dict[str, Any]:
        # 与 typo_check / expression_opt 不同，本页面展示的是整段原文和译文
        # （而非 change 片段），因此除 changes[] 外还需还原顶层 original 和
        # translated 字段的 marker。marker_map 由 _restore_change_markers 返回，
        # 避免对 self._marker_maps 重复 get().get() 查询。
        # 先做确定性契约校验（与 expression_opt 一致）：
        #   1) 主字段与原文逐字符一致时，丢弃 LLM 编造的虚假 changes
        #   2) 主字段已修改但 changes 为空时，标记为 unlisted_changes
        changes = normalize_changes_contract(
            [c.model_dump() for c in validated.changes],
            output_text=validated.translated,
            plain_text=plain_text,
        )
        unlisted_changes = has_unlisted_changes(
            changes, validated.translated, plain_text
        )
        changes, marker_map = self._restore_change_markers(
            changes,
            file_path,
            idx,
            ("original", "translated"),
        )
        return {
            "paragraph_index": idx,
            "original": (
                self._parser.restore_from_markers(original_text, marker_map)
                if marker_map
                else original_text
            ),
            "translated": (
                self._parser.restore_from_markers(validated.translated, marker_map)
                if marker_map
                else validated.translated
            ),
            "changes": changes,
            "unlisted_changes": unlisted_changes,
            "text": original_text,
            # plain_text 保留 marker（不再用于前端对比），
            # 前端 hasTranslation 改为比较 translated !== original
            # 因为两者均已还原为干净的 LaTeX
            "plain_text": plain_text,
            "file_path": file_path,
        }
