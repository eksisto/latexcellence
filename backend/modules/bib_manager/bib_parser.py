"""
轻量级 BibTeX 解析器。

将 .bib 文件内容解析为结构化条目列表，支持嵌套大括号和引号字段。
"""

import re


class BibParserError(ValueError):
    pass


def parse_bib_file(content: str) -> list[dict]:
    """解析 .bib 文件内容，返回条目列表。"""
    entries = []
    i = 0
    n = len(content)

    while i < n:
        if content[i] == "@":
            entry = _parse_one_entry(content, i)
            if entry:
                entries.append(entry)
                i = entry["_end"]
            else:
                i += 1
        else:
            i += 1

    return entries


def _parse_one_entry(content: str, start: int) -> dict | None:
    """从 start 位置解析一个 @type{key,...} 条目。"""
    end = start

    # 跳过 @
    if content[end] != "@":
        return None

    # 读取 entry_type
    type_start = end + 1
    type_end = content.find("{", type_start)
    if type_end == -1:
        return None
    entry_type = content[type_start:type_end].strip().lower()

    # 读取 cite_key（到逗号或右花括号，排除内部花括号）
    key_start = type_end + 1
    if key_start >= len(content):
        return None
    key_end = _find_key_end(content, key_start)
    if key_end == -1:
        return None
    cite_key = content[key_start:key_end].strip().rstrip(",").strip()

    # 找条目结束（括号匹配）
    body_start = key_end
    body_end = _find_balanced_end(content, body_start)
    if body_end == -1:
        return None

    raw_bibtex = content[start:body_end].strip()
    fields = _parse_fields(content, body_start, body_end)

    return {
        "cite_key": cite_key,
        "entry_type": entry_type,
        "fields": fields,
        "raw_bibtex": raw_bibtex,
        "_end": body_end,
    }


def _find_key_end(content: str, start: int) -> int:
    """找到 cite_key 的结束位置（第一个不在花括号内的逗号或右花括号）。"""
    depth = 0
    for i in range(start, len(content)):
        ch = content[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
            else:
                return i
        elif ch == "," and depth == 0:
            return i
    return -1


def _find_balanced_end(content: str, start: int) -> int:
    """从 start 开始，找到花括号平衡的结束位置 + 1。"""
    depth = 1  # 已经经过第一个 {
    for i in range(start + 1, len(content)):
        ch = content[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        elif ch == '"':
            i = _skip_quoted(content, i)
            if i == -1:
                return -1
    return -1


def extract_cite_key(line: str) -> str:
    """从 @type{key,... 行中提取 cite_key。"""
    open_brace = line.find("{")
    if open_brace < 0:
        return ""
    rest = line[open_brace + 1 :]
    comma = rest.find(",")
    brace = rest.find("}")
    if comma >= 0 and (brace < 0 or comma < brace):
        return rest[:comma].strip()
    if brace >= 0:
        return rest[:brace].strip()
    return rest.strip()


def _skip_quoted(content: str, start: int) -> int:
    """跳过引号字符串，返回结束引号之后的索引，-1 表示未闭合。"""
    i = start + 1
    while i < len(content):
        if content[i] == '"' and content[i - 1] != "\\":
            return i
        i += 1
    return -1


def _parse_fields(content: str, body_start: int, body_end: int) -> dict[str, str]:
    """从条目体中提取 field = {value} 或 field = "value"。"""
    body = content[body_start : body_end - 1]
    fields: dict[str, str] = {}

    # 跳过 cite_key 和逗号
    key_end = body.find(",")
    if key_end == -1:
        return fields
    body = body[key_end + 1 :]

    i = 0
    while i < len(body):
        # 跳过空白和逗号
        if body[i] in (" ", "\t", "\n", "\r", ","):
            i += 1
            continue

        # 找等号前面的字段名
        eq = body.find("=", i)
        if eq == -1:
            break
        field_name = body[i:eq].strip().lower()

        # 找等号后面的值开始
        j = eq + 1
        while j < len(body) and body[j] in (" ", "\t", "\n", "\r"):
            j += 1
        if j >= len(body):
            break

        # 提取值
        if body[j] == "{":
            val_start = j + 1
            val_start, val_end, next_i = _find_balanced_value(body, j)
            if val_end == -1:
                break
            value = body[val_start:val_end]
            i = next_i
        elif body[j] == '"':
            val_start = j + 1
            q_end = _skip_quoted(body, j)
            if q_end == -1:
                break
            value = body[val_start:q_end]
            i = q_end + 1
        else:
            # 无引号简单值（如年份数字）
            val_start = j
            space_or_comma = len(body)
            for sep in (",", "\n", "}"):
                p = body.find(sep, val_start)
                if p != -1 and p < space_or_comma:
                    space_or_comma = p
            value = body[val_start:space_or_comma].strip()
            i = space_or_comma

        fields[field_name] = value

    return fields


def _find_balanced_value(content: str, brace_pos: int) -> tuple[int, int, int]:
    """在 brace_pos 处找到花括号平衡的右花括号，返回 (值起始, 值结束, 下一个位置)。"""
    depth = 1
    val_start = brace_pos + 1
    i = val_start
    while i < len(content):
        ch = content[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return val_start, i, i + 1
        i += 1
    return -1, -1, -1


def remove_entry_from_content(content: str, cite_key: str) -> str:
    """从 .bib 内容中移除指定 cite_key 的条目。"""
    in_entry = False
    brace_count = 0
    lines = content.split("\n")
    result = []
    removed = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not in_entry:
            if stripped.startswith("@"):
                key = extract_cite_key(stripped)
                if key == cite_key and not removed:
                    in_entry = True
                    brace_count = line.count("{") - line.count("}")
                    removed = True
                    continue
            result.append(line)
        else:
            brace_count += line.count("{") - line.count("}")
            if brace_count <= 0:
                in_entry = False
                continue

    if not removed:
        raise BibParserError(f"Entry not found: {cite_key}")

    # 清理条目前后多余空行
    text = "\n".join(result)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def normalize_title(title: str) -> str:
    """规范化标题用于相似度比较：去 LaTeX 命令、标点、空格，转小写。"""
    text = re.sub(
        r"\\(?:textbf|textit|emph|textsc|texttt|underline|textsf|textrm|text)\{([^}]*)\}",
        r"\1",
        title,
    )
    text = re.sub(r"\\\w+\{([^}]*)\}", r"\1", text)
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def normalize_doi(doi: str) -> str:
    """规范化 DOI：去前缀、去空格、小写。"""
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi
