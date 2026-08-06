"""
LaTeX 宏包分析器。

职责：
    1. 输入一段 LaTeX 代码
    2. 通过确定性规则表（latex_package_rules）扫描命令/环境/列说明符
    3. 判断需要哪些额外宏包支持
    4. 返回宏包名称列表

设计原则：
    - 主路径为规则表驱动的确定性检测：零 LLM 开销、结果可复现
    - LLM 仅作为兜底：规则表未命中任何宏包时才调用，且输出经 schema 校验、
      归一化（小写/去 .sty/去重/排序）与领域/已加载过滤
    - 返回空列表表示不需要任何特定宏包
    - 兜底调用失败时记录警告并返回空列表（优雅降级）
    - 支持领域参数（general / math / table）：按领域排除无关宏包
      （如公式片段不推荐表格类宏包）
    - 支持排除已加载宏包：扫描 \\usepackage / \\RequirePackage
      （含可选参数、逗号分隔多包），避免整篇文档重复推荐
"""

import re
from collections.abc import Iterable

from pydantic import BaseModel

from backend.core.logger import get_logger
from backend.core.prompts import get_prompt
from backend.utils.json_utils import retry_with_validation
from backend.utils.latex_literals import find_literal_spans, find_matching_brace
from backend.utils.latex_package_rules import (
    ALL_KNOWN_PACKAGES,
    COLSPEC_ENVS,
    COLUMN_LETTER_PACKAGES,
    COLUMN_PREFIX_PACKAGES,
    COMMAND_PACKAGES,
    DOMAIN_EXCLUDED_PACKAGES,
    ENV_PACKAGES,
    WIDTH_FIRST_ENVS,
    X_NATIVE_ENVS,
)

logger = get_logger("latex_package_analyzer")

_COMMAND_RE = re.compile(r"\\([A-Za-z@]+)")
_BEGIN_ENV_RE = re.compile(r"\\begin\{([^{}]+)\}")
_FLOAT_H_RE = re.compile(r"\\begin\{(?:figure|table)\}\s*\[[^\]]*H")
_LOADED_PACKAGE_RE = re.compile(
    r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}"
)

_VALID_DOMAINS = frozenset(DOMAIN_EXCLUDED_PACKAGES)


class LatexPackageAnalysis(BaseModel):
    packages: list[str]


def _validate_domain(domain: str) -> None:
    """校验领域参数，未知领域快速失败。"""
    if domain not in _VALID_DOMAINS:
        raise ValueError(
            f"Unknown domain: {domain!r}. Supported: {sorted(_VALID_DOMAINS)}"
        )


def detect_required_packages(
    latex_code: str, domain: str = "general", exclude_loaded: bool = True
) -> list[str]:
    """确定性规则引擎：扫描 LaTeX 代码，返回所需宏包（排序去重）。

    参数：
        latex_code: 待分析的 LaTeX 代码
        domain: 领域参数（general / math / table），按领域排除无关宏包
        exclude_loaded: 是否排除已通过 \\usepackage / \\RequirePackage 加载的宏包
            （默认 True：会剔除已加载宏包。仅分析含 preamble 的整篇文档时依赖此默认；
            传入的片段只要不含宏包加载语句，结果不受影响）

    扫描顺序：
        1. 屏蔽逐字内容（\\verb / \\lstlisting / \\url 等）与 % 注释
        2. 匹配已知命令 → 宏包
        3. 匹配已知环境 → 宏包
        4. 解析表格类环境的列说明符（X / S / D / >{} 等）
        5. 识别 figure/table 的 [H] 浮动位置（需要 float 宏包）
        6. 按领域排除无关宏包，排除已加载宏包
    """
    _validate_domain(domain)
    if not latex_code or not latex_code.strip():
        return []

    text = _mask_literals_and_comments(latex_code)
    return _apply_exclusions(_scan_rules(text), text, domain, exclude_loaded)


