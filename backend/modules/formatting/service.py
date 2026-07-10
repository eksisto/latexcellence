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
"""

import asyncio
from typing import Any, Optional

from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.core.project_manager import get_project_manager
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.modules.typo_check.latex_parser import LatexParser
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


class FormattingService:
    """
    源码格式化服务。

    用法：
        svc = FormattingService()
        preview = svc.preview_paragraphs("main.tex")
        results = await svc.check_formatting("main.tex")
        svc.apply_changes("main.tex", [{"paragraph_index": 0, "new_text": "..."}])
    """

    def __init__(self):
        self._logger = get_logger("formatting")
        self._parser = LatexParser()
        self._file_path: Optional[str] = None

    def preview_paragraphs(self, file_path: str) -> dict[str, Any]:
        """
        预览段落：返回每段的原始 LaTeX 源码和剥离后的纯文本。

        原始源码（text）将交给 LLM 进行排版优化；
        纯文本（plain_text）仅供前端 ParagraphContext 展示上下文。

        参数：
            file_path: 文件路径

        返回：
            { paragraphs: [...], file_path: "...", total: N }
        """
        pm = get_project_manager()
        paragraphs = pm.get_paragraphs(file_path)

        self._file_path = file_path

        result = []
        for para in paragraphs:
            stripped, _ = self._parser.strip_to_plain_text(para.text)
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

    async def check_formatting(
        self,
        file_path: str,
        paragraph_indices: Optional[list[int]] = None,
    ) -> dict[str, Any]:
        """
        对段落进行源码格式化检查（非流式）。

        参数：
            file_path: 文件路径
            paragraph_indices: 要格式化的段落索引列表，None 表示全部

        返回：
            { results: [...], file_path: "..." }
        """
        pm = get_project_manager()
        cfg = get_config()

        paragraphs = pm.get_paragraphs(file_path)
        self._file_path = file_path

        targets = list(
            paragraph_indices
            if paragraph_indices is not None
            else range(len(paragraphs))
        )

        if not cfg.llm.api_key:
            self._logger.warning(
                "No API key configured, returning original only")
            results = []
            for idx in targets:
                para = paragraphs[idx]
                results.append({
                    "paragraph_index": idx,
                    "original": para.text,
                    "corrected": para.text,
                    "changes": [],
                    "text": para.text,
                })
            return {"results": results, "file_path": file_path}

        llm = get_llm_client()
        batch_size = cfg.features.formatting.batch_size
        results: list[dict[str, Any]] = [None] * \
            len(targets)  # type: ignore[list-item]

        for batch_start in range(0, len(targets), batch_size):
            batch_indices = targets[batch_start: batch_start + batch_size]
            tasks = []

            for slot, idx in enumerate(batch_indices):
                para = paragraphs[idx]
                if not para.text.strip():
                    tasks.append(self._empty_result(idx, para.text))
                else:
                    tasks.append(
                        self._check_one_paragraph(
                            llm, len(tasks), idx, para.text)
                    )

            batch_results = await asyncio.gather(*tasks)
            for slot, idx in enumerate(batch_indices):
                results[batch_start + slot] = batch_results[slot]

            if batch_start + batch_size < len(targets):
                await asyncio.sleep(cfg.llm.request_interval)

        return {"results": results, "file_path": file_path}

    async def check_formatting_stream(self, file_path: str):
        """
        源码格式化流式版本（SSE）。

        逐段 yield 事件：progress / result / error / done。
        """
        pm = get_project_manager()
        cfg = get_config()

        paragraphs = pm.get_paragraphs(file_path)
        self._file_path = file_path

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
            if not para.text.strip():
                yield {
                    "type": "result",
                    "paragraph_index": i,
                    "data": {
                        "paragraph_index": i,
                        "original": para.text,
                        "corrected": para.text,
                        "changes": [],
                        "text": para.text,
                    },
                }
                yield {"type": "progress", "current": i + 1, "total": total}
                continue

            try:
                result = await self._check_one_paragraph(llm, 0, i, para.text)
                yield {"type": "result", "paragraph_index": i, "data": result}
            except Exception as e:
                self._logger.error(f"Error formatting paragraph {i}: {e}")
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
        original_text: str,
    ) -> dict[str, Any]:
        """
        调用 LLM 对单段 LaTeX 源码进行格式化优化，含 Pydantic 校验 + 重试。

        流程：
            1. 将原始 LaTeX 源码交给 LLM
            2. retry_with_validation 进行 LLM 调用 + JSON 校验
            3. 校验 corrected 非空且为字符串
            4. 异常时回退到原始文本

        参数：
            llm: LLM 客户端
            slot: 批次内的槽位（用于日志）
            idx: 段落索引
            original_text: 原始 LaTeX 源码

        返回：
            包含 paragraph_index / original / corrected / changes 的 dict
        """
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Please improve the formatting of the following LaTeX source.\n\n"
                    f"LATEX SOURCE:\n{original_text}"
                ),
            },
        ]

        try:
            validated: FormattingResult = await retry_with_validation(
                chat_fn=llm.chat,
                messages=messages,
                model_cls=FormattingResult,
                max_retries=3,
                logger_name="formatting",
                temperature=0.2,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )

            corrected = validated.corrected

            if not corrected.strip():
                self._logger.warning(
                    f"Paragraph {idx}: empty corrected output, falling back"
                )
                return {
                    "paragraph_index": idx,
                    "original": original_text,
                    "corrected": original_text,
                    "changes": [],
                    "text": original_text,
                }

            return {
                "paragraph_index": idx,
                "original": validated.original,
                "corrected": corrected,
                "changes": [c.model_dump() for c in validated.changes],
                "text": original_text,
            }

        except ValueError as e:
            self._logger.warning(
                f"Paragraph {idx}: validation retries exhausted — {e}"
            )
            return {
                "paragraph_index": idx,
                "original": original_text,
                "corrected": original_text,
                "changes": [],
                "text": original_text,
            }
        except Exception as e:
            self._logger.error(f"Paragraph {idx}: LLM call failed — {e}")
            return {
                "paragraph_index": idx,
                "original": original_text,
                "corrected": original_text,
                "changes": [],
                "text": original_text,
            }

    async def _empty_result(
        self, idx: int, original_text: str
    ) -> dict[str, Any]:
        """为空白段落生成空结果。"""
        return {
            "paragraph_index": idx,
            "original": original_text,
            "corrected": original_text,
            "changes": [],
            "text": original_text,
        }

    def apply_changes(
        self, file_path: str, changes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        将用户确认的格式修改写回源文件。

        与 typo_check/expression_opt 不同：无需还原 marker，
        corrected 字段即为完整的 LaTeX 源码，直接写回即可。

        参数：
            file_path: 文件路径
            changes: [{ "paragraph_index": 0, "new_text": "格式化后 LaTeX" }]

        返回：
            { applied: N, total: M }
        """
        pm = get_project_manager()
        applied = 0

        for change in changes:
            idx = change["paragraph_index"]
            new_text = change["new_text"]

            pm.apply_change(file_path, idx, new_text)
            applied += 1
            self._logger.info(
                f"Applied formatting change to paragraph {idx} in {file_path}")

        return {"applied": applied, "total": len(changes)}
