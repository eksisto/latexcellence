"""
逐段检查服务基类。

封装四个逐段检查模块（typo_check / expression_opt / code_review /
terminology_check）的共享逻辑：

    1. 文件解析与段落提取（含 \\input 展开）
    2. LaTeX marker 替换与还原
    3. LLM 逐段调用编排（批量 + 流式）
    4. 结果写回源文件

子类只需实现以下 hooks：
    llm_role                     → LLM 槽位（multi-model-plan §6，类属性）
    _get_system_prompt()          → LLM system prompt
    _build_user_content(text)     → LLM user message
    _get_model_cls()              → Pydantic 响应模型
    _get_llm_params()             → {"temperature": ..., "max_tokens": ...}
    _get_batch_size()             → 批次大小
    _build_success_result(...)    → 从 validated model 构建结果 dict
"""

import asyncio
import copy
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from backend.core import access_control
from backend.core.logger import get_logger
from backend.core.model_router import get_client_for, has_api_key_for
from backend.core.multiuser import llm_governance
from backend.core.project_manager import FileConflictError
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.core.config import get_config
from backend.utils.latex_parser import LatexParser, MARKER_PATTERN
from backend.utils.input_resolver import resolve_inputs
from backend.utils.json_utils import retry_with_validation
from backend.utils.latex_json_guard import check_latex_text_lite
from backend.utils.file_ops import compute_md5
from backend.utils.path_guard import resolve_project_path


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


# ── 操作上下文全局存储 ──────────────────────────────────────────────────────
# 服务实例按请求创建（消除实例状态并发串扰），但 apply 是独立的 HTTP 请求，
# 必须能按 operation_id 找到 preview/check 阶段保存的上下文。
#
# v2 P7 多实例外置：多用户模式下上下文以 payload JSON 存 operation_contexts
# 表（apply 可落在另一副本）；进程内 dict 降级为无害的 L1 读缓存（命中免
# 查询）。单用户模式不建库，dict 即全部存储（行为与历史版本一致）。
_operation_contexts: dict[str, CheckOperationContext] = {}
_operation_contexts_lock = threading.Lock()


def _context_store_available() -> bool:
    """operation_contexts 表是否可用（多用户模式且库已初始化）。"""
    from backend.core.config import get_config
    from backend.core.multiuser import db

    try:
        if not get_config().multi_user.enabled:
            return False
    except RuntimeError:
        return False
    return db.is_initialized()


def _serialize_context(context: CheckOperationContext) -> dict[str, Any]:
    """CheckOperationContext → JSON payload dict（表存储口径）。"""
    return {
        "marker_maps": context.marker_maps,
        "file_path": context.file_path,
        "file_md5s": context.file_md5s,
        "created_at": context.created_at,
    }


def _deserialize_context(operation_id: str, payload: dict) -> CheckOperationContext:
    """JSON payload dict → CheckOperationContext（含原 created_at epoch）。

    marker_maps 的段落索引键为 int，JSON 对象键序列化后变 str，读取时
    逐文件还原为 int（apply 以 int 段落索引查表）。
    """
    marker_maps: dict[str, dict[int, dict[str, str]]] = {}
    for file_path, para_maps in (payload.get("marker_maps") or {}).items():
        restored: dict[int, dict[str, str]] = {}
        for key, mapping in (para_maps or {}).items():
            try:
                restored[int(key)] = mapping
            except (TypeError, ValueError):
                restored[key] = mapping
        marker_maps[file_path] = restored
    return CheckOperationContext(
        operation_id=operation_id,
        marker_maps=marker_maps,
        file_path=payload["file_path"],
        file_md5s=payload["file_md5s"],
        created_at=float(payload.get("created_at") or time.time()),
    )


def _cleanup_expired_contexts_locked(max_age_seconds: int = 3600) -> None:
    """清理超过 TTL 的过期上下文（L1 缓存侧；调用方必须已持有锁）。"""
    now = time.time()
    expired = [
        oid
        for oid, ctx in _operation_contexts.items()
        if now - ctx.created_at > max_age_seconds
    ]
    for oid in expired:
        del _operation_contexts[oid]


def store_operation_context(context: CheckOperationContext) -> None:
    """保存操作上下文并顺带清理过期项。

    多用户模式写穿到 operation_contexts 表（跨副本 apply 依赖），
    同时保留进程内 L1 缓存；单用户模式仅进程内（行为不变）。
    """
    if _context_store_available():
        from backend.core.multiuser import operation_contexts

        operation_contexts.store_context(
            context.operation_id, _serialize_context(context)
        )
        # 表存储自带 TTL 清理路径；此处仅维护 L1 缓存
        with _operation_contexts_lock:
            _operation_contexts[context.operation_id] = context
            _cleanup_expired_contexts_locked()
        return
    with _operation_contexts_lock:
        _operation_contexts[context.operation_id] = context
        _cleanup_expired_contexts_locked()


