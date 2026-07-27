"""
文档格式转换业务逻辑（Markdown/DOCX/HTML ↔ LaTeX）。

流程：
    1. 检查 Pandoc 是否可用
    2. 根据输入类型（文本/文件）调用 Pandoc 进行格式转换
    3. 目标格式为 LaTeX 时，调用宏包分析器分析所需宏包
    4. 将宏包注释前置到 LaTeX 代码中，返回合并结果
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from backend.core.logger import get_logger
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.utils.latex_package_analyzer import prepend_package_comments

SUPPORTED_FORMATS = {"markdown", "html", "docx", "latex"}

FORMAT_TO_PANDOC = {
    "markdown": "markdown",
    "html": "html",
    "docx": "docx",
    "latex": "latex",
}


class FormatConverterService:
    """文档格式转换服务（Markdown/DOCX/HTML ↔ LaTeX）。"""

    def __init__(self):
        self._logger = get_logger("format_converter")

    async def convert_text(
        self,
        text: str,
        source_format: str = "markdown",
        target_format: str = "latex",
    ) -> dict[str, Any]:
        """
        文本输入格式转换。

        参数：
            text: 输入文本
            source_format: 源格式（markdown/html/latex）
            target_format: 目标格式（markdown/html/latex/docx）

        返回：
            包含 result 字段的 dict
        """
        if not text.strip():
            raise ValueError("Input text is empty")

        self._validate_formats(source_format, target_format)
        self._check_pandoc()

        result = await asyncio.to_thread(
            self._run_pandoc_text, text, source_format, target_format
        )

        if target_format == "latex":
            result = await self._analyze_packages(result)

        is_binary = target_format in self.BINARY_FORMATS
        return {"result": result, "is_binary": is_binary}

    async def convert_file(
        self,
        file_bytes: bytes,
        filename: str,
        source_format: str,
        target_format: str,
    ) -> dict[str, Any]:
        """
        文件上传格式转换。

        参数：
            file_bytes: 文件二进制内容
            filename: 原始文件名（用于推断输出文件名）
            source_format: 源格式
            target_format: 目标格式

        返回：
            包含 result 和 output_filename 字段的 dict
        """
        if not file_bytes:
            raise ValueError("Uploaded file is empty")

        self._validate_formats(source_format, target_format)
        self._validate_file(filename, source_format)
        self._check_pandoc()

        result = await asyncio.to_thread(
            self._run_pandoc_file, file_bytes, source_format, target_format
        )

        if target_format == "latex":
            result = await self._analyze_packages(result)

        output_filename = self._derive_output_filename(filename, target_format)
        is_binary = target_format in self.BINARY_FORMATS

        return {
            "result": result,
            "output_filename": output_filename,
            "is_binary": is_binary,
        }

    async def convert(self, markdown_text: str) -> dict[str, Any]:
        """
        向后兼容接口：将 Markdown 文本转换为 LaTeX。

        参数：
            markdown_text: 输入的 Markdown 文本

        返回：
            包含 latex 字段的 dict
        """
        if not markdown_text.strip():
            raise ValueError("Markdown text is empty")

        result = await self.convert_text(markdown_text, "markdown", "latex")
        return {"latex": result["result"]}

    async def _analyze_packages(self, latex: str) -> str:
        """分析 LaTeX 代码所需的宏包，失败时静默降级。"""
        cfg = get_config()
        if cfg.llm.api_key:
            try:
                llm = get_llm_client()
                latex = await prepend_package_comments(llm, latex)
            except Exception as e:
                self._logger.warning(
                    f"Package analysis failed, returning raw output: {e}"
                )
        return latex

    @staticmethod
    def _validate_formats(source: str, target: str) -> None:
        """校验格式参数有效性。"""
        if source not in SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported source format: '{source}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_FORMATS))}"
            )
        if target not in SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported target format: '{target}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_FORMATS))}"
            )
        if source == target:
            raise ValueError("Source and target formats must be different")

    @staticmethod
    def _validate_file(filename: str, source_format: str) -> None:
        """校验文件扩展名是否匹配声明的格式。"""
        ext = Path(filename).suffix.lower()
        cfg = get_config()
        allowed = cfg.features.format_converter.allowed_extensions
        if ext not in allowed:
            raise ValueError(
                f"Unsupported file type: '{ext}'. "
                f"Allowed: {', '.join(sorted(allowed))}"
            )

        ext_to_format = {
            ".md": "markdown",
            ".markdown": "markdown",
            ".html": "html",
            ".htm": "html",
            ".docx": "docx",
        }
        expected = ext_to_format.get(ext)
        if expected and expected != source_format:
            raise ValueError(
                f"File extension '{ext}' does not match declared format '{source_format}'"
            )

    @staticmethod
    def _check_pandoc() -> None:
        """检查 Pandoc 是否已安装。"""
        import shutil

        if shutil.which("pandoc") is None:
            raise RuntimeError(
                "Pandoc is not installed. "
                "Install it from https://pandoc.org/installing.html"
            )

    BINARY_FORMATS = {"docx"}

    def _run_pandoc_text(
        self, text: str, source_format: str, target_format: str
    ) -> str | bytes:
        """通过 stdin 调用 Pandoc 进行文本转换。二进制格式返回原始字节。"""
        import base64
        import subprocess

        pandoc_from = FORMAT_TO_PANDOC[source_format]
        pandoc_to = FORMAT_TO_PANDOC[target_format]
        extra_args = ["--wrap=preserve"] if target_format == "latex" else []

        if target_format in self.BINARY_FORMATS:
            with tempfile.NamedTemporaryFile(
                suffix=f".{target_format}", delete=False
            ) as tmp:
                out_path = tmp.name
            try:
                proc = subprocess.run(
                    [
                        "pandoc",
                        "-f",
                        pandoc_from,
                        "-t",
                        pandoc_to,
                        *extra_args,
                        "-o",
                        out_path,
                    ],
                    input=text,
                    text=True,
                    encoding="utf-8",
                    timeout=30,
                )
                if proc.returncode != 0:
                    self._logger.error(f"Pandoc error: {proc.stderr}")
                    raise RuntimeError(
                        f"Pandoc conversion failed: {proc.stderr.strip()}"
                    )
                raw = Path(out_path).read_bytes()
                if not raw:
                    raise RuntimeError("Pandoc produced empty output")
                return base64.b64encode(raw).decode("ascii")
            finally:
                Path(out_path).unlink(missing_ok=True)

        proc = subprocess.run(
            ["pandoc", "-f", pandoc_from, "-t", pandoc_to, *extra_args],
            input=text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )

        if proc.returncode != 0:
            self._logger.error(f"Pandoc error: {proc.stderr}")
            raise RuntimeError(f"Pandoc conversion failed: {proc.stderr.strip()}")

        if proc.stderr:
            self._logger.warning(f"Pandoc stderr: {proc.stderr}")

        result = proc.stdout
        if not result.strip():
            raise RuntimeError("Pandoc produced empty output")

        return result

    def _run_pandoc_file(
        self, file_bytes: bytes, source_format: str, target_format: str
    ) -> str | bytes:
        """通过临时文件调用 Pandoc 进行文件转换。二进制格式返回 base64 字符串。"""
        import base64
        import subprocess

        pandoc_from = FORMAT_TO_PANDOC[source_format]
        pandoc_to = FORMAT_TO_PANDOC[target_format]
        ext = f".{source_format}" if source_format != "markdown" else ".md"
        extra_args = ["--wrap=preserve"] if target_format == "latex" else []

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        out_path = None
        if target_format in self.BINARY_FORMATS:
            out_tmp = tempfile.NamedTemporaryFile(
                suffix=f".{target_format}", delete=False
            )
            out_path = out_tmp.name
            out_tmp.close()

        try:
            cmd = ["pandoc", "-f", pandoc_from, "-t", pandoc_to, *extra_args]
            if out_path:
                cmd += ["-o", out_path]
            cmd.append(tmp_path)

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            if proc.returncode != 0:
                self._logger.error(f"Pandoc error: {proc.stderr}")
                raise RuntimeError(f"Pandoc conversion failed: {proc.stderr.strip()}")

            if out_path:
                raw = Path(out_path).read_bytes()
                if not raw:
                    raise RuntimeError("Pandoc produced empty output")
                return base64.b64encode(raw).decode("ascii")

            if proc.stderr:
                self._logger.warning(f"Pandoc stderr: {proc.stderr}")

            result = proc.stdout
            if not result.strip():
                raise RuntimeError("Pandoc produced empty output")
            return result
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            if out_path:
                Path(out_path).unlink(missing_ok=True)

    @staticmethod
    def _derive_output_filename(original: str, target_format: str) -> str:
        """根据目标格式推导输出文件名。"""
        stem = Path(original).stem
        ext_map = {
            "markdown": ".md",
            "html": ".html",
            "docx": ".docx",
            "latex": ".tex",
        }
        return f"{stem}{ext_map[target_format]}"
