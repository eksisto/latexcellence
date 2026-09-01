"""
文档格式转换业务逻辑（Markdown/DOCX/HTML ↔ LaTeX）。

流程：
    1. 检查 Pandoc 是否可用（sandbox docker 后端下由镜像提供，跳过宿主检测）
    2. 根据输入类型（文本/文件）调用 Pandoc 进行格式转换（经 SandboxRunner：
       sandbox 模式进 convert 镜像容器，local 回落本地子进程）
    3. 目标格式为 LaTeX 时，调用宏包分析器分析所需宏包
    4. 将宏包注释前置到 LaTeX 代码中，返回合并结果
"""

import base64
import tempfile
from pathlib import Path
from typing import Any

from backend.core.logger import get_logger

# default 槽位（multi-model-plan §6）：宏包分析兜底等低频辅助调用；
# get_llm_client 即 get_client_for("default") 的薄别名（P2 起语义等价）
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.utils.latex_package_analyzer import prepend_package_comments
from backend.utils.sandbox import (
    ExecResult,
    ExecSpec,
    cached_backend_for,
    current_sandbox_user_id,
    get_sandbox_runner,
)

SUPPORTED_FORMATS = {"markdown", "html", "docx", "latex"}

# Pandoc reader（-f）/ writer（-t）格式映射分离：
# reader 侧为 markdown 启用 tex_math_single_backslash 扩展，将 \(...\) /
# \[...\]（LLM 生成 markdown 的常见公式定界符）识别为行内/行间公式，
# 避免被当作普通括号输出为字面文本；writer 侧保持 pandoc 默认输出
# （不加公式相关扩展，latex 输出维持 \(...\) / \[...\] 形式）。
PANDOC_READER_FORMATS = {
    "markdown": "markdown+tex_math_single_backslash",
    "html": "html",
    "docx": "docx",
    "latex": "latex",
}

PANDOC_WRITER_FORMATS = {
    "markdown": "markdown",
    "html": "html",
    "docx": "docx",
    "latex": "latex",
}

# 单次 pandoc 转换超时（与历史值一致：文本 30s / 文件 60s）
_TEXT_TIMEOUT_SECONDS = 30.0
_FILE_TIMEOUT_SECONDS = 60.0


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

        result = await self._run_pandoc_text(text, source_format, target_format)

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

        result = await self._run_pandoc_file(file_bytes, source_format, target_format)

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
                latex = await prepend_package_comments(llm, latex, domain="general")
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
        """检查 Pandoc 是否可用。

        sandbox docker 后端下由 convert 镜像内置，宿主无需安装；
        仅在本地执行路径要求宿主安装 pandoc。
        """
        if cached_backend_for("convert") == "docker":
            return
        import shutil

        if shutil.which("pandoc") is None:
            raise RuntimeError(
                "Pandoc is not installed. "
                "Install it from https://pandoc.org/installing.html"
            )

    BINARY_FORMATS = {"docx"}

    def _raise_on_pandoc_failure(self, result: ExecResult) -> None:
        """非零退出时抛出与历史文案一致的 RuntimeError。"""
        if result.returncode != 0:
            self._logger.error(f"Pandoc error: {result.stderr}")
            raise RuntimeError(f"Pandoc conversion failed: {result.stderr.strip()}")

    async def _run_pandoc_text(
        self, text: str, source_format: str, target_format: str
    ) -> str | bytes:
        """文本输入转换（30s 超时）：文本按 UTF-8 落临时输入文件。"""
        return await self._run_pandoc(
            text.encode("utf-8"),
            source_format,
            target_format,
            timeout_seconds=_TEXT_TIMEOUT_SECONDS,
        )

    async def _run_pandoc_file(
        self, file_bytes: bytes, source_format: str, target_format: str
    ) -> str | bytes:
        """文件输入转换（60s 超时）：二进制内容落临时输入文件。"""
        return await self._run_pandoc(
            file_bytes,
            source_format,
            target_format,
            timeout_seconds=_FILE_TIMEOUT_SECONDS,
        )

    async def _run_pandoc(
        self,
        input_bytes: bytes,
        source_format: str,
        target_format: str,
        *,
        timeout_seconds: float,
    ) -> str | bytes:
        """经 SandboxRunner 调用 Pandoc 的共享执行路径。

        输入统一落临时文件后以路径传入（不使用 stdin：docker 后端不注入
        标准输入，文件化让 local/docker 两条后端行为一致）；二进制格式输出
        写入同一临时目录后读回 base64。目录即工作目录（容器内同绝对路径
        rw 挂载）。
        """
        pandoc_from = PANDOC_READER_FORMATS[source_format]
        pandoc_to = PANDOC_WRITER_FORMATS[target_format]
        extra_args = ["--wrap=preserve"] if target_format == "latex" else []

        with tempfile.TemporaryDirectory(prefix="latexcellence_pandoc_") as tmp_dir:
            ext = f".{source_format}" if source_format != "markdown" else ".md"
            in_path = Path(tmp_dir) / f"in{ext}"
            in_path.write_bytes(input_bytes)

            cmd = ["pandoc", "-f", pandoc_from, "-t", pandoc_to, *extra_args]
            out_path: Path | None = None
            if target_format in self.BINARY_FORMATS:
                out_path = Path(tmp_dir) / f"out.{target_format}"
                cmd += ["-o", str(out_path)]
            cmd.append(str(in_path))

            spec = ExecSpec(
                command=cmd,
                cwd=tmp_dir,
                image_kind="convert",
                timeout_seconds=timeout_seconds,
            )
            result = await get_sandbox_runner().execute(
                spec, user_id=current_sandbox_user_id()
            )
            return self._collect_pandoc_output(result, out_path)

    def _collect_pandoc_output(
        self, result: ExecResult, out_path: Path | None
    ) -> str | bytes:
        """校验执行结果并收集输出：二进制读文件转 base64，文本取 stdout。"""
        self._raise_on_pandoc_failure(result)
        if out_path is not None:
            raw = out_path.read_bytes()
            if not raw:
                raise RuntimeError("Pandoc produced empty output")
            return base64.b64encode(raw).decode("ascii")
        if result.stderr:
            self._logger.warning(f"Pandoc stderr: {result.stderr}")
        converted = result.stdout
        if not converted.strip():
            raise RuntimeError("Pandoc produced empty output")
        return converted

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
