"""difflib / latexdiff 差异文档生成。"""

import asyncio
import difflib
import tempfile
from pathlib import Path

from backend.core.logger import get_logger

from .constants import (
    _BEGIN_DOCUMENT,
    _DEFAULT_COLOR_ADD_HEX,
    _DEFAULT_COLOR_DEL_HEX,
    _DIFF_PREAMBLE_TEMPLATE,
    _END_DOCUMENT,
    _ENV_TOKEN_RE,
    _FLOAT_ENV_NAMES,
)
from .models import DiffDocument, ProcessResult
from .process import _run_process, redact_command_output_for_user

logger = get_logger(__name__)


def _resolve_color_hex(color: str, fallback: str) -> str:
    """将用户颜色输入（HEX #xxx / #xxxxxx 或直接 hex）统一为 6 位大写 HEX。"""
    if not color:
        return fallback
    c = color.strip().lstrip("#")
    if len(c) == 3:
        c = c[0] * 2 + c[1] * 2 + c[2] * 2
    if len(c) != 6:
        return fallback
    try:
        int(c, 16)
    except ValueError:
        return fallback
    return c.upper()


def _build_diff_preamble(color_add: str = "", color_del: str = "") -> str:
    """用指定颜色构建差异标记 LaTeX preamble。"""
    add_hex = _resolve_color_hex(color_add, _DEFAULT_COLOR_ADD_HEX)
    del_hex = _resolve_color_hex(color_del, _DEFAULT_COLOR_DEL_HEX)
    return _DIFF_PREAMBLE_TEMPLATE.format(color_add=add_hex, color_del=del_hex)


def _strip_blank_lines(content: str) -> str:
    """删除 LaTeX 内容中的所有空白行。

    latexdiff 在差异合并时会向受限上下文（数学模式、\\lstdefinelanguage、
    \\textbf{} 等 braced argument）内插入空白行。空白行在 TeX 中等价于
    ``\\par``，而在受限上下文中 ``\\par`` 是非法的，会导致
    ``Paragraph ended before...`` 等编译错误。

    删除全部空白行可彻底消除此类问题，同时保留所有 diff 标记
    （\\DIFadd / \\DIFdel）和文档结构。段落间距会略有变化，
    但对差异预览 PDF 的可读性影响极小。
    """
    return "\n".join(line for line in content.splitlines() if line.strip() != "")


async def _build_latexdiff_document(
    base_content: str,
    target_content: str,
    *,
    markup_type: str = "UNDERLINE",
    math_markup: str = "fine",
    exclude_safecmd: str = "",
    exclude_textcmd: str = "",
) -> tuple[str, int, int]:
    """使用 latexdiff 引擎生成语义级差异文档。

    Returns:
        (diff_content, added_lines, deleted_lines)
    """
    with tempfile.TemporaryDirectory(prefix="latexcellence_latexdiff_") as tmp_dir:
        tmp = Path(tmp_dir)
        old_file = tmp / "old.tex"
        new_file = tmp / "new.tex"
        # 全量差异文档写盘可达 MB 级，入线程池
        await asyncio.to_thread(old_file.write_text, base_content, encoding="utf-8")
        await asyncio.to_thread(new_file.write_text, target_content, encoding="utf-8")

        try:
            result = await _run_latexdiff(
                old_file,
                new_file,
                markup_type=markup_type,
                math_markup=math_markup,
                exclude_safecmd=exclude_safecmd,
                exclude_textcmd=exclude_textcmd,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"latexdiff command not found. Please confirm that TeX Live is installed and that latexdiff is in your PATH: {exc}"
            ) from exc

        if result.returncode != 0:
            stderr = result.stderr.strip()
            msg = (
                stderr
                or "latexdiff exited with a non-zero exit code, but no error output was produced"
            )
            if not result.stdout.strip():
                # tmp 目录即本次命令的工作目录：其绝对路径（含随机后缀）
                # 不向用户暴露，多用户模式下经统一脱敏口改写
                raise RuntimeError(
                    f"latexdiff failed: {redact_command_output_for_user(msg, tmp)}"
                )
            logger.warning(
                f"latexdiff exited with a non-zero exit code, but there is still some output, which may be incomplete: {msg}"
            )

        diff_content = _strip_blank_lines(result.stdout)
        added, deleted = _count_latexdiff_changes(diff_content)
        return diff_content, added, deleted


