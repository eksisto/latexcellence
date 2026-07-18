"""
自动编译业务逻辑。

负责编排以下流程：
    1. 通过 project_manager 获取项目根目录
    2. 调用 latexmk -xelatex 编译主文件
    3. 拦截 stderr 和 stdout 输出
    4. 编译失败时调用 LLM 翻译错误信息为“人话”
"""

import asyncio
import re
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, ConfigDict

from backend.core.logger import get_logger
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.core.llm_client import get_llm_client
from backend.core.config import get_config
from backend.utils.json_utils import retry_with_validation

logger = get_logger(__name__)

_VALID_FILE_EXTENSIONS = (".tex",)


def _validate_main_file(main_file: str, project_root: Path) -> None:
    """
    校验 main_file 参数的合法性，防止参数注入和路径穿越。

    校验规则：
        1. 不为空 / 纯空白
        2. 不以 '-' 开头（防止被 latexmk 解析为命令行 flag）
        3. 不含 null 字节
        4. 不含 '..' 路径段（防止路径穿越）
        5. 必须以合法扩展名（.tex）结尾
        6. 规范化后必须落在项目 root 目录内
    """
    if not main_file or not main_file.strip():
        raise ValueError("main_file 不能为空")

    if "\x00" in main_file:
        raise ValueError("main_file 包含非法字符")

    if main_file.lstrip().startswith("-"):
        raise ValueError("main_file 不能以 '-' 开头")

    if ".." in Path(main_file).parts:
        raise ValueError("main_file 不能包含 '..' 路径段")

    if not any(main_file.lower().endswith(ext) for ext in _VALID_FILE_EXTENSIONS):
        raise ValueError(
            f"main_file 必须以 {'/'.join(_VALID_FILE_EXTENSIONS)} 结尾"
        )

    resolved = (project_root / main_file).resolve()
    if not str(resolved).startswith(str(project_root.resolve())):
        raise ValueError("main_file 解析后超出项目根目录范围")


class CompileResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool
    stdout: str = ""
    stderr: str = ""
    explanation: str = ""
    fixes: list[str] = []


class ErrorTranslation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explanation: str
    fixes: list[str] = []


_SYSTEM_PROMPT = (
    "你是一个专业的 LaTeX 编译错误诊断专家。\n"
    "你的任务是分析 latexmk/xelatex 的原始报错日志，定位根本原因，并用通俗易懂的中文向非专业用户解释，同时提供切实可行的修复建议。\n\n"
    "【诊断规则】\n"
    "1. 聚焦致命错误：忽略常规警告（如 Underfull \\\\hbox, Font Warning 等），优先找出导致编译失败的核心错误（如 `! Undefined control sequence`, `Fatal error`, `Missing $ inserted`, `File \\`xxx.sty' not found` 等）。\n"
    "2. 精确定位位置：必须尽可能从日志中提取出错的具体文件（如 `(./xxx.tex)`）和代码行号（如 `l.123` 代表第 123 行）。\n"
    "3. 深度分析原因：不要仅做机器翻译。例如：遇到 Undefined control sequence 需分析是拼写错误还是漏加宏包；遇到 File not found 需提示安装对应宏包或检查文件名。\n"
    "4. 具体且可操作的建议：提供具体的修改方案（如：“在导言区添加 `\\\\usepackage{amsmath}`”或“将第 15 行的 `\\\\alfa` 改为 `\\\\alpha`”）。\n"
    "5. 纯 JSON 输出：必须严格返回 JSON 格式，不要包含 Markdown 代码块或任何其他无关文本。\n\n"
    "【JSON 格式要求】\n"
    "{\n"
    '  "explanation": "<字符串：综合说明出错的文件、行号以及根本原因。语气要温和、易懂。>",\n'
    '  "fixes": [\n'
    '    "<字符串：具体的修复建议 1>",\n'
    '    "<字符串：具体的修复建议 2>"\n'
    '  ]\n'
    "}"
)

_LATEX_ERROR_PATTERN = re.compile(
    r"^! .*|^Runaway argument\?|^Emergency stop|^l\.\d+|"
    r"^No file .*|"
    r"^LaTeX Error:|^Package .* Error:|"
    r"^Missing .* inserted",
    re.MULTILINE,
)


def _extract_key_errors(log_text: str, max_chars: int = 3000) -> str:
    """从日志中提取关键错误信息，避免全文过长。"""
    matches = _LATEX_ERROR_PATTERN.findall(log_text)
    if matches:
        lines = log_text.split("\n")
        key_lines = []
        capture = False
        for line in lines:
            stripped = line.strip()
            if _LATEX_ERROR_PATTERN.match(stripped):
                capture = True
            if capture:
                key_lines.append(line)
                if len("\n".join(key_lines)) > max_chars:
                    break
        return "\n".join(key_lines)
    return log_text[-max_chars:]


async def compile_latex(main_file: str, workspace_id: str = "") -> CompileResult:
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
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        process.kill()
        stdout, stderr = await process.communicate()
        return CompileResult(
            success=False,
            stderr=stderr.decode("utf-8", errors="replace") + f"\nCompilation timed out after {timeout} seconds.",
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

    explanation = ""
    fixes = []

    combined_output = stdout_str + "\n" + stderr_str
    if combined_output.strip():
        try:
            key_errors = _extract_key_errors(combined_output)
            llm_client = get_llm_client()

            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"请分析以下 LaTeX 编译错误日志，提供通俗易懂的中文解释和具体的修复建议：\n\n"
                        f"{key_errors}"
                    ),
                },
            ]

            async def chat_fn(msgs, **kwargs):
                llm_cfg = get_config().llm
                kwargs["response_format"] = {"type": "json_object"}
                return await llm_client.chat(msgs, **kwargs)

            result = await retry_with_validation(
                chat_fn=chat_fn,
                messages=messages,
                model_cls=ErrorTranslation,
                max_retries=2,
                logger_name="compile",
                temperature=0.3,
                max_tokens=2048,
            )
            explanation = result.explanation
            fixes = result.fixes
        except Exception as e:
            logger.error(f"LLM error translation failed: {e}")
            explanation = (
                "（LLM 错误翻译失败，请查看下方原始编译输出来排查问题）"
            )

    return CompileResult(
        success=False,
        stdout=stdout_str,
        stderr=stderr_str,
        explanation=explanation,
        fixes=fixes,
    )
