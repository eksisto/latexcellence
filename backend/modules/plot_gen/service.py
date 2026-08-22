"""科研绘图 Agent 业务编排。"""

import asyncio
import io
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.multiuser.quota import (
    checkpoint_derived_usage_for_current_user,
    enforce_derived_quota_for_current_user,
)
from backend.modules.plot_gen.agent import (
    get_plot_gen_agent,
    get_plot_output_dir,
    load_plot_sandbox,
    read_session_meta,
    safe_read_plot_file,
    write_session_meta,
)
from backend.modules.plot_gen.data_parser import (
    ALLOWED_DATA_SUFFIXES,
    normalize_filename,
    parse_data_file,
)
from backend.modules.plot_gen.executor import is_internal_rel_path
from backend.modules.plot_gen.models import PlotSessionMeta
from backend.modules.plot_gen.recommender import recommend_chart_plan
from backend.modules.plot_gen.styles import list_styles
from backend.modules.plot_gen.trace import read_trace

logger = get_logger(__name__)


class PlotGenService:
    """上传、生成、微调和产物管理。"""

    async def create_session_from_upload(
        self,
        file_bytes: bytes,
        filename: str,
        description: str = "",
        style: str = "nature",
        language: str = "zh",
    ) -> dict[str, Any]:
        if not file_bytes:
            raise ValueError("Empty file")
        if not filename:
            raise ValueError("No file selected")

        safe_name = normalize_filename(filename)
        suffix = Path(safe_name).suffix.lower()
        if suffix not in ALLOWED_DATA_SUFFIXES:
            allowed = "/".join(ALLOWED_DATA_SUFFIXES)
            raise ValueError(f"Unsupported file type: '{suffix}'. Supported: {allowed}")

        cfg = get_config()
        fcfg = cfg.features.plot_gen
        if len(file_bytes) > fcfg.max_data_size_mb * 1024 * 1024:
            raise ValueError(
                f"File too large: {len(file_bytes) / 1024 / 1024:.1f}MB. "
                f"Maximum: {fcfg.max_data_size_mb}MB"
            )

        # 多用户模式：新建制图会话前检查衍生数据配额（按实际数据文件大小计，
        # 超限抛 413，方案 §9.2“输出写入时检查”）。
        # v2 P4：检查读 user_derived_usage 缓存，先对本分类做检查点增量刷新
        # （仅扫 plots 单个目录），入线程池避免阻塞事件循环
        await asyncio.to_thread(
            enforce_derived_quota_for_current_user, len(file_bytes), "plots"
        )

        session_id = uuid.uuid4().hex[:12]
        sandbox = get_plot_output_dir(cfg) / session_id
        (sandbox / "data").mkdir(parents=True, exist_ok=True)
        (sandbox / "scripts").mkdir(parents=True, exist_ok=True)
        (sandbox / "artifacts").mkdir(parents=True, exist_ok=True)

        data_path = sandbox / "data" / safe_name
        data_path.write_bytes(file_bytes)
        profile = await asyncio.to_thread(
            parse_data_file, data_path, fcfg.max_data_rows
        )

        now = datetime.now().isoformat(timespec="seconds")
        meta = PlotSessionMeta(
            session_id=session_id,
            data_file=safe_name,
            description=description,
            style=style,
            language=language,
            data_profile=profile,
            created_at=now,
            updated_at=now,
        )
        write_session_meta(sandbox, meta)
        # v2 P4 检查点：新会话落盘后刷新该用户 plots 缓存行（后续生成产物的
        # 增长由下一次配额前置刷新与每小时对账覆盖）
        await asyncio.to_thread(checkpoint_derived_usage_for_current_user, "plots")
        logger.info("plot_gen session created: %s (%s)", session_id, safe_name)
        return {
            "session_id": session_id,
            "data_file": safe_name,
            "data_profile": profile.model_dump(),
        }

    async def generate(
        self,
        session_id: str,
        description: str,
        style: str,
        language: str,
        llm: Any | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        cfg = get_config()
        sandbox = load_plot_sandbox(get_plot_output_dir(cfg), session_id)
        meta = read_session_meta(sandbox)
        meta.description = description or meta.description
        meta.style = style or meta.style
        meta.language = language or meta.language

        profile = meta.data_profile
        if profile is None:
            yield {"type": "error", "message": "Data profile is missing"}
            return

        plan = await recommend_chart_plan(
            profile,
            meta.description,
            meta.style,
            meta.language,
            llm,
        )
        meta.chart_plan = plan
        meta.updated_at = datetime.now().isoformat(timespec="seconds")
        write_session_meta(sandbox, meta)

        yield {"type": "data_profile", "data_profile": profile.model_dump()}
        yield {"type": "chart_plan", "chart_plan": plan.model_dump()}

        agent = get_plot_gen_agent()
        async for event in agent.generate(
            session_id,
            meta.description,
            meta.language,
            llm=llm,
            style=meta.style,
            chart_plan=plan,
        ):
            yield event

    async def refine(
        self,
        session_id: str,
        message: str,
        language: str,
        llm: Any | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        agent = get_plot_gen_agent()
        async for event in agent.refine(session_id, message, language, llm=llm):
            yield event

    def list_session_files(self, session_id: str) -> list[dict[str, Any]]:
        cfg = get_config()
        sandbox = load_plot_sandbox(get_plot_output_dir(cfg), session_id)
        result: list[dict[str, Any]] = []
        root = sandbox.resolve()
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = p.relative_to(root).as_posix()
                if is_internal_rel_path(rel):
                    continue
                result.append(
                    {
                        "path": rel,
                        "size": p.stat().st_size,
                    }
                )
        return result

    def read_session_file(
        self, session_id: str, rel_path: str
    ) -> tuple[str | None, str | None]:
        cfg = get_config()
        sandbox = load_plot_sandbox(get_plot_output_dir(cfg), session_id)
        return safe_read_plot_file(sandbox, rel_path)

    def get_session_trace(self, session_id: str) -> dict[str, Any]:
        """读取 Agent 运行追踪（完整交互日志），用于分析与改进。"""
        cfg = get_config()
        sandbox = load_plot_sandbox(get_plot_output_dir(cfg), session_id)
        events = read_trace(sandbox)
        meta_info: dict[str, Any] = {}
        try:
            meta = read_session_meta(sandbox)
            meta_info = {
                "data_file": meta.data_file,
                "description": meta.description,
                "style": meta.style,
                "language": meta.language,
                "created_at": meta.created_at,
                "updated_at": meta.updated_at,
                "last_execution_success": meta.last_execution_success,
                "last_tikz_success": meta.last_tikz_success,
            }
        except Exception:
            pass
        return {
            "session_id": session_id,
            "meta": meta_info,
            "events": events,
        }

    def zip_session(self, session_id: str) -> tuple[bytes, list[Path]]:
        cfg = get_config()
        sandbox = load_plot_sandbox(get_plot_output_dir(cfg), session_id)
        root = sandbox.resolve()
        files = [
            p
            for p in sorted(root.rglob("*"))
            if p.is_file() and not is_internal_rel_path(p.relative_to(root).as_posix())
        ]

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in files:
                zf.write(p, p.relative_to(root).as_posix())
        return buf.getvalue(), files

    @staticmethod
    def get_styles() -> list[dict[str, str]]:
        return list_styles()


_service: PlotGenService | None = None


def get_plot_gen_service() -> PlotGenService:
    global _service
    if _service is None:
        _service = PlotGenService()
    return _service