def _build_diff_document(
    base_content: str,
    target_content: str,
    color_add: str = "",
    color_del: str = "",
) -> DiffDocument:
    target_preamble, _target_begin, target_body, target_end, target_tail = (
        _split_document(target_content)
    )
    _base_preamble, _base_begin, base_body, _base_end, _base_tail = _split_document(
        base_content
    )

    body_lines, added_lines, deleted_lines = _diff_lines(
        base_body.splitlines(),
        target_body.splitlines(),
    )

    preamble = _inject_diff_preamble(target_preamble, color_add, color_del)
    end_document = target_end or _END_DOCUMENT
    content = (
        "\n".join(
            [
                preamble.rstrip(),
                _BEGIN_DOCUMENT,
                *body_lines,
                end_document,
                target_tail.lstrip("\n"),
            ]
        ).rstrip()
        + "\n"
    )

    return DiffDocument(
        content=content,
        added_lines=added_lines,
        deleted_lines=deleted_lines,
    )


def _split_document(content: str) -> tuple[str, str, str, str, str]:
    begin_index = _find_document_boundary(content, _BEGIN_DOCUMENT, find_last=False)
    if begin_index < 0:
        return "", "", content, "", ""

    begin_end = begin_index + len(_BEGIN_DOCUMENT)
    end_index = _find_document_boundary(content, _END_DOCUMENT, find_last=True)
    if end_index > begin_end:
        return (
            content[:begin_index],
            content[begin_index:begin_end],
            content[begin_end:end_index],
            content[end_index : end_index + len(_END_DOCUMENT)],
            content[end_index + len(_END_DOCUMENT) :],
        )

    return (
        content[:begin_index],
        content[begin_index:begin_end],
        content[begin_end:],
        "",
        "",
    )


def _find_document_boundary(content: str, boundary: str, find_last: bool) -> int:
    """在 content 中查找 boundary 的位置，跳过注释行内的匹配。

    LaTeX 注释以 ``%`` 开始到行尾，其中出现的 ``\\begin{document}`` 或
    ``\\end{document}`` 不应被识别为文档边界。
    """

    offset = 0
    found = -1
    for line in content.split("\n"):
        unescaped = line.replace(r"\%", "")
        comment_pos = unescaped.find("%")
        search_region = line if comment_pos < 0 else line[:comment_pos]

        search_start = 0
        while True:
            pos = search_region.find(boundary, search_start)
            if pos < 0:
                break
            found = offset + pos
            if not find_last:
                return found
            search_start = pos + 1

        offset += len(line) + 1

    return found


def _diff_lines(
    base_lines: list[str], target_lines: list[str]
) -> tuple[list[str], int, int]:
    matcher = difflib.SequenceMatcher(None, base_lines, target_lines, autojunk=False)
    output = []
    added_lines = 0
    deleted_lines = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            output.extend(target_lines[j1:j2])
            continue
        if tag in ("delete", "replace"):
            deleted = base_lines[i1:i2]
            output.extend(_wrap_diff_block("LXDiffDeleted", deleted))
            deleted_lines += len(deleted)
        if tag in ("insert", "replace"):
            added = target_lines[j1:j2]
            output.extend(_wrap_diff_block("LXDiffAdded", added))
            added_lines += len(added)

    return output, added_lines, deleted_lines


