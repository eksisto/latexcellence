"""
模板生成业务编排。

本模块遵循分层约束：router.py（薄路由） → service.py（业务逻辑/LLM 编排） →
agent/（Function Calling Agent 主循环与工具执行）。

- generate()：创建沙箱工作区后驱动自主 Agent（SSE 事件流），并向上转发事件
- list_session_files / read_session_file / zip_session：Agent 产物浏览与打包下载
"""

import asyncio
import io
import uuid
import zipfile
from pathlib import Path
from typing import Any, AsyncIterator

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.multiuser.quota import (
    checkpoint_derived_usage_for_current_user,
    enforce_derived_quota_for_current_user,
)
from backend.modules.template_gen.agent import (
    get_template_agent_service,
    load_sandbox,
    safe_read_sandbox_file,
)
from backend.modules.template_gen.trace import read_trace
from backend.utils.session_mutex import cross_process_session_lock, lock_file_for

logger = get_logger(__name__)


class TemplateGenService:
    """模板生成业务编排（单例）。"""

    # ── 自主 Agent 生成 ──────────────────────────────────────────────

    async def generate(
        self,
        description: str,
        language: str = "zh",
        *,
        llm: Any | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        创建沙箱并驱动自主 Agent 生成完整 LaTeX 工程，SSE 事件流。

        参数：
            description: 用户对模板的自然语言描述
            language: 输出语言（注入 system prompt）
            llm: 可选 LLM 客户端（测试注入用）
        """
        cfg = get_config()

        # 多用户模式：新建模板会话前检查衍生数据配额（超限抛 413，方案 §9.2）。
        # v2 P4：检查读 user_derived_usage 缓存，先对本分类做检查点增量刷新
        # （仅扫 templates 单个目录）保证准确性，入线程池避免阻塞事件循环
        await asyncio.to_thread(enforce_derived_quota_for_current_user, 0, "templates")

        # 创建沙箱工作区（多用户模式按当前用户隔离到 <users_root>/<user_id>/templates/）
        output_dir = self._output_root(cfg)
        session_id = uuid.uuid4().hex[:12]
        sandbox = output_dir / session_id
        sandbox.mkdir(parents=True, exist_ok=True)
        # v2 P4 检查点：新沙箱落盘后刷新该用户 templates 缓存行（生成期间的
        # 增长由下一次配额前置刷新与每小时对账覆盖）
        await asyncio.to_thread(checkpoint_derived_usage_for_current_user, "templates")
        logger.info(f"Agent sandbox has been created: {sandbox}")

        agent = get_template_agent_service()
        # 会话互斥（v2 P7）：跨进程 flock 锁文件（沙箱目录旁）+ 进程内锁，
        # 多副本部署下防止两个副本同时写同一沙箱
        async with cross_process_session_lock(lock_file_for(sandbox)):
            async for event in agent.generate(
                description,
                language,
                sandbox=sandbox,
                llm=llm,
            ):
                yield event

    # ── 产物浏览 ─────────────────────────────────────────────────────

    def list_session_files(self, session_id: str) -> dict[str, Any]:
        """列出某次 Agent 生成产出的文件树。"""
        cfg = get_config()
        sandbox = self._load_sandbox(cfg, session_id)

        files: list[dict[str, Any]] = []
        root = sandbox.resolve()
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            files.append({"path": rel, "size": p.stat().st_size})
        # sandbox 绝对路径不向用户暴露，仅返回 session 内相对文件列表
        return {"session_id": session_id, "files": files}

    def read_session_file(self, session_id: str, rel_path: str) -> dict[str, Any]:
        """读取沙箱内单个文件内容。"""
        cfg = get_config()
        sandbox = self._load_sandbox(cfg, session_id)

        content, err = safe_read_sandbox_file(sandbox, rel_path)
        if err is not None:
            raise ValueError(err)
        return {"session_id": session_id, "path": rel_path, "content": content}

    def zip_session(self, session_id: str) -> tuple[bytes, str]:
        """将沙箱内所有文件打包为 ZIP，返回 (bytes, 下载文件名)。"""
        cfg = get_config()
        sandbox = self._load_sandbox(cfg, session_id)

        root = sandbox.resolve()
        files = [p for p in sorted(root.rglob("*")) if p.is_file()]
        if not files:
            raise ValueError("No files in sandbox")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in files:
                arcname = p.relative_to(root).as_posix()
                zf.write(p, arcname)
                logger.debug("ZIP included: %s", arcname)
        return buf.getvalue(), f"latex-template-{session_id[:8]}.zip"

    def get_session_trace(self, session_id: str) -> dict[str, Any]:
        """读取 Agent 运行追踪（完整交互日志），用于分析与改进。"""
        cfg = get_config()
        sandbox = self._load_sandbox(cfg, session_id)
        events = read_trace(sandbox)
        meta_info: dict[str, Any] = {}
        # 从 trace 首条 run_start 事件中提取基础信息
        for ev in events:
            if ev.get("type") == "run_start":
                data = ev.get("data") or {}
                meta_info = {
                    "description": data.get("description", ""),
                    "language": data.get("language", ""),
                    "max_rounds": data.get("max_rounds"),
                }
                break
        return {
            "session_id": session_id,
            "meta": meta_info,
            "events": events,
        }

    # ── 内部辅助 ─────────────────────────────────────────────────────

    def _output_root(self, cfg: Any) -> Path:
        """返回模板生成输出根目录。

        多用户模式：按当前用户隔离到 <users_root>/<user_id>/templates/；
        单用户模式：返回存量全局路径（cfg.features.template_gen.output_dir），行为不变。
        """
        from backend.core.multiuser import userspace

        legacy_root = Path(cfg.features.template_gen.output_dir)
        if not legacy_root.is_absolute():
            legacy_root = Path.cwd() / legacy_root
        return userspace.user_resource_root("templates", legacy_root)

    def _load_sandbox(self, cfg: Any, session_id: str) -> Path:
        """解析沙箱路径并校验存在性（不存在时抛出 ValueError）。"""
        sandbox = load_sandbox(str(self._output_root(cfg)), session_id)
        if not sandbox.exists():
            raise ValueError("Session not found")
        return sandbox


_service: "TemplateGenService | None" = None


def get_template_gen_service() -> TemplateGenService:
    global _service
    if _service is None:
        _service = TemplateGenService()
    return _service
