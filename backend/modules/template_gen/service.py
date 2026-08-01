"""
模板生成业务编排。

本模块遵循分层约束：router.py（薄路由） → service.py（业务逻辑/LLM 编排） →
agent.py（Function Calling Agent 主循环与工具执行）。

- generate()：创建沙箱工作区后驱动自主 Agent（SSE 事件流），并向上转发事件
- list_session_files / read_session_file / zip_session：Agent 产物浏览与打包下载
"""

import io
import uuid
import zipfile
from pathlib import Path
from typing import Any, AsyncIterator

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.modules.template_gen.agent import (
    get_template_agent_service,
    load_sandbox,
    safe_read_sandbox_file,
)

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

        # 创建沙箱工作区
        output_dir = Path(cfg.features.template_gen.output_dir)
        if not output_dir.is_absolute():
            output_dir = Path.cwd() / output_dir
        session_id = uuid.uuid4().hex[:12]
        sandbox = output_dir / "agent" / session_id
        sandbox.mkdir(parents=True, exist_ok=True)
        logger.info(f"Agent sandbox has been created: {sandbox}")

        agent = get_template_agent_service()
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
        return {"session_id": session_id, "sandbox": str(sandbox), "files": files}

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

    # ── 内部辅助 ─────────────────────────────────────────────────────

    def _load_sandbox(self, cfg: Any, session_id: str) -> Path:
        """解析沙箱路径并校验存在性（不存在时抛出 ValueError）。"""
        sandbox = load_sandbox(cfg.features.template_gen.output_dir, session_id)
        if not sandbox.exists():
            raise ValueError("Session not found")
        return sandbox


_service: "TemplateGenService | None" = None


def get_template_gen_service() -> TemplateGenService:
    global _service
    if _service is None:
        _service = TemplateGenService()
    return _service