def get_operation_context(operation_id: str) -> CheckOperationContext | None:
    """按 operation_id 查找操作上下文（L1 缓存 → DB 回源）。"""
    with _operation_contexts_lock:
        cached = _operation_contexts.get(operation_id)
    if cached is not None:
        return cached
    if not _context_store_available():
        return None
    from backend.core.multiuser import operation_contexts

    payload = operation_contexts.load_context(operation_id)
    if payload is None:
        return None
    context = _deserialize_context(operation_id, payload)
    with _operation_contexts_lock:
        _operation_contexts[operation_id] = context
        _cleanup_expired_contexts_locked()
    return context


class ApplyChangeItem(BaseModel):
    """逐段检查共享：用户确认的修改项请求模型。"""

    paragraph_index: int
    new_text: str
    file_path: str | None = None
    expected_md5: str | None = None


def inject_expected_md5s(
    changes: list[dict[str, Any]],
    fallback_file: str,
    md5s_map: dict[str, str] | None,
) -> None:
    """为每个 change 补充 expected_md5（如果提供且未设置）。

    参数：
        changes: 序列化后的 change 字典列表（in-place 修改）
        fallback_file: 文件路径兜底值（request.file_path）
        md5s_map: 文件级 MD5 映射（request.expected_md5s）
    """
    if md5s_map:
        for change in changes:
            target_file = change.get("file_path", fallback_file)
            if "expected_md5" not in change or change["expected_md5"] is None:
                change["expected_md5"] = md5s_map.get(target_file)


def normalize_changes_contract(
    changes: list[dict[str, Any]],
    output_text: str,
    plain_text: str,
) -> list[dict[str, Any]]:
    """丢弃与主字段不一致的虚假 changes 条目（typo_check / expression_opt 共用）。

    changes 中的每条修改必须反映在主字段（corrected / optimized）里：
    若主字段与原文逐字符一致而 changes 非空，说明 LLM 编造了未实际应用的
    条目，直接清空，避免前端展示虚假问题。
    有修改时 changes 必须非空由 system prompt 约束，后端不伪造展示数据
    （type 是前端展示用的本地化字段，后端没有语言信息，合成条目会引入
    与正常条目不一致的 type 值）。
    """
    if output_text == plain_text:
        return []
    return changes


def has_unlisted_changes(
    changes: list[dict[str, Any]],
    output_text: str,
    plain_text: str,
) -> bool:
    """检测"文本已修改但 changes 为空"的段落（typo_check / expression_opt 共用）。

    LLM 修改了主字段却未输出明细条目时，后端不合成整段条目（type 是前端
    本地化展示字段，后端没有语言信息，合成会引入不一致的 type 值），而是
    返回 True 标记，由前端展示全文修改并提供应用/忽略操作，避免修改静默丢失。
    """
    return bool(output_text != plain_text and not changes)


