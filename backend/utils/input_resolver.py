"""
LaTeX \\input / \\include / \\subfile 递归解析器。

职责：
    1. 从指定入口 .tex 文件出发，逐行扫描 \\input{}、\\include{}、\\subfile{} 引用
    2. 递归展开所有子文件，返回 DFS 序的文件路径列表
    3. 检测并跳过循环引用
    4. 处理行尾 % 注释和 \\section{} 紧邻 \\input{} 的场景

用法：
    from backend.utils.input_resolver import resolve_inputs
    files = resolve_inputs("main.tex", "/path/to/project")
    # → ["main.tex", "sections/abstract.tex", "sections/intro.tex", ...]
"""

import re
from pathlib import Path


from backend.core.logger import get_logger
from backend.utils.path_guard import resolve_project_path

_logger = get_logger("input_resolver")

# 匹配 \input{...}、\include{...}、\subfile{...}
_INPUT_CMD_PATTERN = re.compile(
    r"\\(?:input|include|subfile)\{([^}]+)\}"
)


def _strip_line_comment(line: str) -> str:
    """
    去除行尾 % 注释，保留 \\% 转义。

    逐字符扫描，遇到未转义的 % 即截断。
    """
    result: list[str] = []
    i = 0
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line) and line[i + 1] == "%":
            result.append("\\%")
            i += 2
        elif line[i] == "%":
            break
        else:
            result.append(line[i])
            i += 1
    return "".join(result)


def _find_input_refs(content: str) -> list[str]:
    """
    逐行扫描 LaTeX 内容，提取所有 \\input/\\include/\\subfile 引用。

    处理流程：
        1. 跳过 % 开头的纯注释行
        2. 对每行先去除行尾注释
        3. 在清理后的行中匹配 \\input{} 等命令

    返回：
        引用路径列表（原始字符串，未解析绝对路径）
    """
    refs: list[str] = []
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("%"):
            continue
        cleaned = _strip_line_comment(stripped)
        for match in _INPUT_CMD_PATTERN.finditer(cleaned):
            ref = match.group(1).strip()
            refs.append(ref)
    return refs


def _normalize_ref(ref: str, current_dir: str) -> str:
    """
    将引用路径规范化为相对于项目根目录的路径。

    参数：
        ref: 原始引用（如 "abstract"、"sections/intro"、"./sections/intro.tex"）
        current_dir: 当前文件所在目录（相对于项目根）

    返回：
        规范化后的相对路径（带 .tex 后缀）
    """
    if not ref.endswith(".tex"):
        ref += ".tex"

    if current_dir:
        ref_path = Path(current_dir) / ref
    else:
        ref_path = Path(ref)

    return str(ref_path)


def resolve_inputs(
    file_path: str,
    root_path: str,
    _visited: set[str] | None = None,
) -> list[str]:
    """
    递归解析 LaTeX 文件中的 \\input / \\include / \\subfile 引用。

    采用 DFS 顺序遍历，入口文件始终为列表首元素。
    检测循环引用并跳过（记录日志）。

    参数：
        file_path: 入口文件路径（相对于项目根目录）
        root_path: 项目根目录绝对路径
        _visited: 内部递归用，已访问文件集合

    返回：
        文件路径列表（相对于项目根目录），DFS 序，已去重。
        例如: ["main.tex", "sections/abstract.tex", "sections/intro.tex"]
    """
    if _visited is None:
        _visited = set()

    root = Path(root_path)

    try:
        resolved_file = resolve_project_path(root, file_path)
    except ValueError as e:
        _logger.warning(f"Path traversal rejected for entry file: {file_path} — {e}")
        return []

    full_path = resolved_file

    # 使用 resolved 路径进行循环检测
    abs_path = str(full_path)

    if abs_path in _visited:
        _logger.debug(f"Circular reference detected, skipping: {file_path}")
        return []
    _visited.add(abs_path)

    if not full_path.exists():
        _logger.warning(f"Referenced file not found: {full_path}")
        return []

    try:
        content = full_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        _logger.warning(f"Cannot read file {full_path}: {e}")
        return []

    refs = _find_input_refs(content)
    current_dir = str(Path(file_path).parent)

    result = [file_path]
    for ref in refs:
        ref_path = _normalize_ref(ref, current_dir)
        try:
            resolve_project_path(root, ref_path)
        except ValueError as e:
            _logger.warning(f"Path traversal rejected in \\input: {ref} — {e}")
            continue
        sub_files = resolve_inputs(ref_path, root_path, _visited)
        result.extend(sub_files)

    return result
