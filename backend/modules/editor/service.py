r"""
编辑器整文件读写服务层（editor-plan §5.1，阶段一）。

职责：
    1. 打开读取：GET /api/project/file-content 的业务实现——路径经
       resolve_project_path() 白名单校验（逐组件符号链接检测内置），
       返回文件全文与当前 MD5
    2. 统一保存：POST /api/project/save-file 的业务实现——内容先做
       latex_json_guard 宽松损坏特征校验（不做括号/$ 配平检查，编辑中间态
       合法），再经 LatexProject.apply_whole_files 以 MD5 乐观锁落盘：
       单用户推入 BackupManager 内存撤销栈、多用户记入 workspace_edit_ops
       反向补丁快照，手动编辑因此与 AI 改动共享同一套 undo/redo 历史
       （决策 Q11）；写盘后复用 projects_service.enforce_write_quota 刷新
       项目用量记账，超限回滚语义与既有写路径一致（方案 §8.4）
    3. 二进制预览：GET /api/project/raw-file 的业务实现——路径经
       resolve_project_path() 可预览扩展名白名单校验后，以
       Content-Disposition: inline 的 FileResponse 返回原始字节，
       供编辑器页内嵌 PDF 与常见位图预览（只读，不进入缓冲区与撤销历史）
    4. 引用符号扫描：GET /api/project/latex-symbols 的业务实现
       （editor-productivity-plan §5.2 / 决策 Q7，阶段四）——全项目只读
       扫描 .tex 提取 \label（含行号）与 \cite key、.bib 经 bib_parser
       提取条目 key 与标题，供前端 \ref / \label / \cite 补全；零 LLM、
       零写盘、响应只含相对路径
    5. 一键格式化：POST /api/project/format-latex 的业务实现
       （editor-productivity-plan §5.2 / 决策 Q8，阶段五）——纯转换端点，
       接收缓冲区全文复用 latex_formatter（latexindent 优先、内置兜底，
       外部命令执行沿既有 SandboxRunner 规则）返回格式化结果；不落盘、
       不涉路径、零配额影响、零 LLM

约束：
    - 面向用户的错误消息一律使用项目内相对路径（多用户路径脱敏规则），
      完整路径只允许进服务端日志
    - 同步阻塞 I/O 与内容校验（最大约 2MB 文本的损坏特征扫描）一律经
      asyncio.to_thread 调度，不阻塞事件循环
    - 读写按字节保真：打开返回的 content 与 md5 对应同一份磁盘字节
      （单次 read_bytes，safe_read 的通用换行归一化会把 CRLF 改写为
      LF，此处不用），避免"打开即保存"把整个文件换行符静默翻转
    - 保存内容长度受 MAX_EDITABLE_CHARS 上限保护；配额回滚所需的写前
      全文由 apply_whole_files_detailed 在事务锁内捕获并随结果返回，
      服务层不再锁外预读（消除预读-落盘竞态窗口）
"""

import asyncio
import hashlib
import re
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from backend.modules.bib_manager.bib_parser import parse_bib_file

from backend.core.logger import get_logger
from backend.core.multiuser import edit_log
from backend.core.project_manager import FileConflictError
from backend.modules.projects import service as projects_service
from backend.utils.latex_formatter import format_latex_source
from backend.utils.latex_json_guard import check_latex_corruption
from backend.utils.path_guard import resolve_project_path

logger = get_logger(__name__)

# 可编辑文件扩展名白名单（editor-plan §2 / 决策 Q3；常量供路由层与服务层复用）
EDITABLE_SUFFIXES: tuple[str, ...] = (".tex", ".bib", ".sty", ".cls")

# 单次可保存内容的字符数上限：拦截误粘贴超大文本 / 恶意超大请求体在
# 内容校验与落盘前的资源消耗。save-file、编辑器助手缓冲区与 format-latex
# 纯转换端点共用同一量级（格式化内容与可编辑内容同源，均为编辑器缓冲区）。
MAX_EDITABLE_CHARS = 2_000_000

# 可预览二进制扩展名白名单（编辑器页内嵌只读预览；须与前端
# lib/editor/files.js 的 PREVIEWABLE_SUFFIXES 同步）。刻意不收：
#   - .svg：以 inline 提供用户上传的 SVG 存在存储型 XSS 风险；
#   - .eps：学术项目常见但浏览器无法原生渲染，需额外转换管线。
PREVIEWABLE_SUFFIXES: tuple[str, ...] = (
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
)

