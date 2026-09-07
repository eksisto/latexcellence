"""
自动编译业务逻辑。

负责编排以下流程：
    1. 通过 project_manager 获取项目根目录
    2. 可选清理项目内可再生的 LaTeX 编译产物（clean_first，编辑器链路启用）
    3. 调用 tex_compile（texfot 包装 latexmk）执行编译
    4. 编译失败时调用 LLM 翻译错误信息为“人话”
    5. SyncTeX 正/反向只读查询（editor v3 阶段一）：synctex view/edit
       经 SandboxRunner 在项目根目录执行，零写盘、响应只含相对路径
"""

import asyncio
import os
import re
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict

from backend.core.logger import get_logger
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.core.config import get_config
from backend.utils.compile_error_translator import translate_compile_errors
from backend.utils.path_guard import resolve_project_path
from backend.utils.sandbox import (
    ExecSpec,
    current_sandbox_user_id,
    get_sandbox_runner,
)
from backend.utils.tex_compile import run_tex_compile, sanitize_output_paths

logger = get_logger(__name__)


def _validate_main_file(main_file: str, project_root: Path) -> Path:
    """
    校验 main_file 参数的合法性，防止参数注入和路径穿越。

    使用 resolve_project_path 执行通用路径安全校验，
    额外检查不以 '-' 开头（防止被 latexmk 解析为命令行 flag）。

    返回：
        校验通过后的项目内绝对路径（唯一入口 resolve_project_path）。
    """
    if not main_file or not main_file.strip():
        raise ValueError("main_file must not be empty")

    if main_file.lstrip().startswith("-"):
        raise ValueError("main_file must not start with '-'")

    return resolve_project_path(
        project_root,
        main_file,
        allowed_suffixes=(".tex",),
    )


class CompileResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool
    stdout: str = ""
    stderr: str = ""
    explanation: str = ""
    fixes: list[str] = []
    # 结构化错误码（如 sandbox_killed_oom），前端据此做 i18n 翻译
    error_code: str = ""
    # SyncTeX 是否实际生效（请求 true 且未与 PDF/A 组合；editor v3 Q3）。
    # 编译页 / git_diff 等既有调用方默认 False，行为零变化
    synctex: bool = False


# ── 编译前产物清理（clean_first=True 时启用；编辑器编译链路传 true）────────
#
# 背景：latexmk 依赖项目内持久化的 .fdb_latexmk/.log 记录上次运行状态，
# 源文件未变化时短路为 "Nothing to do"，但因上次失败的错误状态仍会以
# exit 12 失败；其关键线索行（"gave an error in previous invocation"）
# 又不命中 texfot 的 stdout 过滤模式，用户只能看到无信息量的 -f 提示。
# 编译前清理再生产物可保证引擎必跑、失败日志始终来自本次运行。

# 可再生产物扩展名白名单（latexmk 默认清理集 + 常见宏包/工具产物）。
# .pdf 不在列（编译失败时保留旧预览）；.tex/.bib/.sty 等源文件不在列
_CLEANUP_SUFFIXES = frozenset(
    {
        ".aux",
        ".bbl",
        ".bcf",
        ".blg",
        ".fdb_latexmk",
        ".fls",
        ".glg",
        ".glo",
        ".gls",
        ".idx",
        ".ilg",
        ".ind",
        ".log",
        ".lof",
        ".lot",
        ".lol",
        ".nav",
        ".out",
        ".snm",
        ".spl",
        ".toc",
        ".vrb",
        ".xdv",
        ".acn",
        ".acr",
        ".alg",
        ".ist",
    }
)
# 复合后缀：Path.suffix 只取最后一段，需按文件名结尾匹配
_CLEANUP_NAME_ENDINGS = (".synctex.gz", ".synctex(busy)", ".run.xml")

# 不进入的目录（版本库内部与编译无关）
_CLEANUP_SKIP_DIRS = frozenset({".git"})


def clean_project_artifacts(root: Path) -> int:
    """清理项目内可再生的 LaTeX 编译产物，返回删除的文件数。

    遍历项目根目录（含子目录，\\input 子目录也会产生 aux 产物），按
    上述白名单删除；跳过 .git 目录、符号链接与非普通文件。扩展名全部
    为 TeX 工具链的再生产物，不触碰源文件与 PDF。同步阻塞 I/O，调用方
    须经 asyncio.to_thread 调度。
    """
    removed = 0
    for path in sorted(root.rglob("*")):
        if os.path.islink(path) or not path.is_file():
            continue
        if _CLEANUP_SKIP_DIRS.intersection(path.relative_to(root).parts):
            continue
        if path.suffix in _CLEANUP_SUFFIXES or path.name.endswith(
            _CLEANUP_NAME_ENDINGS
        ):
            try:
                path.unlink()
                removed += 1
            except OSError as e:
                logger.warning(f"Failed to remove artifact {path.name}: {e}")
    return removed


