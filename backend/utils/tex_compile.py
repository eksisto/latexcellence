"""
统一 TeX 编译执行模块。

职责：
    1. 通过 texfot（TeX Live 自带工具）包装 latexmk 执行编译，
       自动过滤日志中的噪声信息，只保留错误、警告等关键内容。
    2. 返回过滤后的干净输出 + 原始输出，供 LLM 分析和前端展示。
    3. 当 texfot 不可用时，静默降级为原始 latexmk 输出 + 正则 fallback。
    4. 支持引擎自动检测（根据 .tex 文件内容选择 pdflatex/xelatex/lualatex）。
    5. 支持 PDF/A-2b 归档格式输出（自动注入 pdfx 宏包）。
    6. 执行统一经 SandboxRunner（v2 P1a）：sandbox 模式下进 Docker 容器，
       auto 探测失败或 local 模式回落本地子进程（行为与历史完全一致）。

供 compile 模块（自动编译）、git_diff 模块（差异高亮）、
template_gen 模块（模板生成）、style_modify（shadow 编译）、
plot_gen（TikZ 编译）复用。
"""

import re
import shutil
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.latex_json_guard import strip_latex_comments
from backend.utils.sandbox import (
    ExecSpec,
    cached_backend_for,
    current_sandbox_user_id,
    get_sandbox_runner,
)

# 向后兼容再导出：git_diff 等模块历史上从本模块导入进程树终止工具，
# 实现已随本地执行后端收口到 sandbox.local_backend。
from backend.utils.sandbox.local_backend import terminate_process_tree  # noqa: F401

logger = get_logger(__name__)

_texfot_available: bool | None = None