def _scan_rules(text: str) -> set[str]:
    """纯规则扫描：命令/环境/列说明符/[H] 浮动，不做领域与已加载过滤。"""
    packages: set[str] = set()

    for match in _COMMAND_RE.finditer(text):
        pkg = COMMAND_PACKAGES.get(match.group(1))
        if pkg:
            packages.add(pkg)

    for match in _BEGIN_ENV_RE.finditer(text):
        pkg = ENV_PACKAGES.get(match.group(1))
        if pkg:
            packages.add(pkg)

    packages.update(_scan_column_specs(text))

    if _FLOAT_H_RE.search(text):
        packages.add("float")

    return packages


def _extract_loaded_packages(text: str) -> set[str]:
    """提取已加载的宏包名（小写、去 .sty、逗号分隔多包）。"""
    loaded: set[str] = set()
    for match in _LOADED_PACKAGE_RE.finditer(text):
        for name in match.group(1).split(","):
            name = name.strip().lower()
            if name.endswith(".sty"):
                name = name[:-4]
            if name:
                loaded.add(name)
    return loaded


def _apply_exclusions(
    packages: Iterable[str],
    text: str,
    domain: str,
    exclude_loaded: bool,
) -> list[str]:
    """按领域与已加载宏包过滤结果，归一化并排序返回。"""
    excluded_domain = {p.lower() for p in DOMAIN_EXCLUDED_PACKAGES[domain]}
    loaded = _extract_loaded_packages(text) if exclude_loaded else set()
    canonical = {p.lower(): p for p in ALL_KNOWN_PACKAGES}
    result: set[str] = set()
    for pkg in packages:
        name = str(pkg).strip()
        lowered = name.lower()
        if lowered.endswith(".sty"):
            lowered = lowered[:-4]
        if not lowered:
            continue
        if lowered in excluded_domain:
            continue
        if lowered in loaded:
            continue
        # 优先使用规则表中的规范大小写（如 xeCJK），LLM 兜底输出的小写名同样回映。
        result.add(canonical.get(lowered, lowered))
    return sorted(result)


async def analyze_latex_packages(
    latex_code: str, llm=None, domain: str = "general"
) -> list[str]:
    """
    分析 LaTeX 代码所需的宏包列表。

    参数：
        latex_code: 待分析的 LaTeX 代码
        llm: 可选 LLMClient 实例；仅在规则表未命中任何宏包时作为兜底使用
        domain: 领域参数（general / math / table），结果按领域过滤

    返回：
        宏包名称列表。若不需要任何特定宏包，返回空列表。
        若兜底调用失败，记录警告并返回空列表（优雅降级）。
    """
    _validate_domain(domain)
    if not latex_code or not latex_code.strip():
        return []

    text = _mask_literals_and_comments(latex_code)
    raw = _scan_rules(text)
    # 规则表有原始命中（即使被领域/已加载过滤掉）就不再调用 LLM 兜底，
    # 避免 LLM 重新推荐被领域排除的宏包。
    if raw or llm is None:
        return _apply_exclusions(raw, text, domain, exclude_loaded=True)

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
        normalized = _normalize_packages(validated.packages)
        return _apply_exclusions(normalized, text, domain, exclude_loaded=True)
    except Exception as e:
        logger.warning(f"Package analysis fallback failed, returning empty list: {e}")
        return []


async def prepend_package_comments(
    llm, latex_code: str, domain: str = "general"
) -> str:
    """
    分析 LaTeX 代码所需的宏包，将注释前置到代码前。

    参数：
        llm: LLMClient 实例（可为 None，此时完全走规则表，无 LLM 兜底）
        latex_code: LaTeX 代码
        domain: 领域参数（general / math / table）

    返回：
        带宏包注释前缀的 LaTeX 代码（若无需要或失败则原样返回）
    """
    packages = await analyze_latex_packages(latex_code, llm, domain=domain)
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


def _normalize_packages(packages: list[str]) -> list[str]:
    """归一化 LLM 兜底输出：小写、去 .sty、去重、排序。"""
    seen: set[str] = set()
    result: list[str] = []
    for pkg in packages:
        name = str(pkg).strip().lower()
        if name.endswith(".sty"):
            name = name[:-4]
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return sorted(result)