async def compile_latex(
    main_file: str,
    workspace_id: str = "",
    language: str = "zh",
    engine: str = "auto",
    pdfa: bool = False,
    synctex: bool = False,
    clean_first: bool = False,
) -> CompileResult:
    """
    编译 LaTeX 项目的主文件。

    参数：
        main_file: 主 .tex 文件相对于项目根目录的路径
        workspace_id: 工作区唯一标识符
        language: 用户语言（用于错误翻译）
        engine: 编译引擎（auto/pdflatex/xelatex/lualatex）
        pdfa: 是否启用 PDF/A-2b 归档格式输出
        synctex: 是否生成 SyncTeX 数据（编辑器正向/反向同步查询依赖）；
            与 pdfa 组合时后端忽略（响应 synctex=False）
        clean_first: 编译前先清理项目内可再生产物（.aux/.log/.fdb_latexmk
            等）。编辑器编译链路传 True：避免 latexmk 依上次失败状态短路
            且日志无具体错误；编译页 / git_diff 默认 False 零变化

    返回：
        CompileResult 对象（含编译状态、输出日志、LLM 翻译的解释和建议）
    """
    project = get_project_manager_for_workspace(workspace_id)
    if not project.is_open:
        raise RuntimeError("No project opened")

    root = Path(project.root_path)
    _validate_main_file(main_file, root)

    main_path = _validate_main_file(main_file, root)
    if not main_path.exists():
        raise FileNotFoundError(f"Main file not found: {main_file}")

    cfg = get_config()
    timeout = cfg.features.compile.latexmk_timeout

    # 使用配置默认值（如果参数为默认值）
    if engine == "auto":
        engine = cfg.features.compile.engine
    if not pdfa:
        pdfa = cfg.features.compile.pdfa

    # PDF/A 在临时目录编译后仅回拷 PDF，.synctex.gz 不同步回项目目录，
    # synctex 组合 pdfa 时视为不生效（响应如实回传，editor v3 决策 Q3）
    synctex_effective = synctex and not pdfa

    logger.info(
        f"Compiling {main_file} in {root} "
        f"(engine={engine}, pdfa={pdfa}, synctex={synctex_effective}, "
        f"clean_first={clean_first})"
    )

    if clean_first:
        # 同步文件遍历/删除经线程池调度，不阻塞事件循环
        removed = await asyncio.to_thread(clean_project_artifacts, root)
        logger.info(f"Cleaned {removed} artifact file(s) before compile")

    tex_result = await run_tex_compile(
        main_file,
        root,
        timeout,
        engine=engine,
        pdfa=pdfa,
        synctex=synctex_effective,
    )

    if tex_result.timed_out:
        logger.warning(f"Compilation timed out for {main_file}")
        return CompileResult(
            success=False,
            stderr=tex_result.raw_output,
            explanation="Compilation timed out. Possible causes: document too large, infinite LaTeX loop, or latexmk stuck.",
            fixes=[
                "Check for missing \\end{document} or unclosed environments",
                "Increase features.compile.latexmk_timeout in config.json",
                "Run latexmk -xelatex manually in a terminal to diagnose",
            ],
        )

    if tex_result.oom_killed:
        logger.warning(f"Compilation killed by sandbox memory limit for {main_file}")
        return CompileResult(
            success=False,
            stderr=tex_result.raw_output,
            error_code="sandbox_killed_oom",
            explanation="Compilation was killed because it exceeded the sandbox memory limit.",
            fixes=[
                "Reduce document complexity (large images, huge tables, excessive TikZ)",
                "Increase sandbox.memory_mb in config.json and restart the server",
            ],
        )

    if tex_result.success:
        logger.info(f"Compilation succeeded for {main_file}")
        return CompileResult(
            success=True,
            stdout=tex_result.raw_output,
            stderr=tex_result.filtered_output,
            synctex=synctex_effective,
        )

    logger.warning(f"Compilation failed for {main_file}, translating errors")

    explanation, fixes = await translate_compile_errors(
        tex_result.raw_output,
        language=language,
        filtered_output=tex_result.filtered_output,
    )

    return CompileResult(
        success=False,
        stdout=tex_result.raw_output,
        stderr=tex_result.filtered_output,
        explanation=explanation,
        fixes=fixes,
    )


# ── SyncTeX 正/反向只读查询（editor v3 阶段一 / 决策 Q1）──────────────────
#
# 两端点均为零写盘查询：synctex view / synctex edit（TeX Live 自带）经
# SandboxRunner 在项目根目录执行，不解析 .synctex.gz 前端 JS（Q1）。
# 输出中的文件路径只以相对形式返回（多用户脱敏）；file_path 经
# resolve_project_path() 唯一入口校验。超时取编译超时的一个零头，
# synctex 查询是毫秒级本地操作，10s 已是数量级冗余。

