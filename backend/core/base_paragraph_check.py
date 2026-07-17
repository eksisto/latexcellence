"""
逐段检查服务基类。

封装四个逐段检查模块（typo_check / expression_opt / formatting /
terminology_check）的共享逻辑：

    1. 文件解析与段落提取（含 \\input 展开）
    2. LaTeX marker 替换与还原
    3. LLM 逐段调用编排（批量 + 流式）
    4. 结果写回源文件

子类只需实现以下 hooks：
    _get_system_prompt()          → LLM system prompt
    _build_user_content(text)     → LLM user message
    _get_model_cls()              → Pydantic 响应模型
    _get_llm_params()             → {"temperature": ..., "max_tokens": ...}
    _get_batch_size()             → 批次大小
    _build_success_result(...)    → 从 validated model 构建结果 dict
"""

import asyncio
from typing import Any, ClassVar, Optional

from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.core.project_manager import get_project_manager
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.utils.latex_parser import LatexParser, MARKER_PATTERN
from backend.utils.input_resolver import resolve_inputs
from backend.utils.json_utils import retry_with_validation


class ApplyChangeItem(BaseModel):
    """逐段检查共享：用户确认的修改项请求模型。"""
    paragraph_index: int
    new_text: str
    file_path: Optional[str] = None


class BaseParagraphCheckService:
    """
    逐段检查服务基类。

    子类通过类变量控制行为差异：
        - _output_field: 结果字段名（"corrected" 或 "optimized"）
        - use_marker_protection: 是否启用 marker 保护策略
    """

    _output_field: ClassVar[str] = "corrected"
    use_marker_protection: ClassVar[bool] = True

    def __init__(self, logger_name: str):
        self._logger = get_logger(logger_name)
        self._parser = LatexParser()
        self._marker_maps: dict[str, dict[int, dict[str, str]]] = {}
        self._file_path: Optional[str] = None

    # ── 文件与段落 ──────────────────────────────────────────────────────────

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

    def _collect_para_data(
        self, files: list[str]
    ) -> list[dict[str, Any]]:
        """
        收集所有段落数据，统一字段名。

        返回的每个 dict 包含：
            file_path, para_index, text, plain_text

        当 use_marker_protection=True 时，同时填充 _marker_maps。
        """
        pm = get_project_manager()
        all_data: list[dict[str, Any]] = []
        for f in files:
            paragraphs = pm.get_paragraphs(f)
            for para in paragraphs:
                if self.use_marker_protection:
                    stripped, marker_map = self._parser.strip_to_plain_text(
                        para.text
                    )
                    if f not in self._marker_maps:
                        self._marker_maps[f] = {}
                    self._marker_maps[f][para.index] = marker_map
                else:
                    stripped = self._parser.strip_to_plain_text(para.text)[0]
                all_data.append({
                    "file_path": f,
                    "para_index": para.index,
                    "text": para.text,
                    "plain_text": stripped,
                })
        return all_data

    def _filter_targets(
        self,
        all_para_data: list[dict[str, Any]],
        file_path: str,
        paragraph_indices: Optional[list[int]],
    ) -> list[dict[str, Any]]:
        """按段落索引过滤目标段落。"""
        if paragraph_indices is not None:
            index_set = set(paragraph_indices)
            return [
                d for d in all_para_data
                if d["file_path"] == file_path
                and d["para_index"] in index_set
            ]
        return all_para_data

    # ── 结果构建 ────────────────────────────────────────────────────────────

    def _make_fallback(
        self,
        idx: int,
        text: str,
        original_text: str,
        file_path: str,
        **extra: Any,
    ) -> dict[str, Any]:
        """
        LLM 失败或 marker 不匹配时的回退结果。

        使用 self._output_field 动态设置字段名（"corrected" 或 "optimized"）。
        """
        return {
            "paragraph_index": idx,
            "original": text,
            self._output_field: text,
            "changes": [],
            "text": original_text,
            "plain_text": text,
            "file_path": file_path,
            **extra,
        }

    def _make_empty_from_data(self, d: dict[str, Any]) -> dict[str, Any]:
        """为空白段落生成空结果（从 _collect_para_data 的 dict 构建）。"""
        return self._make_fallback(
            d["para_index"],
            d["plain_text"],
            d["text"],
            d["file_path"],
        )

    # ── 异步编排 ────────────────────────────────────────────────────────────

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
        默认实现：marker 保护 + LLM 调用 + Pydantic 校验 + 异常回退。

        子类可覆写此方法以自定义单段检查逻辑（如 formatting 无 marker 保护）。
        """
        input_markers: set[str] = set()
        if self.use_marker_protection:
            input_markers = set(MARKER_PATTERN.findall(text))

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

            output_text = getattr(validated, self._output_field)

            if self.use_marker_protection:
                output_markers = set(MARKER_PATTERN.findall(output_text))
                missing = input_markers - output_markers
                extra = output_markers - input_markers

                if missing or extra:
                    self._logger.warning(
                        f"Paragraph {idx} in {file_path}: marker mismatch — "
                        f"missing={len(missing)}, extra={len(extra)}. "
                        f"Falling back to original text."
                    )
                    return self._make_fallback(
                        idx, text, original_text, file_path,
                        marker_error=True,
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

    async def _run_batch_check(
        self,
        file_path: str,
        paragraph_indices: Optional[list[int]] = None,
        resolve_input: bool = False,
    ) -> dict[str, Any]:
        """
        批量逐段检查（非流式）。

        共享编排骨架：文件解析 → 段落收集 → 过滤 → 分批 LLM → 汇总结果。
        """
        cfg = get_config()
        files = self._get_files(file_path, resolve_input)

        self._file_path = file_path
        if self.use_marker_protection:
            self._marker_maps = {}

        all_para_data = self._collect_para_data(files)
        targets = self._filter_targets(
            all_para_data, file_path, paragraph_indices)

        if not cfg.llm.api_key:
            self._logger.warning(
                "No API key configured, returning plain text only"
            )
            return self._no_api_key_batch_fallback(targets, file_path)

        llm = get_llm_client()
        batch_size = self._get_batch_size()
        sem = asyncio.Semaphore(cfg.llm.max_concurrency)
        results: list[Optional[dict[str, Any]]] = [None] * len(targets)

        async def _limited(coro):
            async with sem:
                return await coro

        for batch_start in range(0, len(targets), batch_size):
            batch = targets[batch_start: batch_start + batch_size]
            tasks = []

            for d in batch:
                check_text = d["plain_text"] if self.use_marker_protection else d["text"]
                if not check_text.strip():
                    tasks.append(self._make_empty_from_data(d))
                else:
                    tasks.append(
                        self._check_one_paragraph(
                            llm, len(tasks), d["para_index"],
                            check_text, d["text"], d["file_path"],
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

    async def _run_stream_check(
        self,
        file_path: str,
        resolve_input: bool = False,
    ):
        """
        流式逐段检查（SSE）。

        逐段 yield 事件：progress / result / error / done。
        """
        cfg = get_config()
        files = self._get_files(file_path, resolve_input)

        self._file_path = file_path
        if self.use_marker_protection:
            self._marker_maps = {}

        all_para_data = self._collect_para_data(files)

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
            check_text = d["plain_text"] if self.use_marker_protection else d["text"]

            if not check_text.strip():
                empty = self._make_fallback(
                    idx, check_text, d["text"], f,
                )
                yield {"type": "result", "paragraph_index": idx, "data": empty}
                yield {
                    "type": "progress",
                    "current": i + 1,
                    "total": total,
                    "file_path": f,
                }
                continue

            try:
                result = await self._check_one_paragraph(
                    llm, 0, idx, check_text, d["text"], f
                )
                yield {"type": "result", "paragraph_index": idx, "data": result}
            except Exception as e:
                self._logger.error(
                    f"Error checking paragraph {idx} in {f}: {e}"
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

    def _no_api_key_batch_fallback(
        self,
        targets: list[dict[str, Any]],
        file_path: str,
    ) -> dict[str, Any]:
        """无 API Key 时返回纯文本结果。"""
        results = []
        for d in targets:
            results.append(self._make_fallback(
                d["para_index"],
                d["plain_text"] if self.use_marker_protection else d["text"],
                d["text"],
                d["file_path"],
            ))
        return {"results": results, "file_path": file_path}

    # ── 写回 ────────────────────────────────────────────────────────────────

    def apply_changes(
        self, file_path: str, changes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        将用户确认的修改写回源文件。

        当 use_marker_protection=True 时，通过 marker_map 将纯文本还原为
        完整 LaTeX 后写回；否则直接写回。

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

            if self.use_marker_protection:
                file_maps = self._marker_maps.get(target_file, {})
                marker_map = file_maps.get(idx, {})
                restored = self._parser.restore_from_markers(
                    new_text, marker_map
                )
            else:
                restored = new_text

            pm.apply_change(target_file, idx, restored)
            applied += 1
            self._logger.info(
                f"Applied change to paragraph {idx} in {target_file}"
            )

        return {"applied": applied, "total": len(changes)}

    # ── Hook 方法（子类必须实现）───────────────────────────────────────────

    def _get_system_prompt(self) -> str:
        """返回 LLM system prompt。"""
        raise NotImplementedError

    def _build_user_content(self, text: str) -> str:
        """返回 LLM user message content。"""
        raise NotImplementedError

    def _get_model_cls(self):
        """返回 Pydantic 响应模型类。"""
        raise NotImplementedError

    def _get_llm_params(self) -> dict[str, Any]:
        """返回 LLM 调用参数（temperature, max_tokens 等）。"""
        raise NotImplementedError

    def _get_batch_size(self) -> int:
        """返回批次大小。"""
        raise NotImplementedError

    def _build_success_result(
        self,
        idx: int,
        validated,
        original_text: str,
        file_path: str,
        plain_text: str,
    ) -> dict[str, Any]:
        """从 validated Pydantic model 构建成功结果 dict。"""
        raise NotImplementedError