class BaseParagraphCheckService:
    """
    逐段检查服务基类。

    子类通过类变量控制行为差异：
        - llm_role: LLM 槽位（multi-model-plan §6），客户端按槽位实时路由
        - _output_field: 结果字段名（"corrected" 或 "optimized"）
        - use_marker_protection: 是否启用 marker 保护策略
    """

    llm_role: ClassVar[str] = "default"
    _output_field: ClassVar[str] = "corrected"
    use_marker_protection: ClassVar[bool] = True

    def __init__(self, logger_name: str):
        self._logger = get_logger(logger_name)
        self._parser = LatexParser()
        self._marker_maps: dict[str, dict[int, dict[str, str]]] = {}
        self._file_path: str | None = None

    # ── 操作上下文管理 ──────────────────────────────────────────────────────

    def _generate_operation_id(self) -> str:
        """生成唯一操作 ID。"""
        return uuid.uuid4().hex[:12]

    def _capture_file_md5s(self, files: list[str], workspace_id: str) -> dict[str, str]:
        """收集所有文件当前的 MD5 快照。"""
        pm = get_project_manager_for_workspace(workspace_id)
        root = Path(pm.root_path)
        md5s: dict[str, str] = {}
        for f in files:
            full_path = resolve_project_path(root, f)
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
        store_operation_context(
            CheckOperationContext(
                operation_id=operation_id,
                marker_maps=copy.deepcopy(marker_maps),
                file_path=file_path,
                file_md5s=file_md5s,
            )
        )

    def _get_context(self, operation_id: str) -> CheckOperationContext | None:
        """按 operation_id 查找操作上下文。"""
        return get_operation_context(operation_id)

    def _cleanup_old_contexts(self, max_age_seconds: int = 3600) -> None:
        """清理超过 TTL 的过期上下文（兼容旧调用方）。"""
        with _operation_contexts_lock:
            _cleanup_expired_contexts_locked(max_age_seconds)

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
            result.append(
                {
                    "index": para.index,
                    "text": para.text,
                    "plain_text": stripped,
                    "line_start": para.line_start,
                    "line_end": para.line_end,
                    "file_path": file_path,
                }
            )
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
        self._store_context(operation_id, self._marker_maps, file_path, file_md5s)

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
                    stripped, marker_map = self._parser.strip_to_plain_text(para.text)
                    if f not in self._marker_maps:
                        self._marker_maps[f] = {}
                    self._marker_maps[f][para.index] = marker_map
                else:
                    stripped = self._parser.strip_to_plain_text(para.text)[0]
                all_data.append(
                    {
                        "file_path": f,
                        "para_index": para.index,
                        "text": para.text,
                        "plain_text": stripped,
                    }
                )
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
                d
                for d in all_para_data
                if d["file_path"] == file_path and d["para_index"] in index_set
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
            "original": original_text,
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

        子类可覆写此方法以自定义单段检查逻辑（如 code_review 的双数据线架构）。
        """
        # 若段落仅含 marker（纯注释/命令块），跳过 LLM 请求
        if self.use_marker_protection:
            plain_without_markers = MARKER_PATTERN.sub("", text).strip()
            if not plain_without_markers:
                return self._make_fallback(idx, text, original_text, file_path)

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

            # 轻量内容校验：只查控制字符（排除 \t / \n，\r 仍拦截）与
            # 垃圾占位符，不查花括号 / $ 配平——marker 化段落可能残留
            # 合法结构（如 \~{n}），配平检查会误报。失败视为内容损坏，
            # 回退到原文，避免损坏内容进入 marker 校验与写回链路。
            content_error = check_latex_text_lite(output_text)
            if content_error:
                self._logger.warning(
                    f"Paragraph {idx} in {file_path}: output content "
                    f"corrupted — {content_error}. Falling back to original text."
                )
                return self._make_fallback(idx, text, original_text, file_path)

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
                        idx,
                        text,
                        original_text,
                        file_path,
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
                        idx,
                        text,
                        original_text,
                        file_path,
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
            # LLM 每用户治理超限（429 语义）不属于"段落检查失败"，原样重抛：
            # 流式路径由调用方转为 error 事件（前端按码翻译），批量路径直达
            # 路由层映射为 429 + Retry-After；静默降级会让用户误以为无建议
            if llm_governance.governance_code_of(e) is not None:
                raise
            self._logger.error(f"Paragraph {idx} in {file_path}: LLM call failed — {e}")
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
        files = self._get_files(file_path, resolve_input, workspace_id)

        self._file_path = file_path
        if self.use_marker_protection:
            self._marker_maps = {}

        all_para_data = self._collect_para_data(files, workspace_id)
        targets = self._filter_targets(all_para_data, file_path, paragraph_indices)

        operation_id = self._generate_operation_id()
        if self.use_marker_protection:
            file_md5s = self._capture_file_md5s(files, workspace_id)
            self._store_context(operation_id, self._marker_maps, file_path, file_md5s)

        if not has_api_key_for(self.llm_role):
            self._logger.warning("No API key configured, returning plain text only")
            return self._no_api_key_batch_fallback(targets, file_path, operation_id)

        # 每次调用时按子类槽位实时取客户端（热重载语义，禁止缓存实例）
        llm = get_client_for(self.llm_role)
        batch_size = self._get_batch_size()
        # 批次编排信号量与档案客户端内部信号量同口径：按合并配置
        # （含档案 max_concurrency 覆盖项）取容量，而非仅顶层字段
        sem = asyncio.Semaphore(llm.resolved_max_concurrency())
        results: list[dict[str, Any] | None] = [None] * len(targets)

        async def _limited(coro):
            async with sem:
                return await coro

        for batch_start in range(0, len(targets), batch_size):
            batch = targets[batch_start : batch_start + batch_size]
            tasks = []

            for d in batch:
                check_text = (
                    d["plain_text"] if self.use_marker_protection else d["text"]
                )
                if not check_text.strip():
                    tasks.append(self._make_empty_from_data(d))
                else:
                    tasks.append(
                        self._check_one_paragraph(
                            llm,
                            len(tasks),
                            d["para_index"],
                            check_text,
                            d["text"],
                            d["file_path"],
                        )
                    )

            batch_results = await asyncio.gather(*(_limited(t) for t in tasks))
            for slot, d in enumerate(batch):
                results[batch_start + slot] = batch_results[slot]

            if batch_start + batch_size < len(targets):
                await asyncio.sleep(cfg.llm.request_interval)

        self._logger.info(
            f"Batch check completed for {file_path} " f"[op={operation_id}]"
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
            self._store_context(operation_id, self._marker_maps, file_path, file_md5s)

        total = len(all_para_data)
        yield {"type": "progress", "current": 0, "total": total}

        if not has_api_key_for(self.llm_role):
            yield {
                "type": "error",
                "message": "No API key configured",
                "retrying": False,
            }
            yield {"type": "done", "operation_id": operation_id}
            return

        # 每次调用时按子类槽位实时取客户端（热重载语义，禁止缓存实例）
        llm = get_client_for(self.llm_role)

        for i, d in enumerate(all_para_data):
            f = d["file_path"]
            idx = d["para_index"]
            check_text = d["plain_text"] if self.use_marker_protection else d["text"]

            if not check_text.strip():
                empty = self._make_fallback(
                    idx,
                    check_text,
                    d["text"],
                    f,
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
                self._logger.error(f"Error checking paragraph {idx} in {f}: {e}")
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
            f"Stream check completed for {file_path} " f"[op={operation_id}]"
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
            results.append(
                self._make_fallback(
                    d["para_index"],
                    d["plain_text"] if self.use_marker_protection else d["text"],
                    d["text"],
                    d["file_path"],
                )
            )
        return {
            "results": results,
            "file_path": file_path,
            "operation_id": operation_id,
        }

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
            { applied: N, total: M, errors: [...] }
        """
        pm = get_project_manager_for_workspace(workspace_id)
        applied = 0
        errors: list[dict[str, Any]] = []

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
            expected_md5 = change.get("expected_md5")

            if self.use_marker_protection:
                if context is not None:
                    file_maps = context.marker_maps.get(target_file, {})
                else:
                    file_maps = self._marker_maps.get(target_file, {})
                marker_map = file_maps.get(idx)
                if marker_map is None:
                    self._logger.error(
                        f"Marker map lost for paragraph {idx} in "
                        f"{target_file} [op={operation_id or 'legacy'}], "
                        f"skipping to prevent literal marker corruption"
                    )
                    errors.append(
                        {
                            "paragraph_index": idx,
                            "file_path": target_file,
                            "reason": "Marker map missing",
                        }
                    )
                    continue
                restored = self._parser.restore_from_markers(new_text, marker_map)
            else:
                restored = new_text

            try:
                pm.apply_change(target_file, idx, restored, expected_md5=expected_md5)
                applied += 1
                self._logger.info(
                    f"Applied change to paragraph {idx} in {target_file}"
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

        if applied:
            # 多用户模式：写盘后刷新项目用量记账（单用户无绑定，静默跳过）。
            # 本方法为同步执行，使用同步版刷新避免再起线程
            access_control.refresh_workspace_project_usage_sync(workspace_id)

        return {"applied": applied, "total": len(changes), "errors": errors}

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

    def _restore_change_markers(
        self,
        changes: list[dict[str, Any]],
        file_path: str,
        para_index: int,
        fields: tuple[str, ...] = ("original", "corrected", "optimized", "translated"),
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        r"""
        将 changes 中指定字段的 marker（如 __MATH_0001__）还原为原始 LaTeX。

        ── 设计背景 ───────────────────────────────────────────────
        逐段检查结果中的文本走两条数据通道，服务于不同消费者：

        │ 字段          │ 内容             │ 消费者               │
        │──────────────│─────────────────│──────────────────────│
        │ corrected     │ 含 marker 的文本 │ handleApplyChange    │
        │ changes[].original │ 还原后的 LaTeX │ 前端结果卡片（只读展示） │
        │ changes[].corrected│ 还原后的 LaTeX │ 前端结果卡片（只读展示） │

        corrected 字段必须保留 marker，因为 apply_changes() 依赖
        marker_map 将 marker 还原后再写入 .tex 文件。若预先还原，
        apply 链路会因找不到 marker 而误判或跳过写入。
        而 changes[] 仅用于前端展示，还原后用户看到干净的 LaTeX，
        便于"原文 vs 修正后"对比阅读。

        ── 返回 tuple 的原因 ─────────────────────────────────────
        某些服务的 _build_success_result 不仅需要还原 changes，
        还需还原顶层字段（如 paragraph_translate 的 original/translated
        因为该页面展示整段文本而非 change 片段）。返回 marker_map
        让调用方无需再次 self._marker_maps.get().get() 重复查询，
        避免代码重复和潜在不一致。

        无 marker_map 时（如 context 过期），原样返回 changes 和空 dict，
        调用方应据此跳过顶层还原，保持降级行为一致。
        """
        marker_map = self._marker_maps.get(file_path, {}).get(para_index, {})
        if not marker_map:
            return changes, marker_map
        restored = []
        for change in changes:
            d = dict(change)
            for field in fields:
                if field in d and isinstance(d[field], str):
                    d[field] = self._parser.restore_from_markers(d[field], marker_map)
            restored.append(d)
        return restored, marker_map

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
