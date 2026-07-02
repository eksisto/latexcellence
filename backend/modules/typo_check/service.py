"""
错字查找业务逻辑。

负责编排以下流程：
    1. 通过 project_manager 提取段落
    2. 通过 latex_parser 剥离 LaTeX 命令为纯文本
    3. 调用 LLM 逐段检查错字（含 Pydantic 校验 + 重试）
    4. 审核确认后还原 marker 并写回源文件
"""

import asyncio
import re
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.core.project_manager import get_project_manager
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.modules.typo_check.latex_parser import LatexParser
from backend.utils.json_utils import retry_with_validation


# ── Pydantic models for LLM response schema ────────────────────────────────

class ChangeItem(BaseModel):
    """单条修改建议"""
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
    changes: list[ChangeItem] = []


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

# regex for extracting markers from plain text
_MARKER_PATTERN = re.compile(r"__[A-Z]+_\d{4}__")


class TypoCheckService:
    """
    错字检查服务。

    用法：
        svc = TypoCheckService()
        preview = svc.preview_paragraphs("main.tex")
        results = await svc.check_typos("main.tex")
        svc.apply_changes("main.tex", [{"paragraph_index": 0, "new_text": "..."}])
    """

    def __init__(self):
        self._logger = get_logger("typo_check")
        self._parser = LatexParser()
        self._marker_maps: dict[int, dict[str, str]] = {}
        self._file_path: Optional[str] = None

    def preview_paragraphs(self, file_path: str) -> dict[str, Any]:
        """
        预览段落：返回每段的原始文本和剥离后的纯文本。

        参数：
            file_path: 文件路径

        返回：
            { paragraphs: [...], file_path: "...", total: N }
        """
        pm = get_project_manager()
        paragraphs = pm.get_paragraphs(file_path)

        self._file_path = file_path
        self._marker_maps = {}

        result = []
        for para in paragraphs:
            stripped, marker_map = self._parser.strip_to_plain_text(para.text)
            self._marker_maps[para.index] = marker_map
            result.append({
                "index": para.index,
                "text": para.text,
                "plain_text": stripped,
                "line_start": para.line_start,
                "line_end": para.line_end,
            })

        self._logger.info(
            f"Previewed {len(result)} paragraphs from {file_path}"
        )
        return {
            "paragraphs": result,
            "file_path": file_path,
            "total": len(result),
        }

    async def check_typos(
        self,
        file_path: str,
        paragraph_indices: Optional[list[int]] = None,
    ) -> dict[str, Any]:
        """
        对段落进行错字检查（非流式）。

        参数：
            file_path: 文件路径
            paragraph_indices: 要检查的段落索引列表，None 表示全部

        返回：
            { results: [...], file_path: "..." }
        """
        pm = get_project_manager()
        cfg = get_config()

        paragraphs = pm.get_paragraphs(file_path)
        self._file_path = file_path
        self._marker_maps = {}

        targets = list(
            paragraph_indices
            if paragraph_indices is not None
            else range(len(paragraphs))
        )

        if not cfg.llm.api_key:
            self._logger.warning("No API key configured, returning plain text only")
            results = []
            for idx in targets:
                para = paragraphs[idx]
                stripped, marker_map = self._parser.strip_to_plain_text(para.text)
                self._marker_maps[idx] = marker_map
                results.append({
                    "paragraph_index": idx,
                    "original": stripped,
                    "corrected": stripped,
                    "changes": [],
                    "text": para.text,
                })
            return {"results": results, "file_path": file_path}

        llm = get_llm_client()
        batch_size = cfg.features.typo_check.batch_size
        results: list[dict[str, Any]] = [None] * len(targets)  # type: ignore[list-item]

        for batch_start in range(0, len(targets), batch_size):
            batch_indices = targets[batch_start : batch_start + batch_size]
            tasks = []

            for slot, idx in enumerate(batch_indices):
                para = paragraphs[idx]
                stripped, marker_map = self._parser.strip_to_plain_text(para.text)
                self._marker_maps[idx] = marker_map

                if not stripped.strip():
                    tasks.append(self._empty_result(idx, stripped, para.text))
                else:
                    tasks.append(
                        self._check_one_paragraph(llm, len(tasks), idx, stripped, para.text)
                    )

            batch_results = await asyncio.gather(*tasks)
            for slot, idx in enumerate(batch_indices):
                results[batch_start + slot] = batch_results[slot]

            if batch_start + batch_size < len(targets):
                await asyncio.sleep(cfg.llm.request_interval)

        return {"results": results, "file_path": file_path}

    async def check_typos_stream(self, file_path: str):
        """
        错字检查流式版本（SSE）。

        逐段 yield 事件：progress / result / error / done。
        """
        pm = get_project_manager()
        cfg = get_config()

        paragraphs = pm.get_paragraphs(file_path)
        self._file_path = file_path
        self._marker_maps = {}

        total = len(paragraphs)
        yield {"type": "progress", "current": 0, "total": total}

        if not cfg.llm.api_key:
            yield {
                "type": "error",
                "message": "No API key configured",
                "retrying": False,
            }
            yield {"type": "done"}
            return

        llm = get_llm_client()

        for i, para in enumerate(paragraphs):
            stripped, marker_map = self._parser.strip_to_plain_text(para.text)
            self._marker_maps[i] = marker_map

            if not stripped.strip():
                yield {
                    "type": "result",
                    "paragraph_index": i,
                    "data": {
                        "paragraph_index": i,
                        "original": stripped,
                        "corrected": stripped,
                        "changes": [],
                        "text": para.text,
                    },
                }
                yield {"type": "progress", "current": i + 1, "total": total}
                continue

            try:
                result = await self._check_one_paragraph(llm, 0, i, stripped, para.text)
                yield {"type": "result", "paragraph_index": i, "data": result}
            except Exception as e:
                self._logger.error(f"Error checking paragraph {i}: {e}")
                yield {
                    "type": "error",
                    "message": f"Paragraph {i}: {e}",
                    "retrying": False,
                }

            yield {"type": "progress", "current": i + 1, "total": total}
            await asyncio.sleep(cfg.llm.request_interval)

        yield {"type": "done"}

    async def _check_one_paragraph(
        self,
        llm,
        slot: int,
        idx: int,
        plain_text: str,
        original_text: str,
    ) -> dict[str, Any]:
        """
        调用 LLM 检查单个段落的错字，含 Pydantic 校验 + 重试 + marker 保护验证。

        流程：
            1. 提取输入中的所有 marker
            2. 调用 retry_with_validation 进行 LLM 调用 + JSON 校验
            3. 验证 LLM 输出中的 marker 是否完整保留
            4. marker 丢失时回退到原始文本

        参数：
            llm: LLM 客户端
            slot: 批次内的槽位（用于日志）
            idx: 段落索引
            plain_text: 剥离后的纯文本（含 marker）
            original_text: 原始 LaTeX 源码

        返回：
            包含 paragraph_index / original / corrected / changes 的 dict
        """
        input_markers = set(_MARKER_PATTERN.findall(plain_text))

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Please check the following text for typos and errors.\n\n"
                    f"TEXT:\n{plain_text}"
                ),
            },
        ]

        try:
            validated: TypoCheckResult = await retry_with_validation(
                chat_fn=llm.chat,
                messages=messages,
                model_cls=TypoCheckResult,
                max_retries=3,
                logger_name="typo_check",
                temperature=0.3,
                max_tokens=2048,
                response_format={"type": "json_object"},
            )

            corrected = validated.corrected

            # marker 保护验证
            output_markers = set(_MARKER_PATTERN.findall(corrected))
            missing = input_markers - output_markers
            extra = output_markers - input_markers

            if missing or extra:
                self._logger.warning(
                    f"Paragraph {idx}: marker mismatch — "
                    f"missing={len(missing)}, extra={len(extra)}. "
                    f"Falling back to original text."
                )
                return {
                    "paragraph_index": idx,
                    "original": plain_text,
                    "corrected": plain_text,
                    "changes": [],
                    "text": original_text,
                    "plain_text": plain_text,
                    "marker_error": True,
                }

            return {
                "paragraph_index": idx,
                "original": validated.original,
                "corrected": corrected,
                "changes": [c.model_dump() for c in validated.changes],
                "text": original_text,
                "plain_text": plain_text,
            }

        except ValueError as e:
            self._logger.warning(
                f"Paragraph {idx}: validation retries exhausted — {e}"
            )
            return {
                "paragraph_index": idx,
                "original": plain_text,
                "corrected": plain_text,
                "changes": [],
                "text": original_text,
                "plain_text": plain_text,
            }
        except Exception as e:
            self._logger.error(f"Paragraph {idx}: LLM call failed — {e}")
            return {
                "paragraph_index": idx,
                "original": plain_text,
                "corrected": plain_text,
                "changes": [],
                "text": original_text,
                "plain_text": plain_text,
            }

    async def _empty_result(
        self, idx: int, plain_text: str, original_text: str
    ) -> dict[str, Any]:
        """为空白段落生成空结果。"""
        return {
            "paragraph_index": idx,
            "original": plain_text,
            "corrected": plain_text,
            "changes": [],
            "text": original_text,
            "plain_text": plain_text,
        }

    def apply_changes(
        self, file_path: str, changes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        将用户确认的修改写回源文件。

        流程：
            1. 对每个 change，用 marker_map 将纯文本还原为 LaTeX
            2. 调用 project_manager.apply_change() 写回文件

        参数：
            file_path: 文件路径
            changes: [{ "paragraph_index": 0, "new_text": "修正后纯文本" }]

        返回：
            { applied: N, total: M }
        """
        pm = get_project_manager()
        applied = 0

        for change in changes:
            idx = change["paragraph_index"]
            new_plain_text = change["new_text"]
            marker_map = self._marker_maps.get(idx, {})

            restored = self._parser.restore_from_markers(new_plain_text, marker_map)

            pm.apply_change(file_path, idx, restored)
            applied += 1
            self._logger.info(f"Applied change to paragraph {idx} in {file_path}")

        return {"applied": applied, "total": len(changes)}
