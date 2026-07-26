"""
LaTeX 宏包分析器。

职责：
    1. 输入一段 LaTeX 代码
    2. 使用 LLM 分析该代码使用的 LaTeX 命令/环境
    3. 判断需要哪些额外宏包支持
    4. 返回宏包名称列表

设计原则：
    - 独立于具体业务模块（表格 OCR、公式 OCR 等均可复用）
    - 返回空列表表示不需要任何特定宏包
    - LLM 调用失败时记录警告并返回空列表（优雅降级）
"""

from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.core.prompts import get_prompt
from backend.utils.json_utils import retry_with_validation

logger = get_logger("latex_package_analyzer")


class LatexPackageAnalysis(BaseModel):
    packages: list[str]


async def analyze_latex_packages(latex_code: str, llm) -> list[str]:
    """
    分析 LaTeX 代码所需的宏包列表。

    参数：
        latex_code: 待分析的 LaTeX 代码
        llm: LLMClient 实例，用于调用 LLM

    返回：
        宏包名称列表。若不需要任何特定宏包，返回空列表。
        若 LLM 调用失败，记录警告并返回空列表（优雅降级）。
    """
    if not latex_code or not latex_code.strip():
        return []

    messages = [
        {"role": "system", "content": get_prompt("latex_package_analyzer", "system")},
        {
            "role": "user",
            "content": f"Analyze this LaTeX code and list required packages:\n\n{latex_code}",
        },
    ]

    try:
        validated: LatexPackageAnalysis = await retry_with_validation(
            chat_fn=llm.chat,
            messages=messages,
            model_cls=LatexPackageAnalysis,
            max_retries=2,
            logger_name="latex_package_analyzer",
            temperature=0.1,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        return validated.packages
    except Exception as e:
        logger.warning(f"Package analysis failed, falling back to empty list: {e}")
        return []


async def prepend_package_comments(llm, latex_code: str) -> str:
    """
    分析 LaTeX 代码所需的宏包，将注释前置到代码前。

    参数：
        llm: LLMClient 实例
        latex_code: LaTeX 代码

    返回：
        带宏包注释前缀的 LaTeX 代码（若无需要或失败则原样返回）
    """
    packages = await analyze_latex_packages(latex_code, llm)
    if packages:
        package_comments = packages_to_comments(packages)
        logger.info(f"Detected packages: {packages}")
        return package_comments + "\n" + latex_code
    logger.debug("No special packages detected")
    return latex_code


def packages_to_comments(packages: list[str]) -> str:
    """
    将宏包名称列表转换为 LaTeX 注释格式。

    参数：
        packages: 宏包名称列表

    返回：
        格式化的注释字符串，每行一个 % \\usepackage{...}
        若列表为空，返回空字符串。
    """
    if not packages:
        return ""
    return "\n".join(f"% \\usepackage{{{pkg}}}" for pkg in packages)
