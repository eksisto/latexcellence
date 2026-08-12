"""
统一 TeX 编译执行模块。

职责：
    1. 通过 texfot（TeX Live 自带工具）包装 latexmk 执行编译，
       自动过滤日志中的噪声信息，只保留错误、警告等关键内容。
    2. 返回过滤后的干净输出 + 原始输出，供 LLM 分析和前端展示。
    3. 当 texfot 不可用时，静默降级为原始 latexmk 输出 + 正则 fallback。
    4. 支持引擎自动检测（根据 .tex 文件内容选择 pdflatex/xelatex/lualatex）。
    5. 支持 PDF/A-2b 归档格式输出（自动注入 pdfx 宏包）。

供 compile 模块（自动编译）、git_diff 模块（差异高亮）、
template_gen 模块（模板生成）复用。
"""

import asyncio
import os
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from backend.core.logger import get_logger

logger = get_logger(__name__)

_texfot_available: bool | None = None


class TexCompileResult(BaseModel):
    """TeX 编译执行结果。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    filtered_output: str
    raw_output: str
    timed_out: bool = False
    used_texfot: bool = True


def _check_texfot_available() -> bool:
    """检测 texfot 是否在 PATH 中可用（模块级缓存，只查一次）。"""
    global _texfot_available
    if _texfot_available is None:
        _texfot_available = shutil.which("texfot") is not None
        if _texfot_available:
            logger.info("texfot is available, will use it for compile filtering")
        else:
            logger.info("texfot not found on PATH, falling back to raw latexmk output")
    return _texfot_available


async def terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    """终止进程及其子进程树，供 latexmk / latexdiff 等会派生子进程的命令使用。

    只 kill 顶层进程时，Windows 上 xelatex / bibtex 等子进程会残留并占用
    文件锁。子进程创建时已通过 ``creationflags`` / ``start_new_session``
    建立独立进程组，这里整组终止。taskkill 不可用或失败时回退为只杀
    顶层进程（与旧行为一致）。
    """
    pid = process.pid
    # pid<=1（0/1 或 MagicMock 缺省值）绝不能作为 killpg 目标：
    # PID 1 的进程组可能正是当前进程组，误杀会带走整个服务/测试进程
    if not pid or pid <= 1:
        return

    def _kill() -> None:
        if os.name == "nt":
            try:
                result = subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    return
            except Exception:
                pass
            try:
                process.kill()
            except ProcessLookupError:
                pass
            return

        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.kill()
            except ProcessLookupError:
                pass

    await asyncio.to_thread(_kill)


def _filter_with_regex(raw_output: str, max_chars: int = 3000) -> str:
    """降级路径：用正则从原始日志中提取关键错误行。

    当 texfot 不可用时作为 fallback，复用 compile_error_translator 中的
    同款正则逻辑。
    """
    import re

    error_pattern = re.compile(
        r"^! .*|^Runaway argument\?|^Emergency stop|^l\.\d+|"
        r"^No file .*|"
        r"^LaTeX Error:|^Package .* Error:|"
        r"^Missing .* inserted",
        re.MULTILINE,
    )

    lines = raw_output.split("\n")
    key_lines: list[str] = []
    capture = False
    for line in lines:
        stripped = line.strip()
        if error_pattern.match(stripped):
            capture = True
        if capture:
            key_lines.append(line)
            if len("\n".join(key_lines)) > max_chars:
                break

    if key_lines:
        return "\n".join(key_lines)
    return raw_output[-max_chars:] if len(raw_output) > max_chars else raw_output


def detect_engine_from_file(tex_path: Path) -> str:
    """根据 .tex 文件内容自动检测编译引擎。

    检测逻辑（按优先级）：
    1. 读取文件内容并过滤注释
    2. 检测 XeLaTeX 专属宏包（fontspec/xeCJK/polyglossia）→ xelatex
    3. 检测 LuaLaTeX 专属宏包/命令（luacode/luatextra/directlua）→ lualatex
    4. 检测非 ASCII 字符（中文、日文、韩文、特殊符号等）：
       - pdfLaTeX 不支持 Unicode，排除 pdfLaTeX
       - 若已命中 LuaLaTeX 规则则返回 lualatex
       - 否则默认 xelatex
    5. 以上都不匹配 → pdflatex

    参数：
        tex_path: .tex 文件路径

    返回：
        引擎名称：pdflatex/xelatex/lualatex
    """
    if not tex_path.exists():
        logger.warning(
            f"File not found for engine detection: {tex_path}, defaulting to pdflatex"
        )
        return "pdflatex"

    try:
        content = tex_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning(
            f"Failed to read {tex_path} for engine detection: {e}, defaulting to pdflatex"
        )
        return "pdflatex"

    # 过滤注释：移除行内 % 注释，删除空行
    lines = content.split("\n")
    uncommented_lines = []
    for line in lines:
        # 移除行内注释（简单处理，不处理转义的 %）
        if "%" in line:
            line = line[: line.index("%")]
        stripped = line.strip()
        if stripped:
            uncommented_lines.append(stripped)

    uncommented = "\n".join(uncommented_lines)

    # 检测 XeLaTeX 专属宏包
    xelatex_patterns = [
        r"\\usepackage\{fontspec\}",
        r"\\usepackage\{xeCJK\}",
        r"\\usepackage\{polyglossia\}",
    ]
    for pattern in xelatex_patterns:
        if re.search(pattern, uncommented):
            logger.info(f"Detected XeLaTeX engine from pattern: {pattern}")
            return "xelatex"

    # 检测 LuaLaTeX 专属宏包/命令
    lualatex_patterns = [
        r"\\usepackage\{luacode\}",
        r"\\usepackage\{luatextra\}",
        r"\\directlua",
    ]
    for pattern in lualatex_patterns:
        if re.search(pattern, uncommented):
            logger.info(f"Detected LuaLaTeX engine from pattern: {pattern}")
            return "lualatex"

    # 检测非 ASCII 字符：pdfLaTeX 不支持 Unicode，需要 XeLaTeX 或 LuaLaTeX
    # 排除 LaTeX 命令中的字符（如 \command{中文} 只检测花括号内的内容）
    has_non_ascii = bool(re.search(r"[^\x00-\x7F]", uncommented))
    if has_non_ascii:
        logger.info(
            "Non-ASCII characters detected, defaulting to xelatex for Unicode support"
        )
        return "xelatex"

    logger.info("No special engine requirements detected, using pdflatex")
    return "pdflatex"


def inject_pdfx_for_pdfa(tex_path: Path, output_path: Path) -> bool:
    """在 \\documentclass 之后注入 \\usepackage[a-2b]{pdfx} 以支持 PDF/A 输出。

    注入逻辑：
    1. 检查是否已存在 pdfx 或 pdfmanagement 包
    2. 找到 \\documentclass 行
    3. 在其后插入 \\usepackage[a-2b]{pdfx}

    参数：
        tex_path: 原始 .tex 文件路径
        output_path: 输出 .tex 文件路径

    返回：
        是否成功注入
    """
    if not tex_path.exists():
        logger.error(f"Source file not found for PDF/A injection: {tex_path}")
        return False

    try:
        content = tex_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.error(f"Failed to read {tex_path} for PDF/A injection: {e}")
        return False

    # 检查是否已存在 pdfx 或 pdfmanagement 包
    if re.search(r"\\usepackage(\[.*\])?\{pdfx\}", content):
        logger.info("pdfx package already present, skipping injection")
        # 直接复制文件
        output_path.write_text(content, encoding="utf-8")
        return True

    if re.search(r"\\usepackage(\[.*\])?\{pdfmanagement-testphase\}", content):
        logger.info("pdfmanagement-testphase already present, skipping injection")
        output_path.write_text(content, encoding="utf-8")
        return True

    # 找到 \documentclass 行并注入
    lines = content.split("\n")
    new_lines = []
    injected = False

    for line in lines:
        new_lines.append(line)
        # 匹配 \documentclass（可能带选项）
        if not injected and re.match(r"\\documentclass(\[.*\])?\{", line.strip()):
            new_lines.append(
                "\\usepackage[a-2b]{pdfx} % Auto-injected for PDF/A compliance"
            )
            injected = True
            logger.info("Injected \\usepackage[a-2b]{pdfx} after \\documentclass")

    if not injected:
        logger.warning("Could not find \\documentclass to inject pdfx package")
        output_path.write_text(content, encoding="utf-8")
        return False

    output_path.write_text("\n".join(new_lines), encoding="utf-8")
    return True


async def run_tex_compile(
    main_file: str,
    cwd: str | Path,
    timeout: float,
    extra_args: list[str] | None = None,
    engine: str = "auto",
    pdfa: bool = False,
) -> TexCompileResult:
    """通过 texfot 包装 latexmk 执行 TeX 编译，返回过滤后的输出。

    参数：
        main_file: 主 .tex 文件路径（相对于 cwd）
        cwd: 编译工作目录
        timeout: 超时秒数
        extra_args: 传递给 latexmk 的额外参数（如 ["-halt-on-error"]）
        engine: 编译引擎（auto/pdflatex/xelatex/lualatex），auto 时根据文件内容检测
        pdfa: 是否启用 PDF/A-2b 归档格式输出

    返回：
        TexCompileResult（含过滤后输出、原始输出、成功/失败状态）
    """
    cwd_path = Path(cwd)
    main_tex_path = cwd_path / main_file

    # 处理引擎选择
    if engine == "auto":
        resolved_engine = detect_engine_from_file(main_tex_path)
    else:
        resolved_engine = engine

    # 映射引擎到 latexmk 参数
    engine_args_map = {
        "pdflatex": "-pdf",
        "xelatex": "-xelatex",
        "lualatex": "-lualatex",
    }
    engine_arg = engine_args_map.get(resolved_engine, "-xelatex")

    # 处理 PDF/A 模式：创建临时目录并注入 pdfx 包
    temp_dir = None
    work_cwd = cwd_path
    actual_main_file = main_file

    if pdfa:
        temp_dir = Path(tempfile.mkdtemp(prefix="latexcellence_pdfa_"))
        # 复制项目文件到临时目录
        import shutil as shutil_copy

        for item in cwd_path.iterdir():
            if item.is_file():
                shutil_copy.copy2(item, temp_dir / item.name)
            elif item.is_dir() and item.name != "__pycache__":
                shutil_copy.copytree(item, temp_dir / item.name, dirs_exist_ok=True)

        # 注入 pdfx 包
        temp_tex_path = temp_dir / main_file
        pdfa_tex_path = temp_dir / f"{main_tex_path.stem}_pdfa.tex"

        if inject_pdfx_for_pdfa(temp_tex_path, pdfa_tex_path):
            actual_main_file = pdfa_tex_path.name
            work_cwd = temp_dir
            logger.info(f"PDF/A mode: working in temp dir {temp_dir}")
        else:
            # 注入失败，清理临时目录并继续正常编译
            shutil.rmtree(temp_dir, ignore_errors=True)
            temp_dir = None
            logger.warning("PDF/A injection failed, continuing with normal compilation")

    cwd_str = str(work_cwd)
    latexmk_base = ["latexmk", engine_arg, "-interaction=nonstopmode"]
    if extra_args:
        latexmk_base.extend(extra_args)
    latexmk_base.append(actual_main_file)

    try:
        if _check_texfot_available():
            cmd = ["texfot", "--quiet", "--no-interactive"] + latexmk_base
            logger.debug(f"Running: {' '.join(cmd)} in {cwd_str}")
            try:
                proc_kwargs: dict = {}
                if os.name == "nt":
                    proc_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                else:
                    proc_kwargs["start_new_session"] = True
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=cwd_str,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    **proc_kwargs,
                )
                stdout_b, stderr_b = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                await terminate_process_tree(process)
                stdout_b, stderr_b = await process.communicate()
                raw = _decode(stdout_b, stderr_b)
                return TexCompileResult(
                    success=False,
                    filtered_output="",
                    raw_output=raw,
                    timed_out=True,
                )

            raw = _decode(stdout_b, stderr_b)
            filtered = raw
            success = process.returncode == 0
            return TexCompileResult(
                success=success,
                filtered_output=filtered,
                raw_output=raw,
                used_texfot=True,
            )

        # ── 降级路径：直接 latexmk + 正则过滤 ──
        logger.debug(f"Running (fallback): {' '.join(latexmk_base)} in {cwd_str}")
        try:
            proc_kwargs: dict = {}
            if os.name == "nt":
                proc_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                proc_kwargs["start_new_session"] = True
            process = await asyncio.create_subprocess_exec(
                *latexmk_base,
                cwd=cwd_str,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **proc_kwargs,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            await terminate_process_tree(process)
            stdout_b, stderr_b = await process.communicate()
            raw = _decode(stdout_b, stderr_b)
            return TexCompileResult(
                success=False,
                filtered_output="",
                raw_output=raw,
                timed_out=True,
                used_texfot=False,
            )

        raw = _decode(stdout_b, stderr_b)
        success = process.returncode == 0
        filtered = raw if success else _filter_with_regex(raw)
        return TexCompileResult(
            success=success,
            filtered_output=filtered,
            raw_output=raw,
            used_texfot=False,
        )
    finally:
        # PDF/A 模式：将生成的 PDF 复制回原始目录
        if temp_dir is not None and temp_dir.exists():
            try:
                # PDF/A 编译生成的 PDF 文件名是 {stem}_pdfa.pdf
                pdfa_pdf_name = Path(actual_main_file).with_suffix(".pdf").name
                pdfa_pdf_path = temp_dir / pdfa_pdf_name
                original_pdf_path = cwd_path / main_tex_path.with_suffix(".pdf").name

                if pdfa_pdf_path.exists():
                    shutil.copy2(pdfa_pdf_path, original_pdf_path)
                    logger.info(f"Copied PDF/A output to {original_pdf_path}")
                else:
                    logger.warning(f"PDF/A output not found: {pdfa_pdf_path}")
            except Exception as e:
                logger.error(f"Failed to copy PDF/A output: {e}")
            finally:
                # 清理临时目录
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.debug(f"Cleaned up PDF/A temp dir: {temp_dir}")


def _decode(stdout_b: bytes, stderr_b: bytes) -> str:
    """合并解码 stdout + stderr。"""
    stdout_str = stdout_b.decode("utf-8", errors="replace")
    stderr_str = stderr_b.decode("utf-8", errors="replace")
    if stderr_str:
        return stdout_str + "\n" + stderr_str
    return stdout_str