SYNCTEX_QUERY_TIMEOUT_SECONDS = 10.0

# synctex view 记录起始行：水平盒 `{Page:N` / 垂直盒 `[Page:N`（同形态
# 语义，坐标均为 72dpi 点单位、页面左上原点）
_SYNCTEX_RECORD_START_RE = re.compile(r"^[{\[]Page:(\d+)\s*$")

# synctex 记录内允许出现的坐标字段（V 为垂直盒纵深，暂不消费）
_SYNCTEX_RECORD_KEYS = frozenset({"x", "y", "w", "h"})

# 反向查询可定位的源文件白名单（与编辑器可编辑白名单同口径）
SYNCTEX_SOURCE_SUFFIXES = (".tex", ".bib", ".sty", ".cls")


class SynctexRecord(BaseModel):
    """正向查询单条定位记录（72dpi 点单位，页面左上原点）。"""

    model_config = ConfigDict(extra="forbid")

    page: int
    x: float
    y: float
    w: float = 0.0
    h: float = 0.0


class SynctexForwardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[SynctexRecord] = []


class SynctexBackwardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool = False
    file_path: str = ""
    line: int = 0
    col: int = 0


def parse_synctex_view_records(output: str) -> list[SynctexRecord]:
    """解析 synctex view 输出中的定位记录。

    记录形如（``{``/``[`` 语义等价，均以独立 ``}``/``]`` 行收尾）::

        {Page:1
        x:113.811016
        y:104.411476
        w:324.176788
        h:10.448627
        }

    缺坐标、坐标非法或未闭合的记录整体丢弃（保守解析，不猜）。
    """
    records: list[SynctexRecord] = []
    current: dict | None = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if current is None:
            match = _SYNCTEX_RECORD_START_RE.match(line)
            if match:
                current = {"page": int(match.group(1))}
            continue
        if line in ("}", "]"):
            if {"x", "y"} <= current.keys():
                try:
                    records.append(
                        SynctexRecord(
                            page=current["page"],
                            x=float(current["x"]),
                            y=float(current["y"]),
                            w=float(current.get("w") or 0.0),
                            h=float(current.get("h") or 0.0),
                        )
                    )
                except (TypeError, ValueError):
                    logger.debug("Discarding malformed synctex record: %s", current)
            current = None
            continue
        key, sep, value = line.partition(":")
        if sep and key in _SYNCTEX_RECORD_KEYS:
            # 容忍 "113.811016pt" 形态的单位后缀
            current[key] = re.sub(r"[a-zA-Z]+$", "", value).strip()
    return records


def parse_synctex_edit_result(output: str) -> tuple[str, int, int] | None:
    """解析 synctex edit 输出，返回 ``(input_path, line, col)``。

    ``Input`` 缺失或行号无效（0/负值，synctex 以 -1 标记未知行）时返回
    None，由调用方收敛为 not_found。
    """
    input_path: str | None = None
    line: int | None = None
    col: int | None = None
    for raw_line in output.splitlines():
        text = raw_line.strip()
        if text.startswith("Input:") and input_path is None:
            input_path = text[len("Input:") :].strip() or None
        elif text.startswith("Line:"):
            try:
                line = int(text[len("Line:") :].strip())
            except ValueError:
                pass
        elif text.startswith("Column:"):
            try:
                col = int(text[len("Column:") :].strip())
            except ValueError:
                pass
    if not input_path or line is None or line < 1:
        return None
    return input_path, line, (col if col is not None and col >= 0 else 0)


def _relativize_synctex_input(input_path: str, root: Path) -> str | None:
    """把 synctex 输出的 Input 路径归一为项目内相对 POSIX 路径。

    latexmk 以项目根为 cwd 编译时记录的 Input 多为相对路径（原样保留，
    仅剥 ``./`` 前缀）；绝对路径仅当位于项目根内时转相对，项目外（texmf
    系统文件等）一律拒绝——响应绝不携带绝对路径（多用户脱敏约束）。
    """
    candidate = input_path.strip()
    if not candidate:
        return None
    path = Path(candidate)
    if not path.is_absolute():
        # PurePosixPath 归一化掉 "." 段并保留 ".." 段：含 ".." 的相对路径
        # 不在项目根内，拒绝（不能用 lstrip 剥前缀，会误伤 ".." 与点开头的文件名）
        pure = PurePosixPath(path.as_posix())
        if pure.is_absolute() or ".." in pure.parts:
            return None
        return pure.as_posix() or None
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


