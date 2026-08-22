"""
自动编译业务逻辑。

负责编排以下流程：
    1. 通过 project_manager 获取项目根目录
    2. 调用 tex_compile（texfot 包装 latexmk）执行编译
    3. 编译失败时调用 LLM 翻译错误信息为“人话”
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from backend.core.logger import get_logger
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.core.config import get_config
from backend.utils.compile_error_translator import translate_compile_errors
from backend.utils.path_guard import resolve_project_path
from backend.utils.tex_compile import run_tex_compile

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


async def compile_latex(
    main_file: str,
    workspace_id: str = "",
    language: str = "zh",
    engine: str = "auto",
    pdfa: bool = False,
) -> CompileResult:
    """
    编译 LaTeX 项目的主文件。

    参数：
        main_file: 主 .tex 文件相对于项目根目录的路径
        workspace_id: 工作区唯一标识符
        language: 用户语言（用于错误翻译）
        engine: 编译引擎（auto/pdflatex/xelatex/lualatex）
        pdfa: 是否启用 PDF/A-2b 归档格式输出

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

    logger.info(f"Compiling {main_file} in {root} (engine={engine}, pdfa={pdfa})")

    tex_result = await run_tex_compile(
        main_file, root, timeout, engine=engine, pdfa=pdfa
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