# 引用符号扫描扩展名白名单（latex-symbols 端点，editor-productivity-plan
# §5.2 阶段四）：.tex 提取 \label / \cite key，.bib 经 bib_parser 提取条目
SYMBOL_SCAN_SUFFIXES: tuple[str, ...] = (".tex", ".bib")

# \label{...}：标签 key 内不允许花括号（防跨标签误匹配）；逐行匹配无嵌套量词
_LABEL_RE = re.compile(r"\\label\{([^{}]+)\}")

# \cite 家族（natbib/biblatex：cite/citep/citet/parencite/textcite/...，
# 含星号变体与已敲出的可选参数 [p.~5]，可连续多个 [..]）；key 组内不允许
# 花括号，一个 \cite{a,b,c} 由服务层按逗号拆分
_CITE_RE = re.compile(r"\\[A-Za-z]*[cC]ite[A-Za-z]*\*?(?:\[[^\][{}]*\])*\{([^{}]*)\}")

# 单文件符号扫描的内容上限（与 MAX_EDITABLE_CHARS 同量级）：异常超大文件
# 跳过并记日志，防止个别畸形文件拖垮全项目扫描
MAX_SYMBOL_SCAN_CHARS = 2_000_000

# 可预览扩展名 → MIME 类型（FileResponse media_type 用）
_RAW_MEDIA_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def _http(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message})


# ── 打开读取 ──────────────────────────────────────────────────────────────
async def read_file_content(project, file_path: str) -> dict:
    """读取项目内可编辑文件的完整内容与当前 MD5（编辑器打开文件）。"""
    try:
        return await asyncio.to_thread(_read_file_sync, project, file_path)
    except FileNotFoundError as e:
        raise _http(404, "file_not_found", str(e)) from e
    except ValueError as e:
        raise _http(400, "invalid_path", str(e)) from e
    except RuntimeError as e:
        raise _http(400, "invalid_workspace", str(e)) from e


def _read_file_sync(project, file_path: str) -> dict:
    root = Path(project.root_path)
    full_path = resolve_project_path(
        root, file_path, allowed_suffixes=EDITABLE_SUFFIXES
    )
    if not full_path.is_file():
        # 错误消息使用相对路径（多用户脱敏）；完整路径只进服务端日志
        logger.info("Editor open miss: %s (root=%s)", file_path, root)
        raise FileNotFoundError(f"File not found: {file_path}")
    # 字节级保真单次读取（编辑器读写面专用，不用 safe_read）：
    # safe_read 走 read_text 的通用换行归一化（\r\n / \r → \n），会导致
    # 返回的 content 与 md5（按磁盘字节计算）不一致，且"打开即保存"会把
    # CRLF 文件整体改写为 LF。content 与 md5 必须对应同一份磁盘字节，
    # 因此只做一次 read_bytes，解码失败抛稳定错误码 invalid_encoding
    # （供前端翻译层消费），错误消息只含项目内相对路径。
    try:
        raw = full_path.read_bytes()
        content = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_encoding",
                "message": f"File {file_path} is not valid UTF-8. "
                "Only UTF-8 encoded files are supported.",
            },
        ) from e
    md5 = hashlib.md5(raw).hexdigest()  # 与 file_ops.compute_md5 同口径
    logger.info("Editor file opened: %s (%d chars)", file_path, len(content))
    return {"file_path": file_path, "content": content, "md5": md5}


