"""
自动编译业务逻辑。

负责编排以下流程：
    1. 通过 project_manager 获取项目根目录
    2. 调用 latexmk -xelatex 编译主文件
    3. 拦截 stderr 和 stdout 输出
    4. 编译失败时调用 LLM 翻译错误信息为“人话”
"""

import asyncio
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from backend.core.logger import get_logger
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.core.config import get_config
from backend.utils.compile_error_translator import translate_compile_errors
from backend.utils.path_guard import resolve_project_path

logger = get_logger(__name__)


def _validate_main_file(main_file: str, project_root: Path) -> None:
    """
    校验 main_file 参数的合法性，防止参数注入和路径穿越。

    使用 resolve_project_path 执行通用路径安全校验，
    额外检查不以 '-' 开头（防止被 latexmk 解析为命令行 flag）。
    """
    if not main_file or not main_file.strip():
        raise ValueError("main_file 不能为空")

    if main_file.lstrip().startswith("-"):
        raise ValueError("main_file 不能以 '-' 开头")

    resolve_project_path(
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


async def compile_latex(
    main_file: str, workspace_id: str = "", language: str = "zh"
) -> CompileResult:
    """
    编译 LaTeX 项目的主文件。

    参数：
        main_file: 主 .tex 文件相对于项目根目录的路径
        workspace_id: 工作区唯一标识符

    返回：
        CompileResult 对象（含编译状态、输出日志、LLM 翻译的解释和建议）
    """
    project = get_project_manager_for_workspace(workspace_id)
    if not project.is_open:
        raise RuntimeError("No project opened")

    root = Path(project.root_path)
    _validate_main_file(main_file, root)

    main_path = root / main_file
    if not main_path.exists():
        raise FileNotFoundError(f"Main file not found: {main_file}")

    cfg = get_config()
    timeout = cfg.features.compile.latexmk_timeout

    logger.info(f"Compiling {main_file} in {root}")

    try:
        process = await asyncio.create_subprocess_exec(
            "latexmk",
            "-xelatex",
            "-interaction=nonstopmode",
            str(main_file),
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        stdout, stderr = await process.communicate()
        return CompileResult(
            success=False,
            stderr=stderr.decode("utf-8", errors="replace")
            + f"\nCompilation timed out after {timeout} seconds.",
            explanation="编译超时。可能原因：文档过大、有无限循环的 LaTeX 命令、或 latexmk 卡住。",
            fixes=[
                "检查是否缺少 \\end{document} 或类似的环境闭合语句",
                "增加 config.json 中 features.compile.latexmk_timeout 的值",
                "在终端手动运行 latexmk -xelatex 排除环境问题",
            ],
        )

    stdout_str = stdout.decode("utf-8", errors="replace")
    stderr_str = stderr.decode("utf-8", errors="replace")

    success = process.returncode == 0

    if success:
        logger.info(f"Compilation succeeded for {main_file}")
        return CompileResult(success=True, stdout=stdout_str, stderr=stderr_str)

    logger.warning(f"Compilation failed for {main_file}, translating errors")

    explanation, fixes = await translate_compile_errors(
        stdout_str, stderr_str, language
    )

    return CompileResult(
        success=False,
        stdout=stdout_str,
        stderr=stderr_str,
        explanation=explanation,
        fixes=fixes,
    )
