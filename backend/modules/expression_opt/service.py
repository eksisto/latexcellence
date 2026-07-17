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

import asyncio
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.core.project_manager import get_project_manager
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.utils.latex_parser import LatexParser, MARKER_PATTERN
from backend.modules.expression_opt.prompt_manager import get_prompt_manager
from backend.utils.input_resolver import resolve_inputs
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

class ExpressionOptService:
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

    def __init__(self):
        self._logger = get_logger("expression_opt")
        self._parser = LatexParser()
        self._marker_maps: dict[str, dict[int, dict[str, str]]] = {}
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

    def _process_paragraphs(
        self, paragraphs: list, file_path: str
    ) -> list[dict[str, Any]]:
        """对单个文件的段落执行 marker 替换，返回结果列表。"""
        if file_path not in self._marker_maps:
            self._marker_maps[file_path] = {}
        result = []
        for para in paragraphs:
            stripped, marker_map = self._parser.strip_to_plain_text(para.text)
            self._marker_maps[file_path][para.index] = marker_map
            result.append({
                "index": para.index,
                "text": para.text,
                "plain_text": stripped,
                "line_start": para.line_start,
                "line_end": para.line_end,
                "file_path": file_path,
            })
        return result

    def preview_paragraphs(
        self, file_path: str, resolve_input: bool = False
    ) -> dict[str, Any]:
        """
        预览段落：返回每段的原始文本和剥离后的纯文本。

        参数：
            file_path: 文件路径
            resolve_input: 是否递归展开 \\input 引用

        返回：
            { paragraphs: [...], file_path: "...", total: N }
        """
        pm = get_project_manager()
        files = self._get_files(file_path, resolve_input)

        self._file_path = file_path
        self._marker_maps = {}

        all_paragraphs: list[dict[str, Any]] = []
        for f in files:
            paragraphs = pm.get_paragraphs(f)
            all_paragraphs.extend(self._process_paragraphs(paragraphs, f))

        self._logger.info(
            f"Previewed {len(all_paragraphs)} paragraphs from "
            f"{len(files)} file(s) starting at {file_path}"
        )
        return {
            "paragraphs": all_paragraphs,
            "file_path": file_path,
            "total": len(all_paragraphs),
        }

    async def optimize(
        self,
        file_path: str,
        paragraph_indices: Optional[list[int]] = None,
        resolve_input: bool = False,
    ) -> dict[str, Any]:
        """
        对段落进行表达优化（非流式）。

        参数：
            file_path: 文件路径
            paragraph_indices: 要优化的段落索引列表，None 表示全部
            resolve_input: 是否递归展开 \\input 引用

        返回：
            { results: [...], file_path: "..." }
        """
        pm = get_project_manager()
        cfg = get_config()
        files = self._get_files(file_path, resolve_input)

        self._file_path = file_path
        self._marker_maps = {}

        all_para_data: list[dict[str, Any]] = []
        for f in files:
            paragraphs = pm.get_paragraphs(f)
            for para in paragraphs:
                stripped, marker_map = self._parser.strip_to_plain_text(para.text)
                if f not in self._marker_maps:
                    self._marker_maps[f] = {}
                self._marker_maps[f][para.index] = marker_map
                all_para_data.append({
                    "file_path": f,
                    "para_index": para.index,
                    "plain_text": stripped,
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
            self._logger.warning("No API key configured, returning plain text only")
            results = []
            for d in targets:
                results.append({
                    "paragraph_index": d["para_index"],
                    "original": d["plain_text"],
                    "optimized": d["plain_text"],
                    "changes": [],
                    "text": d["text"],
                    "file_path": d["file_path"],
                })
            return {"results": results, "file_path": file_path}

        llm = get_llm_client()
        batch_size = cfg.features.expression_opt.batch_size
        results: list[dict[str, Any]] = [None] * len(targets)  # type: ignore[list-item]

        for batch_start in range(0, len(targets), batch_size):
            batch = targets[batch_start : batch_start + batch_size]
            tasks = []

            for d in batch:
                stripped = d["plain_text"]
                if not stripped.strip():
                    tasks.append(self._empty_result(
                        d["para_index"], stripped, d["text"], d["file_path"]
                    ))
                else:
                    tasks.append(
                        self._optimize_one_paragraph(
                            llm, len(tasks), d["para_index"],
                            stripped, d["text"], d["file_path"],
                        )
                    )

            batch_results = await asyncio.gather(*tasks)
            for slot, d in enumerate(batch):
                results[batch_start + slot] = batch_results[slot]

            if batch_start + batch_size < len(targets):
                await asyncio.sleep(cfg.llm.request_interval)

        return {"results": results, "file_path": file_path}

    async def optimize_stream(
        self, file_path: str, resolve_input: bool = False
    ):
        """
        表达优化流式版本（SSE）。

        逐段 yield 事件：progress / result / error / done。
        """
        pm = get_project_manager()
        cfg = get_config()
        files = self._get_files(file_path, resolve_input)

        self._file_path = file_path
        self._marker_maps = {}

        all_para_data: list[dict[str, Any]] = []
        for f in files:
            paragraphs = pm.get_paragraphs(f)
            for para in paragraphs:
                stripped, marker_map = self._parser.strip_to_plain_text(para.text)
                if f not in self._marker_maps:
                    self._marker_maps[f] = {}
                self._marker_maps[f][para.index] = marker_map
                all_para_data.append({
                    "file_path": f,
                    "para_index": para.index,
                    "plain_text": stripped,
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
            stripped = d["plain_text"]
            f = d["file_path"]
            idx = d["para_index"]

            if not stripped.strip():
                yield {
                    "type": "result",
                    "paragraph_index": idx,
                    "data": {
                        "paragraph_index": idx,
                        "original": stripped,
                        "optimized": stripped,
                        "changes": [],
                        "text": d["text"],
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
                result = await self._optimize_one_paragraph(
                    llm, 0, idx, stripped, d["text"], f
                )
                yield {"type": "result", "paragraph_index": idx, "data": result}
            except Exception as e:
                self._logger.error(
                    f"Error optimizing paragraph {idx} in {f}: {e}"
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

    async def _optimize_one_paragraph(
        self,
        llm,
        slot: int,
        idx: int,
        plain_text: str,
        original_text: str,
        file_path: str,
    ) -> dict[str, Any]:
        """
        调用 LLM 优化单个段落的表达，含 Pydantic 校验 + 重试 + marker 保护验证。
        """
        input_markers = set(MARKER_PATTERN.findall(plain_text))

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

            output_markers = set(MARKER_PATTERN.findall(optimized))
            missing = input_markers - output_markers
            extra = output_markers - input_markers

            if missing or extra:
                self._logger.warning(
                    f"Paragraph {idx} in {file_path}: marker mismatch — "
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
                    "file_path": file_path,
                    "marker_error": True,
                }

            return {
                "paragraph_index": idx,
                "original": validated.original,
                "optimized": optimized,
                "changes": [c.model_dump() for c in validated.changes],
                "text": original_text,
                "plain_text": plain_text,
                "file_path": file_path,
            }

        except ValueError as e:
            self._logger.warning(
                f"Paragraph {idx} in {file_path}: "
                f"validation retries exhausted — {e}"
            )
            return {
                "paragraph_index": idx,
                "original": plain_text,
                "optimized": plain_text,
                "changes": [],
                "text": original_text,
                "plain_text": plain_text,
                "file_path": file_path,
            }
        except Exception as e:
            self._logger.error(
                f"Paragraph {idx} in {file_path}: LLM call failed — {e}"
            )
            return {
                "paragraph_index": idx,
                "original": plain_text,
                "optimized": plain_text,
                "changes": [],
                "text": original_text,
                "plain_text": plain_text,
                "file_path": file_path,
            }

    async def _empty_result(
        self, idx: int, plain_text: str, original_text: str,
        file_path: str,
    ) -> dict[str, Any]:
        return {
            "paragraph_index": idx,
            "original": plain_text,
            "optimized": plain_text,
            "changes": [],
            "text": original_text,
            "plain_text": plain_text,
            "file_path": file_path,
        }

    def apply_changes(
        self, file_path: str, changes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        将用户确认的修改写回源文件。

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
            new_plain_text = change["new_text"]
            target_file = change.get("file_path", file_path)

            file_maps = self._marker_maps.get(target_file, {})
            marker_map = file_maps.get(idx, {})

            restored = self._parser.restore_from_markers(new_plain_text, marker_map)

            pm.apply_change(target_file, idx, restored)
            applied += 1
            self._logger.info(
                f"Applied expression change to paragraph {idx} in {target_file}"
            )

        return {"applied": applied, "total": len(changes)}