# ── 统一保存 ──────────────────────────────────────────────────────────────
async def save_file_content(
    project,
    binding: dict,
    *,
    file_path: str,
    content: str,
    base_md5: str | None,
    actor_user_id: str | None,
) -> dict:
    """编辑器整文件保存（MD5 乐观锁 + 统一撤销记账 + 用量记账刷新）。

    base_md5 与文件当前 MD5 不符时返回 409 结构化错误（错误消息为相对
    路径）；base_md5 缺省时跳过乐观锁校验（与 apply_change 语义一致）。
    """
    # 长度上限：在内容校验与落盘前拦截超大请求体（含 project_router
    # SaveFileRequest 入口——校验统一收口在本服务方法入口处）
    if len(content) > MAX_EDITABLE_CHARS:
        raise _http(
            413,
            "content_too_long",
            f"Content is {len(content)} characters, exceeding the "
            f"maximum editable size of {MAX_EDITABLE_CHARS} characters",
        )

    # 内容校验：仅拦截控制字符损坏与 '[object Object]' 类垃圾占位符等
    # 损坏特征；不做括号/$ 配平检查——编辑中间态合法（§5.1 关键约束）。
    # 整文件文本的扫描是 CPU 密集操作，入线程池调度（约束见模块 docstring）
    corruption = await asyncio.to_thread(check_latex_corruption, content)
    if corruption:
        raise _http(400, "invalid_content", corruption)

    try:
        expected_md5s = {file_path: base_md5} if base_md5 else None
        # source_scope 显式标记写入来源：经 to_thread 调度后帧链推断不可用，
        # 记账诊断字段 source_module 固定为 "editor"。
        # before_contents 由事务在锁内捕获并随结果返回（R1：写前内容不再
        # 在锁外预读，杜绝配额回滚读到并发改后内容的竞态）；目标文件缺失
        # 的 FileNotFoundError 同样由锁内存在性检查抛出 → 404 语义不变。
        with edit_log.source_scope("editor"):
            result = await asyncio.to_thread(
                project.apply_whole_files_detailed,
                {file_path: content},
                expected_md5s=expected_md5s,
                allowed_suffixes=EDITABLE_SUFFIXES,
            )
    except FileConflictError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "file_conflict",
                "message": str(e),
                "file_path": e.file_path,
                "expected_md5": e.expected_md5,
                "current_md5": e.current_md5,
            },
        ) from e
    except FileNotFoundError as e:
        raise _http(404, "file_not_found", str(e)) from e
    except UnicodeDecodeError:
        # 目标文件现有内容非 UTF-8（事务锁内 prepare 阶段读取写前全文时
        # 检出）：稳定错误码 invalid_encoding；发生在任何写入之前。
        # 注意 UnicodeDecodeError 是 ValueError 子类，必须先于其分支捕获。
        raise _http(
            400,
            "invalid_encoding",
            f"File {file_path} is not valid UTF-8. "
            "Only UTF-8 encoded files are supported.",
        )
    except ValueError as e:
        raise _http(400, "invalid_path", str(e)) from e
    except RuntimeError as e:
        raise _http(400, "invalid_workspace", str(e)) from e

    # 多用户模式：刷新项目用量记账，超限按既有语义以锁内捕获的写前全文
    # 直接恢复并撤销本次记账 op（单用户模式内部直接跳过）
    await projects_service.enforce_write_quota(
        binding,
        project,
        file_path,
        before_content=result.before_contents.get(file_path),
        edit_op_id=result.edit_op_id,
        actor_user_id=actor_user_id,
    )

    logger.info(
        "Editor file saved: %s (%d chars, optimistic_lock=%s)",
        file_path,
        len(content),
        bool(base_md5),
    )
    return {"status": "ok", "file_path": file_path, "md5": result.md5s[file_path]}


# ── 二进制预览 ────────────────────────────────────────────────────────────
async def read_raw_file(project, file_path: str) -> FileResponse:
    """读取项目内可预览二进制文件（PDF 与常见位图），inline 返回原始字节。

    只读预览：不进入编辑器缓冲区、不产生撤销记账；路径校验与错误语义
    （invalid_path / file_not_found / invalid_workspace）与打开读取一致。
    """
    try:
        full_path = await asyncio.to_thread(_resolve_raw_file_sync, project, file_path)
    except FileNotFoundError as e:
        raise _http(404, "file_not_found", str(e)) from e
    except ValueError as e:
        raise _http(400, "invalid_path", str(e)) from e
    except RuntimeError as e:
        raise _http(400, "invalid_workspace", str(e)) from e

    logger.info("Editor raw file served: %s", file_path)
    # filename 只取 basename（非服务器路径，不违反多用户脱敏）；
    # content_disposition_type="inline" 使浏览器在 iframe 内联预览而非下载，
    # 与 git_diff 的 PDF 端点口径一致
    return FileResponse(
        path=full_path,
        media_type=_RAW_MEDIA_TYPES[full_path.suffix.lower()],
        filename=full_path.name,
        content_disposition_type="inline",
    )


