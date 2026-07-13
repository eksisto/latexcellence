"""
Markdown 转 LaTeX 业务逻辑。

流程：
    1. 检查 Pandoc 是否可用
    2. 调用 Pandoc 将 Markdown 文本转换为 LaTeX
    3. 返回转换后的 LaTeX 代码
"""

import asyncio
from typing import Any

from backend.core.logger import get_logger
from backend.core.config import get_config


class MarkdownConverterService:
    """Markdown 转 LaTeX 服务。"""

    def __init__(self):
        self._logger = get_logger("markdown_converter")

    async def convert(self, markdown_text: str) -> dict[str, Any]:
        """
        将 Markdown 文本转换为 LaTeX。

        参数：
            markdown_text: 输入的 Markdown 文本

        返回：
            包含 latex 字段的 dict
        """
        if not markdown_text.strip():
            raise ValueError("Markdown text is empty")

        self._check_pandoc()

        latex = await asyncio.to_thread(self._run_pandoc, markdown_text)
        return {"latex": latex}

    @staticmethod
    def _check_pandoc() -> None:
        """检查 Pandoc 是否已安装。"""
        import shutil
        if shutil.which("pandoc") is None:
            raise RuntimeError(
                "Pandoc is not installed. "
                "Install it from https://pandoc.org/installing.html"
            )

    def _run_pandoc(self, markdown_text: str) -> str:
        """
        调用 Pandoc 进行转换。

        参数：
            markdown_text: 输入的 Markdown 文本

        返回：
            转换后的 LaTeX 代码
        """
        import subprocess

        proc = subprocess.run(
            [
                "pandoc",
                "-f", "markdown",
                "-t", "latex",
                "--wrap=preserve",
            ],
            input=markdown_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )

        if proc.returncode != 0:
            self._logger.error(f"Pandoc error: {proc.stderr}")
            raise RuntimeError(
                f"Pandoc conversion failed: {proc.stderr.strip()}")

        if proc.stderr:
            self._logger.warning(f"Pandoc stderr: {proc.stderr}")

        result = proc.stdout
        if not result.strip():
            raise RuntimeError("Pandoc produced empty output")

        return result