async def _run_synctex_query(command: list[str], root: Path) -> str:
    """经 SandboxRunner 执行一条 synctex 查询，返回合并后的标准输出。

    抛出的 RuntimeError 交由路由层按既有 compile 端点的异常映射规则
    （沙箱基础设施错误优先）处理；执行失败/超时/OOM 收敛为空输出。
    """
    spec = ExecSpec(
        command=command,
        cwd=str(root),
        image_kind="tex",
        timeout_seconds=SYNCTEX_QUERY_TIMEOUT_SECONDS,
    )
    logger.debug("Running synctex query: %s", " ".join(command))
    res = await get_sandbox_runner().execute(spec, user_id=current_sandbox_user_id())
    if res.timed_out:
        logger.warning(
            "synctex query timed out: %s", command[1] if len(command) > 1 else command
        )
        return ""
    if res.oom_killed:
        logger.warning("synctex query killed by sandbox memory limit")
        return ""
    output = res.stdout + ("\n" + res.stderr if res.stderr else "")
    # 多用户模式：输出中的服务器绝对路径统一脱敏为相对形式（与编译
    # 日志同规则）；单用户本地模式保留原始输出
    if get_config().multi_user.enabled:
        output = sanitize_output_paths(output, root)
    return output


def _resolve_project_or_raise(workspace_id: str) -> Path:
    """解析工作区项目根目录；未打开项目抛 RuntimeError（路由层映射 400）。"""
    project = get_project_manager_for_workspace(workspace_id)
    if not project.is_open:
        raise RuntimeError("No project opened")
    return Path(project.root_path)


def _resolve_synctex_pdf(workspace_id: str, main_file: str) -> tuple[Path, str]:
    """校验工作区与 main_file，返回 ``(项目根, pdf 相对 POSIX 路径)``。

    与既有 /api/compile/pdf 端点同构：main_file 经 _validate_main_file
    （唯一入口 resolve_project_path）校验后派生同名 PDF；尚未编译时抛
    FileNotFoundError（路由层映射 404）。
    """
    root = _resolve_project_or_raise(workspace_id)
    pdf_abs = _validate_main_file(main_file, root).with_suffix(".pdf")
    if not pdf_abs.exists():
        raise FileNotFoundError(
            f"PDF not found: {pdf_abs.name}. Please compile the project first."
        )
    return root, Path(main_file).with_suffix(".pdf").as_posix()


async def synctex_forward(
    workspace_id: str,
    main_file: str,
    file_path: str,
    line: int,
    col: int = 0,
) -> SynctexForwardResponse:
    """正向同步：源码 ``(file_path, line, col)`` → PDF 页面矩形记录。

    执行 ``synctex view -i "line:col:file" -o <pdf>``（cwd=项目根），
    返回 72dpi 点单位的页面矩形（页面左上原点）。零写盘。
    """
    root, pdf_rel = _resolve_synctex_pdf(workspace_id, main_file)
    resolved_source = resolve_project_path(
        root, file_path, allowed_suffixes=SYNCTEX_SOURCE_SUFFIXES
    )
    source_rel = resolved_source.relative_to(root.resolve()).as_posix()
    command = [
        "synctex",
        "view",
        "-i",
        f"{line}:{col}:{source_rel}",
        "-o",
        pdf_rel,
    ]
    output = await _run_synctex_query(command, root)
    records = parse_synctex_view_records(output)
    if not records:
        logger.info(
            f"synctex view returned no records for {file_path}:{line} (main={main_file})"
        )
    return SynctexForwardResponse(records=records)


async def synctex_backward(
    workspace_id: str,
    main_file: str,
    page: int,
    x: float,
    y: float,
) -> SynctexBackwardResponse:
    """反向同步：PDF ``(page, x, y)`` → 源码位置。

    执行 ``synctex edit -o "page:x:y:<pdf>"``（cwd=项目根），返回项目内
    相对路径与行号；找不到对应源码位置时返回 ``found=False``（HTTP 200，
    属合法空结果而非错误）。零写盘。
    """
    root, pdf_rel = _resolve_synctex_pdf(workspace_id, main_file)
    command = [
        "synctex",
        "edit",
        "-o",
        f"{page}:{x}:{y}:{pdf_rel}",
    ]
    output = await _run_synctex_query(command, root)
    parsed = parse_synctex_edit_result(output)
    if parsed is None:
        logger.info(f"synctex edit found no source for page={page} (main={main_file})")
        return SynctexBackwardResponse(found=False)
    input_path, line, col = parsed
    rel = _relativize_synctex_input(input_path, root)
    if rel is None:
        # 定位到项目外文件（texmf / 生成文件）：不泄露路径，按未命中处理
        logger.info(f"synctex edit target outside project root, dropping: page={page}")
        return SynctexBackwardResponse(found=False)
    return SynctexBackwardResponse(found=True, file_path=rel, line=line, col=col)