def _resolve_raw_file_sync(project, file_path: str) -> Path:
    """校验可预览目标合法（白名单 + 符号链接检测），返回绝对路径。"""
    root = Path(project.root_path)
    full_path = resolve_project_path(
        root, file_path, allowed_suffixes=PREVIEWABLE_SUFFIXES
    )
    if not full_path.is_file():
        logger.info("Editor raw file miss: %s (root=%s)", file_path, root)
        raise FileNotFoundError(f"File not found: {file_path}")
    return full_path


# ── 引用符号扫描（editor-productivity-plan §5.2 / 决策 Q7，阶段四） ──────
def _strip_comment(line: str) -> str:
    """截掉一行中非转义 `%` 之后的内容（与前端 outlineScan.stripComment 同语义）。

    `%` 之前连续反斜杠个数为奇数视为转义百分号（`\\%` 字面量，非注释），
    偶数（含 0）则为注释起点——因此 `\\\\%`（换行命令后的真注释）正确截断。
    """
    for i, ch in enumerate(line):
        if ch != "%":
            continue
        slashes = 0
        j = i - 1
        while j >= 0 and line[j] == "\\":
            slashes += 1
            j -= 1
        if slashes % 2 == 0:
            return line[:i]
    return line


def _clean_bib_title(title: str) -> str:
    """bib 条目标题净化：去花括号定界符、折叠空白（保留转义命令原样）。"""
    text = title.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", text).strip()


def _collect_tex_symbols(
    content: str, rel_path: str, labels: list[dict], cited_keys: list[dict]
) -> None:
    """逐行提取单个 .tex 文件的 \\label（含行号）与 \\cite key（补全引用侧）。"""
    for line_no, raw_line in enumerate(content.splitlines(), 1):
        line = _strip_comment(raw_line)
        for m in _LABEL_RE.finditer(line):
            labels.append(
                {"key": m.group(1).strip(), "file_path": rel_path, "line": line_no}
            )
        for m in _CITE_RE.finditer(line):
            for key in m.group(1).split(","):
                key = key.strip()
                # \nocite{*} 通配（"引用全部 bib 条目"）不是真实 key，
                # 不进补全候选
                if key and key != "*":
                    cited_keys.append({"key": key, "file_path": rel_path})


def _collect_bib_keys(content: str, rel_path: str, entries: list[dict]) -> None:
    """经 bib_parser 提取单个 .bib 文件的条目 key 与标题（供 detail 展示）。"""
    for entry in parse_bib_file(content):
        key = (entry.get("cite_key") or "").strip()
        if not key:
            continue
        title = _clean_bib_title(entry.get("fields", {}).get("title", ""))
        entries.append({"key": key, "file_path": rel_path, "title": title})


