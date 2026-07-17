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

import asyncio
from typing import Any, Optional

from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.core.project_manager import get_project_manager
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.utils.latex_parser import LatexParser
from backend.utils.input_resolver import resolve_inputs
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

    支持 \\input 解析：
        preview = svc.preview_paragraphs("main.tex", resolve_input=True)
        results = await svc.check_formatting_stream("main.tex", resolve_input=True)
    """

    def __init__(self):
        self._logger = get_logger("formatting")
        self._parser = LatexParser()
        self._file_path: Optional[str] = None

    def _get_files(
        self, file_path: str, resolve_input: bool
    ) -> list[str]:
        """解析 \\input 依赖，返回待处理文件列表。"""
        if not resolve_input:
            return [file_path]
        pm = get_project_manager()
        root = pm._project_info.root_path
        files = resolve_inputs(file_path, root)
        self._logger.info(
            f"Resolved \\input tree from {file_path}: {len(files)} file(s)"
        )
        return files

    def preview_paragraphs(
        self, file_path: str, resolve_input: bool = False
    ) -> dict[str, Any]:
        """
        预览段落：返回每段的原始 LaTeX 源码和剥离后的纯文本。

        原始源码（text）将交给 LLM 进行排版优化；
        纯文本（plain_text）仅供前端 ParagraphContext 展示上下文。

        参数：
            file_path: 文件路径
            resolve_input: 是否递归展开 \\input 引用

        返回：
            { paragraphs: [...], file_path: "...", total: N }
        """
        pm = get_project_manager()
        files = self._get_files(file_path, resolve_input)

        self._file_path = file_path

        all_paragraphs: list[dict[str, Any]] = []
        for f in files:
            paragraphs = pm.get_paragraphs(f)
            for para in paragraphs:
                stripped, _ = self._parser.strip_to_plain_text(para.text)
                all_paragraphs.append({
                    "index": para.index,
                    "text": para.text,
                    "plain_text": stripped,
                    "line_start": para.line_start,
                    "line_end": para.line_end,
                    "file_path": f,
                })

        self._logger.info(
            f"Previewed {len(all_paragraphs)} paragraphs from "
            f"{len(files)} file(s) starting at {file_path}"
        )
        return {
            "paragraphs": all_paragraphs,
            "file_path": file_path,
            "total": len(all_paragraphs),
        }

    async def check_formatting(
        self,
        file_path: str,
        paragraph_indices: Optional[list[int]] = None,
        resolve_input: bool = False,
    ) -> dict[str, Any]:
        """
        对段落进行源码格式化检查（非流式）。

        参数：
            file_path: 文件路径
            paragraph_indices: 要格式化的段落索引列表，None 表示全部
            resolve_input: 是否递归展开 \\input 引用

        返回：
            { results: [...], file_path: "..." }
        """
        pm = get_project_manager()
        cfg = get_config()
        files = self._get_files(file_path, resolve_input)

        self._file_path = file_path

        all_para_data: list[dict[str, Any]] = []
        for f in files:
            paragraphs = pm.get_paragraphs(f)
            for para in paragraphs:
                all_para_data.append({
                    "file_path": f,
                    "para_index": para.index,
                    "text": para.text,
                })

        if paragraph_indices is not None:
            targets = [
                d for d in all_para_data
                if (d["file_path"], d["para_index"]) in {
                    (file_path, i) for i in paragraph_indices
                }
            ]
        else:
            targets = all_para_data

        if not cfg.llm.api_key:
            self._logger.warning(
                "No API key configured, returning original only")
            results = []
            for d in targets:
                results.append({
                    "paragraph_index": d["para_index"],
                    "original": d["text"],
                    "corrected": d["text"],
                    "changes": [],
                    "text": d["text"],
                    "file_path": d["file_path"],
                })
            return {"results": results, "file_path": file_path}

        llm = get_llm_client()
        batch_size = cfg.features.formatting.batch_size
        sem = asyncio.Semaphore(cfg.llm.max_concurrency)
        results: list[dict[str, Any]] = [None] * \
            len(targets)  # type: ignore[list-item]

        async def _limited(coro):
            async with sem:
                return await coro

        for batch_start in range(0, len(targets), batch_size):
            batch = targets[batch_start: batch_start + batch_size]
            tasks = []

            for d in batch:
                if not d["text"].strip():
                    tasks.append(self._empty_result(
                        d["para_index"], d["text"], d["file_path"]
                    ))
                else:
                    tasks.append(
                        self._check_one_paragraph(
                            llm, len(tasks), d["para_index"],
                            d["text"], d["file_path"],
                        )
                    )

            batch_results = await asyncio.gather(
                *(_limited(t) for t in tasks)
            )
            for slot, d in enumerate(batch):
                results[batch_start + slot] = batch_results[slot]

            if batch_start + batch_size < len(targets):
                await asyncio.sleep(cfg.llm.request_interval)

        return {"results": results, "file_path": file_path}

    async def check_formatting_stream(
        self, file_path: str, resolve_input: bool = False
    ):
        """
        源码格式化流式版本（SSE）。

        逐段 yield 事件：progress / result / error / done。
        """
        pm = get_project_manager()
        cfg = get_config()
        files = self._get_files(file_path, resolve_input)

        self._file_path = file_path

        all_para_data: list[dict[str, Any]] = []
        for f in files:
            paragraphs = pm.get_paragraphs(f)
            for para in paragraphs:
                all_para_data.append({
                    "file_path": f,
                    "para_index": para.index,
                    "text": para.text,
                })

        total = len(all_para_data)
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

        for i, d in enumerate(all_para_data):
            f = d["file_path"]
            idx = d["para_index"]
            text = d["text"]

            if not text.strip():
                yield {
                    "type": "result",
                    "paragraph_index": idx,
                    "data": {
                        "paragraph_index": idx,
                        "original": text,
                        "corrected": text,
                        "changes": [],
                        "text": text,
                        "file_path": f,
                    },
                }
                yield {
                    "type": "progress",
                    "current": i + 1,
                    "total": total,
                    "file_path": f,
                }
                continue

            try:
                result = await self._check_one_paragraph(
                    llm, 0, idx, text, f
                )
                yield {"type": "result", "paragraph_index": idx, "data": result}
            except Exception as e:
                self._logger.error(
                    f"Error formatting paragraph {idx} in {f}: {e}"
                )
                yield {
                    "type": "error",
                    "message": f"{f} P{idx}: {e}",
                    "retrying": False,
                }

            yield {
                "type": "progress",
                "current": i + 1,
                "total": total,
                "file_path": f,
            }
            await asyncio.sleep(cfg.llm.request_interval)

        yield {"type": "done"}

    async def _check_one_paragraph(
        self,
        llm,
        slot: int,
        idx: int,
        original_text: str,
        file_path: str,
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
            file_path: 段落所属文件路径

        返回：
            包含 paragraph_index / original / corrected / changes / file_path 的 dict
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
                    f"Paragraph {idx} in {file_path}: "
                    f"empty corrected output, falling back"
                )
                return {
                    "paragraph_index": idx,
                    "original": original_text,
                    "corrected": original_text,
                    "changes": [],
                    "text": original_text,
                    "file_path": file_path,
                }

            return {
                "paragraph_index": idx,
                "original": validated.original,
                "corrected": corrected,
                "changes": [c.model_dump() for c in validated.changes],
                "text": original_text,
                "file_path": file_path,
            }

        except ValueError as e:
            self._logger.warning(
                f"Paragraph {idx} in {file_path}: "
                f"validation retries exhausted — {e}"
            )
            return {
                "paragraph_index": idx,
                "original": original_text,
                "corrected": original_text,
                "changes": [],
                "text": original_text,
                "file_path": file_path,
            }
        except Exception as e:
            self._logger.error(
                f"Paragraph {idx} in {file_path}: LLM call failed — {e}"
            )
            return {
                "paragraph_index": idx,
                "original": original_text,
                "corrected": original_text,
                "changes": [],
                "text": original_text,
                "file_path": file_path,
            }

    async def _empty_result(
        self, idx: int, original_text: str, file_path: str
    ) -> dict[str, Any]:
        """为空白段落生成空结果。"""
        return {
            "paragraph_index": idx,
            "original": original_text,
            "corrected": original_text,
            "changes": [],
            "text": original_text,
            "file_path": file_path,
        }

    def apply_changes(
        self, file_path: str, changes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        将用户确认的格式修改写回源文件。

        与 typo_check/expression_opt 不同：无需还原 marker，
        corrected 字段即为完整的 LaTeX 源码，直接写回即可。

        参数：
            file_path: 默认文件路径（当 change 中无 file_path 时使用）
            changes: [{ "paragraph_index": 0, "new_text": "...", "file_path": "..." }]

        返回：
            { applied: N, total: M }
        """
        pm = get_project_manager()
        applied = 0

        for change in changes:
            idx = change["paragraph_index"]
            new_text = change["new_text"]
            target_file = change.get("file_path", file_path)

            pm.apply_change(target_file, idx, new_text)
            applied += 1
            self._logger.info(
                f"Applied formatting change to paragraph {idx} in {target_file}")

        return {"applied": applied, "total": len(changes)}