class TexCompileResult(BaseModel):
    """TeX 编译执行结果。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    filtered_output: str
    raw_output: str
    timed_out: bool = False
    oom_killed: bool = False  # 沙箱内存超限被杀（docker 后端；local 恒为 False）
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


def _resolve_use_texfot() -> bool:
    """决定是否用 texfot 包装：容器内必然内置；宿主按 PATH 检测。"""
    if cached_backend_for("tex") == "docker":
        return True
    return _check_texfot_available()


def sanitize_output_paths(text: str, cwd: Path) -> str:
    """将命令输出中的工作目录绝对路径前缀改写为相对形式。

    latexmk / Python traceback 等输出天然携带绝对路径（多用户模式下即
    服务器上的用户空间长路径），这些输出会原样展示给用户，因此统一把
    ``<cwd>/xxx`` 改写为 ``./xxx``。仅做前缀替换、不解析内容，
    不影响错误定位。tex_compile 与 plot_gen 等沙箱执行方共用。

    本函数是纯文本工具，不含模式判断；调用方须仅在多用户模式下应用
    （``get_config().multi_user.enabled`` 为真时调用），单用户本地模式
    保留原始输出。
    """
    base = str(cwd).rstrip("/\\")
    if not base:
        return text
    for sep in ("/", "\\"):
        text = text.replace(base + sep, "./")
    return text


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


_LOCAL_ENGINE_SCAN_SUFFIXES = frozenset(
    {".tex", ".ltx", ".latex", ".sty", ".cls", ".cfg", ".def"}
)
_LOCAL_DEPENDENCY_RE = re.compile(
    r"\\(?P<command>input|include|subfile|documentclass|usepackage|"
    r"RequirePackage|LoadClass)"
    r"(?:\s*\[[^\]]*\])?\s*\{(?P<names>[^{}]+)\}"
)


def _strip_tex_comments(content: str) -> str:
    """移除 TeX 注释，保留转义的百分号。

    复用 latex_json_guard 的反斜杠奇偶语义（\\% 不是注释起点；
    \\\\% 中换行命令后的 % 开始注释），避免注释内容参与引擎检测。
    """
    lines: list[str] = []
    for line in content.splitlines():
        uncommented = strip_latex_comments(line)
        if uncommented.strip():
            lines.append(uncommented.strip())
    return "\n".join(lines)


def _local_dependency_candidates(
    root: Path, current_dir: Path, raw_name: str
) -> list[Path]:
    """解析项目内的 input/package/class 名称，不访问 TeX 发行版文件。"""
    name = raw_name.strip()
    if not name or name.startswith(("/", "\\")):
        return []
    relative_name = Path(name)
    bases = [current_dir / relative_name]
    root_candidate = root / relative_name
    if root_candidate != bases[0]:
        bases.append(root_candidate)

    candidates: list[Path] = []
    for base in bases:
        if base.suffix.lower() in _LOCAL_ENGINE_SCAN_SUFFIXES:
            candidates.append(base)
        else:
            candidates.extend(
                base.with_suffix(suffix) for suffix in _LOCAL_ENGINE_SCAN_SUFFIXES
            )
    return candidates


def _read_local_engine_sources(tex_path: Path) -> list[str]:
    """读取主文件及其可解析的本地依赖，供引擎检测使用。

    只沿着 ``\\input``/``\\include``/包和类加载关系扫描项目目录，避免把
    未使用的测试文件或用户 README 中的文字误判成当前文档的引擎需求。
    """
    try:
        root = tex_path.resolve().parent
        first = tex_path.resolve()
    except OSError:
        return []

    queue = [first]
    seen: set[Path] = set()
    sources: list[str] = []
    while queue and len(seen) < 128:
        current = queue.pop(0)
        try:
            current = current.resolve()
        except OSError:
            continue
        if current in seen or not current.is_file():
            continue
        try:
            if not current.is_relative_to(root):
                continue
            content = current.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError):
            continue
        seen.add(current)
        source = _strip_tex_comments(content)
        sources.append(source)

        for match in _LOCAL_DEPENDENCY_RE.finditer(source):
            current_dir = current.parent
            for raw_name in match.group("names").split(","):
                for candidate in _local_dependency_candidates(
                    root, current_dir, raw_name
                ):
                    try:
                        candidate = candidate.resolve()
                    except OSError:
                        continue
                    if (
                        candidate in seen
                        or not candidate.is_file()
                        or not candidate.is_relative_to(root)
                    ):
                        continue
                    queue.append(candidate)
                    break
    return sources


def detect_engine_from_file(tex_path: Path) -> str:
    """根据 .tex 文件内容自动检测编译引擎。

    检测逻辑（按优先级）：
    1. 读取主文件及其项目内的 \\input/\\include/类/包依赖并过滤注释
    2. 检测 XeLaTeX 专属宏包（fontspec/xeCJK/polyglossia）→ xelatex
    3. 检测 LuaLaTeX 专属宏包/命令（luacode/luatextra/directlua）→ lualatex
    4. 检测主文件或本地依赖中的非 ASCII 字符（中文、日文、韩文、特殊符号等）：
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

    sources = _read_local_engine_sources(tex_path)
    if not sources:
        logger.warning(
            f"Failed to read {tex_path} for engine detection, defaulting to pdflatex"
        )
        return "pdflatex"

    uncommented = "\n".join(sources)

    # 检测 XeLaTeX 专属宏包
    xelatex_patterns = [
        r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\s*\{[^}]*\bfontspec\b",
        r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\s*\{[^}]*\bxeCJK\b",
        r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\s*\{[^}]*\bpolyglossia\b",
        r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\s*\{[^}]*\bunicode-math\b",
        r"\\(?:documentclass|LoadClass)\s*\{[^}]*\bctex(?:art|book|rep)?\b",
        r"\\set(?:main|sans|mono|CJKmain|CJKsans|CJKfamily)font\b",
    ]
    for pattern in xelatex_patterns:
        if re.search(pattern, uncommented):
            logger.info(f"Detected XeLaTeX engine from pattern: {pattern}")
            return "xelatex"

    # 检测 LuaLaTeX 专属宏包/命令
    lualatex_patterns = [
        r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\s*\{[^}]*\bluacode\b",
        r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\s*\{[^}]*\bluatextra\b",
        r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\s*\{[^}]*\bluatexja\b",
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

    # texfot 包装决策：docker 后端镜像内置 texfot；本地按 PATH 检测
    use_texfot = _resolve_use_texfot()
    cmd = (
        ["texfot", "--quiet", "--no-interactive"] + latexmk_base
        if use_texfot
        else latexmk_base
    )

    try:
        spec = ExecSpec(
            command=cmd,
            cwd=cwd_str,
            image_kind="tex",
            timeout_seconds=float(timeout),
        )
        logger.debug(f"Running: {' '.join(cmd)} in {cwd_str}")
        res = await get_sandbox_runner().execute(
            spec, user_id=current_sandbox_user_id()
        )

        raw = res.stdout + ("\n" + res.stderr if res.stderr else "")
        # 多用户模式：输出中的服务器绝对路径统一脱敏为相对形式；
        # 单用户本地模式保留原始输出（维持现状）
        if get_config().multi_user.enabled:
            raw = sanitize_output_paths(raw, work_cwd)
        if res.timed_out:
            logger.warning(f"Compilation timed out after {timeout}s in {cwd_str}")
            return TexCompileResult(
                success=False,
                filtered_output="",
                raw_output=raw,
                timed_out=True,
                oom_killed=res.oom_killed,
                used_texfot=use_texfot,
            )
        if res.oom_killed:
            logger.error(
                f"Compilation killed by sandbox memory limit (OOM) in {cwd_str}"
            )
        success = res.returncode == 0
        filtered = raw if (use_texfot or success) else _filter_with_regex(raw)
        return TexCompileResult(
            success=success,
            filtered_output=filtered,
            raw_output=raw,
            oom_killed=res.oom_killed,
            used_texfot=use_texfot,
        )
    finally:
        # PDF/A 模式：将生成的 PDF 复制回原始目录
        if temp_dir is not None and temp_dir.exists():
            try:
                # PDF/A 编译生成的 PDF 文件名是 {stem}_pdfa.pdf
                pdfa_pdf_name = Path(actual_main_file).with_suffix(".pdf").name
                pdfa_pdf_path = temp_dir / pdfa_pdf_name
                # 保留主文件所在子目录：main_file 为 chapters/main.tex 时，
                # 原 PDF 路径应为 chapters/main.pdf，而不是项目根目录 main.pdf。
                original_pdf_path = main_tex_path.with_suffix(".pdf")

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