def _scan_symbols_sync(project) -> dict:
    """全项目符号扫描（同步实现，由 asyncio.to_thread 调度）。

    只读扫描：遍历项目内 .tex / .bib（隐藏目录跳过），每个文件经
    resolve_project_path() 校验（扩展名白名单 + 逐组件符号链接检测，
    指向项目外的文件静默跳过）；响应只含项目内相对路径（多用户脱敏）。
    """
    root = Path(project.root_path)  # 未打开项目时 RuntimeError → invalid_workspace
    labels: list[dict] = []
    bib_entries: list[dict] = []
    cited_keys: list[dict] = []
    scanned = 0
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in SYMBOL_SCAN_SUFFIXES or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        # 隐藏目录（.git 等）下的文件不参与符号扫描
        if any(part.startswith(".") for part in Path(rel).parts):
            continue
        try:
            full_path = resolve_project_path(
                root, rel, allowed_suffixes=SYMBOL_SCAN_SUFFIXES
            )
        except ValueError as e:
            logger.debug("Symbol scan skip %s: %s", rel, e)
            continue
        # 超限预检在读入前按字节大小进行：UTF-8 字节数 ≥ 字符数，字节
        # 超限的文件必然也超字符上限，避免把 GB 级畸形文件整读进内存
        try:
            size = full_path.stat().st_size
        except OSError as e:
            logger.info("Symbol scan unreadable file %s: %s", rel, e)
            continue
        if size > MAX_SYMBOL_SCAN_CHARS:
            logger.info("Symbol scan skip oversized file %s (%d bytes)", rel, size)
            continue
        try:
            content = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.info("Symbol scan unreadable file %s: %s", rel, e)
            continue
        if full_path.suffix.lower() == ".bib":
            _collect_bib_keys(content, rel, bib_entries)
        else:
            _collect_tex_symbols(content, rel, labels, cited_keys)
        scanned += 1

    # cites：bib 条目优先（带标题）；.tex 中已 \cite 的 key 去重后补尾
    # （bib 缺失 / 条目未录入时仍可补全出已引用的 key）
    seen_bib_keys = {e["key"] for e in bib_entries}
    cites = list(bib_entries)
    seen_cites = set(seen_bib_keys)
    for item in cited_keys:
        if item["key"] in seen_cites:
            continue
        seen_cites.add(item["key"])
        cites.append({**item, "kind": "tex"})
    for entry in bib_entries:
        entry["kind"] = "bib"

    labels.sort(key=lambda x: (x["file_path"], x["line"], x["key"]))
    logger.info(
        "Symbol scan done: %d files, %d labels, %d cite keys",
        scanned,
        len(labels),
        len(cites),
    )
    return {"labels": labels, "cites": cites}


async def scan_project_symbols(project) -> dict:
    """全项目只读符号扫描（引用补全数据源，零 LLM、零写盘）。

    返回 {labels: [{key, file_path, line}], cites: [{key, file_path,
    kind, title?}]}；file_path 一律为项目内相对路径（多用户脱敏）。
    """
    try:
        return await asyncio.to_thread(_scan_symbols_sync, project)
    except RuntimeError as e:
        raise _http(400, "invalid_workspace", str(e)) from e


# ── 一键格式化（editor-productivity-plan §5.2 / 决策 Q8，阶段五） ──────
async def format_latex_buffer(project, content: str) -> dict:
    """编辑器一键格式化（纯转换端点，零写盘、零配额影响、零 LLM）。

    接收缓冲区全文，复用 latex_formatter 统一入口（latexindent 优先、
    内置轻量格式化器兜底，外部命令执行沿既有 SandboxRunner 规则与
    超时语义）返回格式化结果；不接收 file_path、不访问项目文件、
    不进入任何写盘 / 记账通道——落盘仍由前端走 save-file 单一路径。

    失败语义：latexindent 与内置兜底两层内部均"失败返回原样"（层内
    吞掉外部命令异常 / 超时，属 latex_formatter 既有契约，此时结果与
    原文一致、changed=False）；本服务层仅拦截意外异常，收敛为 500
    format_failed 稳定错误码（三语 errors 命名空间同步）。
    """
    # 工作区把关：纯转换不访问项目文件，仅复用 root_path 的打开状态
    # 检查（未打开项目 → RuntimeError → invalid_workspace，与
    # latex-symbols 同语义）
    try:
        Path(project.root_path)
    except RuntimeError as e:
        raise _http(400, "invalid_workspace", str(e)) from e

    # 大小上限：与 save-file / 编辑器打开同量级（约 2MB），在格式化
    # 调度前拦截超大请求体
    if len(content) > MAX_EDITABLE_CHARS:
        raise _http(
            413,
            "content_too_long",
            f"Content is {len(content)} characters, exceeding the "
            f"maximum editable size of {MAX_EDITABLE_CHARS} characters",
        )

    try:
        # format_latex_source 内部已按层调度线程池（探测/内置格式化走
        # to_thread、latexindent 经 SandboxRunner 异步执行），不阻塞事件循环
        formatted = await format_latex_source(content)
    except Exception as e:  # noqa: BLE001 — 收敛为稳定错误码，不透传内部细节
        logger.warning("Editor format-latex failed: %s", e)
        raise _http(
            500,
            "format_failed",
            "LaTeX formatting failed. Please retry or check the document.",
        ) from e

    changed = formatted != content
    logger.info(
        "Editor buffer formatted: %d chars -> %d chars (changed=%s)",
        len(content),
        len(formatted),
        changed,
    )
    return {"content": formatted, "changed": changed}