def _wrap_diff_block(environment: str, lines: list[str]) -> list[str]:
    """将差异行包裹为 LXDiffAdded / LXDiffDeleted 高亮块。

    framed 颜色框不允许浮动体（table/figure 等）出现，否则编译报
    "Not in outer par mode"。因此遇到浮动体环境时，会把高亮块从
    浮动体前后拆开：浮动体本身（含其全部内容）保持不包裹，
    前后的普通文本仍正常高亮。浮动体必须位于独立段落中（前后留空行），
    否则紧贴 framed 框时 TeX 会报 "Float(s) lost"。
    """

    if not lines:
        return []
    content = lines if any(line.strip() for line in lines) else ["~"]

    output: list[str] = []
    buffer: list[str] = []
    float_stack: list[str] = []

    def in_float() -> bool:
        return bool(float_stack)

    def flush_buffer() -> None:
        if not buffer:
            return
        output.extend([f"\\begin{{{environment}}}", *buffer, f"\\end{{{environment}}}"])
        buffer.clear()

    def ensure_blank_separator() -> None:
        if output and output[-1] != "":
            output.append("")

    for line in content:
        if not line.strip():
            # 空白行原样保留（包裹内是段落分隔；浮动体内保持原样）
            if in_float():
                output.append(line)
            else:
                buffer.append(line)
            continue

        for fragment in _ENV_TOKEN_RE.split(line):
            if not fragment:
                continue
            if not _ENV_TOKEN_RE.fullmatch(fragment):
                # 普通文本：浮动体内直接输出，否则先暂存待包裹
                if in_float():
                    output.append(fragment)
                else:
                    buffer.append(fragment)
                continue

            token = fragment
            if token.startswith("\\begin"):
                name = token[len("\\begin{") : -1]
                if name in _FLOAT_ENV_NAMES:
                    if not in_float():
                        flush_buffer()
                        ensure_blank_separator()
                    float_stack.append(name)
                elif in_float():
                    # 浮动体内部的环境（如 tabular）随浮动体一起不包裹
                    pass
                else:
                    buffer.append(token)
                    continue
                output.append(token)
                continue

            # \end{...}
            name = token[len("\\end{") : -1]
            if name in _FLOAT_ENV_NAMES:
                if float_stack and float_stack[-1] == name:
                    float_stack.pop()
                elif name in float_stack:
                    float_stack.remove(name)
                output.append(token)
                if not in_float():
                    # 浮动体结束后回到普通段落，与后续包裹之间留空行
                    flush_buffer()
                    output.append("")
            elif in_float():
                output.append(token)
            else:
                buffer.append(token)

    flush_buffer()
    return output


def _inject_diff_preamble(
    preamble: str,
    color_add: str = "",
    color_del: str = "",
) -> str:
    diff_preamble = _build_diff_preamble(color_add, color_del)
    return preamble.rstrip() + "\n" + diff_preamble.strip() + "\n"


async def _run_latexdiff(
    old_path: Path,
    new_path: Path,
    markup_type: str = "UNDERLINE",
    math_markup: str = "fine",
    exclude_safecmd: str = "",
    exclude_textcmd: str = "",
) -> ProcessResult:
    """调用 latexdiff 生成语义级差异文档。"""
    cmd = [
        "latexdiff",
        f"--type={markup_type}",
        f"--math-markup={math_markup}",
        "--allow-spaces",
    ]
    if exclude_safecmd:
        cmd.append(f"--exclude-safecmd={exclude_safecmd}")
    if exclude_textcmd:
        cmd.append(f"--exclude-textcmd={exclude_textcmd}")
    cmd.extend([str(old_path), str(new_path)])
    return await _run_process(cmd, cwd=old_path.parent, timeout=120)


def _count_latexdiff_changes(diff_content: str) -> tuple[int, int]:
    """统计 latexdiff 输出中的新增和删除行数。

    latexdiff 用 ``\\DIFdel`` / ``\\DIFadd`` 等命令标记变更，
    这里按行统计含这些标记的行数。
    """
    added = 0
    deleted = 0
    for line in diff_content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if r"\DIFdel" in stripped or r"\DIFDELTAMARK" in stripped:
            deleted += 1
        if r"\DIFadd" in stripped:
            added += 1
    return added, deleted