def _mask_literals_and_comments(text: str) -> str:
    """屏蔽逐字内容与 % 注释，避免其中的伪命令触发误报。"""
    chars = list(text)
    for span in reversed(find_literal_spans(text)):
        if span.kind == "env":
            # 保留 \\begin{...} / \\end{...} 令牌（lstlisting 等环境本身需要命中规则），
            # 只屏蔽环境体内容。
            begin_match = re.match(r"\\begin\{[^{}]*\}", text[span.start : span.end])
            end_search = re.search(r"\\end\{[^{}]*\}", text[span.start : span.end])
            mask_start = span.start + (len(begin_match.group(0)) if begin_match else 0)
            mask_end = span.start + (
                end_search.start() if end_search else span.end - span.start
            )
            for i in range(mask_start, mask_end):
                chars[i] = " "
        elif span.kind == "url":
            # 保留 \\url{ / \\href{ / \\path{ 命令令牌（自身需要命中规则），
            # 只屏蔽 URL 参数内容（其中的 % 不是注释起点）。
            open_rel = text[span.start : span.end].find("{")
            keep_end = span.start + open_rel + 1 if open_rel != -1 else span.start
            for i in range(keep_end, span.end):
                chars[i] = " "
        elif span.kind in ("verb", "verb_fancy", "mintinline"):
            # 保留 \\lstinline / \\Verb / \\mintinline 命令令牌（自身需要命中规则），
            # 只屏蔽逐字内容。
            cmd = _COMMAND_RE.match(text, span.start)
            keep_end = span.start + len(cmd.group(0)) if cmd else span.start
            for i in range(keep_end, span.end):
                chars[i] = " "
        else:
            for i in range(span.start, span.end):
                chars[i] = " "

    return _strip_comments("".join(chars))


def _strip_comments(text: str) -> str:
    """删除未转义的 % 注释（\\% 为字面百分号，不算注释起点）。"""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "%":
            backslashes = 0
            j = i - 1
            while j >= 0 and text[j] == "\\":
                backslashes += 1
                j -= 1
            if backslashes % 2 == 0:
                while i < n and text[i] != "\n":
                    i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _scan_column_specs(text: str) -> set[str]:
    """解析表格类环境的列说明符，返回所需宏包集合。"""
    found: set[str] = set()
    pos = 0
    while True:
        match = _BEGIN_ENV_RE.search(text, pos)
        if not match:
            break
        env = match.group(1)
        pos = match.end()
        if env not in COLSPEC_ENVS:
            continue

        i = pos
        # 跳过可选参数 [options]（如 \\begin{tabular}[t] / \\begin{tblr}[h]）
        if i < len(text) and text[i] == "[":
            end = text.find("]", i)
            if end == -1:
                break
            i = end + 1

        open_idx = text.find("{", i)
        if open_idx == -1:
            break
        close_idx = find_matching_brace(text, open_idx)
        if close_idx is None:
            break

        if env in WIDTH_FIRST_ENVS:
            # 第一个参数是宽度，列说明符是第二个花括号参数
            open_idx = text.find("{", close_idx + 1)
            if open_idx == -1:
                continue
            close_idx = find_matching_brace(text, open_idx)
            if close_idx is None:
                break

        found.update(_packages_from_colspec(text[open_idx + 1 : close_idx], env))
        pos = close_idx + 1

    return found


def _packages_from_colspec(spec: str, env: str) -> set[str]:
    """扫描单个列说明符字符串（花括号内内容不计入列类型）。"""
    found: set[str] = set()
    depth = 0
    i = 0
    n = len(spec)
    while i < n:
        ch = spec[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0:
            pkg = COLUMN_LETTER_PACKAGES.get(ch)
            if pkg and not (ch == "X" and env in X_NATIVE_ENVS):
                found.add(pkg)
            if i + 1 < n and spec[i + 1] == "{":
                prefix = spec[i] + "{"
                if prefix in COLUMN_PREFIX_PACKAGES:
                    found.add("array")
                    i += 1
                    continue
        i += 1
    return found
