"""
表达优化业务逻辑。

负责编排以下流程：
    1. 通过 project_manager 提取段落
    2. 通过 latex_parser 剥离 LaTeX 命令为纯文本
    3. 调用 LLM 逐段优化表达（含 Pydantic 校验 + 重试）
    4. 支持用户自定义提示词
    5. 审核确认后还原 marker 并写回源文件
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
from backend.modules.expression_opt.prompt_manager import get_prompt_manager
from backend.utils.json_utils import retry_with_validation


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
    "1. The text contains markers like __CMD_0001__ or __MATH_0001__ "
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

_MARKER_PATTERN = re.compile(r"__[A-Z]+_\d{4}__")


class ExpressionOptService:
    """
    表达优化服务。

    用法：
        svc = ExpressionOptService()
        preview = svc.preview_paragraphs("main.tex")
        results = await svc.optimize("main.tex")
        svc.apply_changes("main.tex", [{"paragraph_index": 0, "new_text": "..."}])
    """

    def __init__(self):
        self._logger = get_logger("expression_opt")
        self._parser = LatexParser()
        self._marker_maps: dict[int, dict[str, str]] = {}
        self._file_path: Optional[str] = None

    def _build_system_prompt(self) -> str:
        """构建完整的 system prompt（含用户自定义提示词）。"""
        pm = get_prompt_manager()
        custom = pm.get_active_prompt_content()
        if custom:
            return _BASE_SYSTEM_PROMPT.format(custom_prompt=custom)
        cfg = get_config()
        default = cfg.features.expression_opt.default_prompt
        return _BASE_SYSTEM_PROMPT.format(custom_prompt=default or "No specific instructions.")

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

    async def optimize(
        self,
        file_path: str,
        paragraph_indices: Optional[list[int]] = None,
    ) -> dict[str, Any]:
        """
        对段落进行表达优化（非流式）。

        参数：
            file_path: 文件路径
            paragraph_indices: 要优化的段落索引列表，None 表示全部

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
                    "optimized": stripped,
                    "changes": [],
                    "text": para.text,
                })
            return {"results": results, "file_path": file_path}

        llm = get_llm_client()
        batch_size = cfg.features.expression_opt.batch_size
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
                        self._optimize_one_paragraph(llm, len(tasks), idx, stripped, para.text)
                    )

            batch_results = await asyncio.gather(*tasks)
            for slot, idx in enumerate(batch_indices):
                results[batch_start + slot] = batch_results[slot]

            if batch_start + batch_size < len(targets):
                await asyncio.sleep(cfg.llm.request_interval)

        return {"results": results, "file_path": file_path}

    async def optimize_stream(self, file_path: str):
        """
        表达优化流式版本（SSE）。

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
                        "optimized": stripped,
                        "changes": [],
                        "text": para.text,
                    },
                }
                yield {"type": "progress", "current": i + 1, "total": total}
                continue

            try:
                result = await self._optimize_one_paragraph(llm, 0, i, stripped, para.text)
                yield {"type": "result", "paragraph_index": i, "data": result}
            except Exception as e:
                self._logger.error(f"Error optimizing paragraph {i}: {e}")
                yield {
                    "type": "error",
                    "message": f"Paragraph {i}: {e}",
                    "retrying": False,
                }

            yield {"type": "progress", "current": i + 1, "total": total}
            await asyncio.sleep(cfg.llm.request_interval)

        yield {"type": "done"}

    async def _optimize_one_paragraph(
        self,
        llm,
        slot: int,
        idx: int,
        plain_text: str,
        original_text: str,
    ) -> dict[str, Any]:
        """
        调用 LLM 优化单个段落的表达，含 Pydantic 校验 + 重试 + marker 保护验证。
        """
        input_markers = set(_MARKER_PATTERN.findall(plain_text))

        system_prompt = self._build_system_prompt()
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Please improve the academic expression of the following text.\n\n"
                    f"TEXT:\n{plain_text}"
                ),
            },
        ]

        try:
            validated: ExpressionOptResult = await retry_with_validation(
                chat_fn=llm.chat,
                messages=messages,
                model_cls=ExpressionOptResult,
                max_retries=3,
                logger_name="expression_opt",
                temperature=0.3,
                max_tokens=2048,
                response_format={"type": "json_object"},
            )

            optimized = validated.optimized

            output_markers = set(_MARKER_PATTERN.findall(optimized))
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
                    "optimized": plain_text,
                    "changes": [],
                    "text": original_text,
                    "plain_text": plain_text,
                    "marker_error": True,
                }

            return {
                "paragraph_index": idx,
                "original": validated.original,
                "optimized": optimized,
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
                "optimized": plain_text,
                "changes": [],
                "text": original_text,
                "plain_text": plain_text,
            }
        except Exception as e:
            self._logger.error(f"Paragraph {idx}: LLM call failed — {e}")
            return {
                "paragraph_index": idx,
                "original": plain_text,
                "optimized": plain_text,
                "changes": [],
                "text": original_text,
                "plain_text": plain_text,
            }

    async def _empty_result(
        self, idx: int, plain_text: str, original_text: str
    ) -> dict[str, Any]:
        return {
            "paragraph_index": idx,
            "original": plain_text,
            "optimized": plain_text,
            "changes": [],
            "text": original_text,
            "plain_text": plain_text,
        }

    def apply_changes(
        self, file_path: str, changes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        将用户确认的修改写回源文件。

        参数：
            file_path: 文件路径
            changes: [{ "paragraph_index": 0, "new_text": "优化后纯文本" }]

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
            self._logger.info(f"Applied expression change to paragraph {idx} in {file_path}")

        return {"applied": applied, "total": len(changes)}
