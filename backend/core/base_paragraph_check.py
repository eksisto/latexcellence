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
import copy
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.utils.latex_parser import LatexParser, MARKER_PATTERN
from backend.utils.input_resolver import resolve_inputs
from backend.utils.json_utils import retry_with_validation
from backend.utils.file_ops import compute_md5


@dataclass
class CheckOperationContext:
    """
    一次检查操作的完整上下文。

    保存 marker 映射、文件 MD5 和项目上下文，避免将请求相关状态
    保存在服务单例字段中。apply 时通过 operation_id 查找对应上下文，
    确保 marker 还原正确。
    """

    operation_id: str
    marker_maps: dict[str, dict[int, dict[str, str]]]
    file_path: str
    file_md5s: dict[str, str]
    created_at: float = field(default_factory=time.time)


class ApplyChangeItem(BaseModel):
    """逐段检查共享：用户确认的修改项请求模型。"""
    paragraph_index: int
    new_text: str
    file_path: str | None = None


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
        self._file_path: str | None = None
        self._operation_contexts: dict[str, CheckOperationContext] = {}

    # ── 操作上下文管理 ──────────────────────────────────────────────────────

    def _generate_operation_id(self) -> str:
        """生成唯一操作 ID。"""
        return uuid.uuid4().hex[:12]

    def _capture_file_md5s(
        self, files: list[str], workspace_id: str
    ) -> dict[str, str]:
        """收集所有文件当前的 MD5 快照。"""
        pm = get_project_manager_for_workspace(workspace_id)
        root = Path(pm.root_path)
        md5s: dict[str, str] = {}
        for f in files:
            full_path = root / f
            if full_path.exists():
                md5s[f] = compute_md5(full_path)
        return md5s

    def _store_context(
        self,
        operation_id: str,
        marker_maps: dict[str, dict[int, dict[str, str]]],
        file_path: str,
        file_md5s: dict[str, str],
    ) -> None:
        """保存操作上下文（深拷贝 marker_maps），并清理过期上下文。"""
        self._operation_contexts[operation_id] = CheckOperationContext(
            operation_id=operation_id,
            marker_maps=copy.deepcopy(marker_maps),
            file_path=file_path,
            file_md5s=file_md5s,
        )
        self._cleanup_old_contexts()

    def _get_context(
        self, operation_id: str
    ) -> CheckOperationContext | None:
        """按 operation_id 查找操作上下文。"""
        return self._operation_contexts.get(operation_id)

    def _cleanup_old_contexts(self, max_age_seconds: int = 3600) -> None:
        """清理超过 TTL 的过期上下文。"""
        now = time.time()
        expired = [
            oid
            for oid, ctx in self._operation_contexts.items()
            if now - ctx.created_at > max_age_seconds
        ]
        for oid in expired:
            del self._operation_contexts[oid]
            self._logger.debug(f"Cleaned up expired operation context: {oid}")

    # ── 文件与段落 ──────────────────────────────────────────────────────────

    def _get_files(
        self, file_path: str, resolve_input: bool, workspace_id: str
    ) -> list[str]:
        """解析 \\input 依赖，返回待处理文件列表。"""
        if not resolve_input:
            return [file_path]
        pm = get_project_manager_for_workspace(workspace_id)
        root = pm.root_path
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
        self, file_path: str, resolve_input: bool = False, workspace_id: str = ""
    ) -> dict[str, Any]:
        """
        预览段落：返回每段的原始文本和剥离后的纯文本。

        参数：
            file_path: 文件路径
            resolve_input: 是否递归展开 \\input 引用
            workspace_id: 工作区唯一标识符

        返回：
            { paragraphs: [...], file_path: "...", total: N, operation_id: "..." }
        """
        pm = get_project_manager_for_workspace(workspace_id)
        files = self._get_files(file_path, resolve_input, workspace_id)

        self._file_path = file_path
        self._marker_maps = {}

        all_paragraphs: list[dict[str, Any]] = []
        for f in files:
            paragraphs = pm.get_paragraphs(f)
            all_paragraphs.extend(self._process_paragraphs(paragraphs, f))

        operation_id = self._generate_operation_id()
        file_md5s = self._capture_file_md5s(files, workspace_id)
        self._store_context(
            operation_id, self._marker_maps, file_path, file_md5s
        )

        self._logger.info(
            f"Previewed {len(all_paragraphs)} paragraphs from "
            f"{len(files)} file(s) starting at {file_path} "
            f"[op={operation_id}]"
        )
        return {
            "paragraphs": all_paragraphs,
            "file_path": file_path,
            "total": len(all_paragraphs),
            "operation_id": operation_id,
        }

    def _collect_para_data(
        self, files: list[str], workspace_id: str
    ) -> list[dict[str, Any]]:
        """
        收集所有段落数据，统一字段名。

        返回的每个 dict 包含：
            file_path, para_index, text, plain_text

        当 use_marker_protection=True 时，同时填充 _marker_maps。
        """
        pm = get_project_manager_for_workspace(workspace_id)
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
        paragraph_indices: list[int] | None,
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
        input_markers: list[str] = []
        if self.use_marker_protection:
            input_markers = MARKER_PATTERN.findall(text)

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
                output_markers = MARKER_PATTERN.findall(output_text)
                in_cnt = Counter(input_markers)
                out_cnt = Counter(output_markers)

                # 1) 计数校验：拦截 marker 缺失、多余、重复
                if in_cnt != out_cnt:
                    self._logger.warning(
                        f"Paragraph {idx} in {file_path}: marker count/multiplicity "
                        f"mismatch (input={len(input_markers)}, "
                        f"output={len(output_markers)}). "
                        f"Falling back to original text."
                    )
                    return self._make_fallback(
                        idx, text, original_text, file_path,
                        marker_error=True,
                    )

                # 2) 顺序校验：拦截 marker 重排/错位
                #    （纯文本被移动但 marker 序列不变属合法编辑，不应误伤）
                if input_markers != output_markers:
                    self._logger.warning(
                        f"Paragraph {idx} in {file_path}: marker order mismatch "
                        f"(reordered markers). Falling back to original text."
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
        paragraph_indices: list[int] | None = None,
        resolve_input: bool = False,
        workspace_id: str = "",
    ) -> dict[str, Any]:
        """
        批量逐段检查（非流式）。

        共享编排骨架：文件解析 → 段落收集 → 过滤 → 分批 LLM → 汇总结果。
        """
        cfg = get_config()
        files = self._get_files(file_path, resolve_input, workspace_id)

        self._file_path = file_path
        if self.use_marker_protection:
            self._marker_maps = {}

        all_para_data = self._collect_para_data(files, workspace_id)
        targets = self._filter_targets(
            all_para_data, file_path, paragraph_indices)

        operation_id = self._generate_operation_id()
        if self.use_marker_protection:
            file_md5s = self._capture_file_md5s(files, workspace_id)
            self._store_context(
                operation_id, self._marker_maps, file_path, file_md5s
            )

        if not cfg.llm.api_key:
            self._logger.warning(
                "No API key configured, returning plain text only"
            )
            return self._no_api_key_batch_fallback(targets, file_path, operation_id)

        llm = get_llm_client()
        batch_size = self._get_batch_size()
        sem = asyncio.Semaphore(cfg.llm.max_concurrency)
        results: list[dict[str, Any] | None] = [None] * len(targets)

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

        self._logger.info(
            f"Batch check completed for {file_path} "
            f"[op={operation_id}]"
        )
        return {
            "results": results,
            "file_path": file_path,
            "operation_id": operation_id,
        }

    async def _run_stream_check(
        self,
        file_path: str,
        resolve_input: bool = False,
        workspace_id: str = "",
    ):
        """
        流式逐段检查（SSE）。

        逐段 yield 事件：progress / result / error / done。
        done 事件包含 operation_id，前端 apply 时需回传。
        """
        cfg = get_config()
        files = self._get_files(file_path, resolve_input, workspace_id)

        self._file_path = file_path
        if self.use_marker_protection:
            self._marker_maps = {}

        all_para_data = self._collect_para_data(files, workspace_id)

        operation_id = self._generate_operation_id()
        if self.use_marker_protection:
            file_md5s = self._capture_file_md5s(files, workspace_id)
            self._store_context(
                operation_id, self._marker_maps, file_path, file_md5s
            )

        total = len(all_para_data)
        yield {"type": "progress", "current": 0, "total": total}

        if not cfg.llm.api_key:
            yield {
                "type": "error",
                "message": "No API key configured",
                "retrying": False,
            }
            yield {"type": "done", "operation_id": operation_id}
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

        self._logger.info(
            f"Stream check completed for {file_path} "
            f"[op={operation_id}]"
        )
        yield {"type": "done", "operation_id": operation_id}

    def _no_api_key_batch_fallback(
        self,
        targets: list[dict[str, Any]],
        file_path: str,
        operation_id: str = "",
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
        return {"results": results, "file_path": file_path, "operation_id": operation_id}

    # ── 写回 ────────────────────────────────────────────────────────────────

    def apply_changes(
        self,
        file_path: str,
        changes: list[dict[str, Any]],
        workspace_id: str = "",
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        将用户确认的修改写回源文件。

        当 use_marker_protection=True 时，通过 operation_id 查找操作上下文，
        使用对应的 marker_map 将纯文本还原为完整 LaTeX 后写回；
        否则直接写回。

        参数：
            file_path: 默认文件路径（当 change 中无 file_path 时使用）
            changes: [{ "paragraph_index": 0, "new_text": "...", "file_path": "..." }]
            workspace_id: 工作区唯一标识符
            operation_id: 检查操作 ID，用于查找对应的 marker 映射

        返回：
            { applied: N, total: M }
        """
        pm = get_project_manager_for_workspace(workspace_id)
        applied = 0

        context = None
        if operation_id:
            context = self._get_context(operation_id)
            if context is None:
                self._logger.warning(
                    f"Operation context not found for {operation_id}, "
                    f"falling back to instance marker_maps"
                )

        for change in changes:
            idx = change["paragraph_index"]
            new_text = change["new_text"]
            target_file = change.get("file_path", file_path)

            if self.use_marker_protection:
                if context is not None:
                    file_maps = context.marker_maps.get(target_file, {})
                else:
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
                f" [op={operation_id or 'legacy'}]"
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
