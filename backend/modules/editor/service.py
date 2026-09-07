r"""
编辑器整文件读写服务层（editor-design §5.1，阶段一）。

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
       （editor-productivity-design §5.2 / 决策 Q7，阶段四）——全项目只读
       扫描 .tex 提取 \label（含行号）与 \cite key、.bib 经 bib_parser
       提取条目 key 与标题，供前端 \ref / \label / \cite 补全；零 LLM、
       零写盘、响应只含相对路径
    5. 一键格式化：POST /api/project/format-latex 的业务实现
       （editor-productivity-design §5.2 / 决策 Q8，阶段五）——纯转换端点，
       接收缓冲区全文复用 latex_formatter（latexindent 优先、内置兜底，
       外部命令执行沿既有 SandboxRunner 规则）返回格式化结果；不落盘、
       不涉路径、零配额影响、零 LLM
    6. 跨文件大纲：GET /api/project/outline 的业务实现
       （editor-experience-design §4.4 / 决策 Q10，阶段四）——全项目 .tex
       分节扫描（与前端 outlineScan.js 同正则口径），项目级缓存 + 保存
       失效；零 LLM、零写盘、响应只含相对路径
    7. 文件树新建/删除：POST /api/project/create-file 与
       POST /api/project/delete-file 的业务实现——新建走可编辑白名单
       后缀校验，删除放开为任意非隐藏后缀（主文档受删除保护，
       editor-file-upload-design §4.4）；冲突/父目录检查 + 多用户模式
       创建前按新文件字节做配额预检、删除后刷新用量记账（配额随之
       释放）；响应携带重扫后的 file_tree 供前端单次往返刷新。刻意不
       纳入统一撤销记账（edit_log 恢复通道只支持改写已存在文件，不
       支持文件级增删），误删由前端确认弹窗兜底
    8. 文件树上传：POST /api/project/upload-file 的业务实现
       （editor-file-upload-design §4）——multipart 任意格式上传到项目
       内任意相对路径：20MB 流式大小上限（超限即中止）→ 文件名消毒 →
       隐藏组件拒绝 → resolve_project_path（不限后缀）→ 冲突检查 →
       多用户配额预检 → 同目录临时文件 + Path.replace 原子落盘 →
       多用户用量刷新（§4.1 校验链）。零 LLM、不进撤销记账；响应携带
       重扫后的 file_tree

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
import os
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend.core.logger import get_logger
from backend.core.multiuser import edit_log
from backend.core.multiuser.quota import (
    enforce_project_quota,
    refresh_owner_usage_after_write,
)
from backend.core.project_manager import FileConflictError
from backend.modules.bib_manager.bib_parser import parse_bib_file
from backend.modules.projects import service as projects_service
from backend.utils.latex_formatter import format_latex_source
from backend.utils.latex_json_guard import check_latex_corruption
from backend.utils.path_guard import resolve_project_path

logger = get_logger(__name__)

# 可编辑文件扩展名白名单（editor-design §2 / 决策 Q3；常量供路由层与服务层复用）
EDITABLE_SUFFIXES: tuple[str, ...] = (".tex", ".bib", ".sty", ".cls")

# 单次可保存内容的字符数上限：拦截误粘贴超大文本 / 恶意超大请求体在
# 内容校验与落盘前的资源消耗。save-file、编辑器助手缓冲区与 format-latex
# 纯转换端点共用同一量级（格式化内容与可编辑内容同源，均为编辑器缓冲区）。
MAX_EDITABLE_CHARS = 2_000_000

# 单文件上传字节上限（editor-file-upload-design §4.2 / 决策 D6）：任意格式
# 上传的唯一大小约束——文本类上传不受 MAX_EDITABLE_CHARS 约束（那是编辑
# 缓冲区/保存路径的约束）。20MB 后端常量、不引入配置项；多用户模式另有
# 项目配额预检兜底。
MAX_UPLOAD_FILE_BYTES = 20 * 1024 * 1024

# multipart 流式分块拷贝的块大小：逐块落盘避免大文件整读进内存
_UPLOAD_CHUNK_BYTES = 1024 * 1024

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

# 引用符号扫描扩展名白名单（latex-symbols 端点，editor-productivity-design
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
    # 跨文件大纲缓存保存失效（editor v3 §4.4 / 决策 Q10）：保存可能新增 /
    # 删除分节命令，下次 outline 请求全量重扫
    invalidate_project_outline_cache(project)
    return {"status": "ok", "file_path": file_path, "md5": result.md5s[file_path]}


# ── 文件树新建 / 删除 ────────────────────────────────────────────────────
def serialize_file_tree(nodes) -> list[dict]:
    """FileNode 树 → JSON 兼容嵌套结构（name/path/is_dir/children 递归）。"""
    result: list[dict] = []
    for node in nodes:
        item: dict = {"name": node.name, "path": node.path, "is_dir": node.is_dir}
        if node.children:
            item["children"] = serialize_file_tree(node.children)
        result.append(item)
    return result


def _binding_owner(binding: dict) -> tuple[str | None, str | None]:
    """多用户绑定取 (owner_id, project_id)；单用户模式返回 (None, None)。"""
    if not binding or not binding.get("multi_user"):
        return None, None
    return binding.get("owner_id"), binding.get("project_id")


def _refreshed_tree(project) -> list[dict]:
    """重扫项目目录并返回序列化文件树（就地更新 open 时的缓存）。"""
    return serialize_file_tree(project.refresh_file_tree())


async def create_project_file(
    project,
    binding: dict,
    *,
    file_path: str,
    content: str,
) -> dict:
    """在项目内新建文件（文件树操作，零 LLM、不进撤销记账）。

    路径经 resolve_project_path 白名单校验（逐组件符号链接检测内置）；
    目标已存在（含目录）报 409 file_exists，父目录缺失报 400
    parent_not_found。多用户模式属"已知增量"写路径：落盘前按新文件
    字节数做项目配额预检（超限 413 quota_exceeded），落盘后刷新用量
    记账。响应携带重扫后的 file_tree，前端单次往返完成文件树刷新。
    """
    if len(content) > MAX_EDITABLE_CHARS:
        raise _http(
            413,
            "content_too_long",
            f"Content is {len(content)} characters, exceeding the "
            f"maximum editable size of {MAX_EDITABLE_CHARS} characters",
        )
    corruption = await asyncio.to_thread(check_latex_corruption, content)
    if corruption:
        raise _http(400, "invalid_content", corruption)

    # 多用户配额预检（先于落盘；HTTPException 原样透传）
    owner_id, project_id = _binding_owner(binding)
    if owner_id and project_id:
        await asyncio.to_thread(
            enforce_project_quota, owner_id, len(content.encode("utf-8"))
        )

    try:
        created = await asyncio.to_thread(
            _create_file_sync, project, file_path, content
        )
    except FileExistsError as e:
        raise _http(409, "file_exists", str(e)) from e
    except FileNotFoundError as e:
        raise _http(400, "parent_not_found", str(e)) from e
    except ValueError as e:
        raise _http(400, "invalid_path", str(e)) from e
    except RuntimeError as e:
        raise _http(400, "invalid_workspace", str(e)) from e

    if project_id:
        await asyncio.to_thread(refresh_owner_usage_after_write, project_id)
    invalidate_project_outline_cache(project)
    logger.info("Editor file created: %s (%d chars)", file_path, len(content))
    return {
        "status": "ok",
        "file_path": created,
        "file_tree": _refreshed_tree(project),
    }


def _create_file_sync(project, file_path: str, content: str) -> str:
    """同步新建：路径校验 → 冲突/父目录检查 → 字节保真写盘。"""
    root = Path(project.root_path)
    full_path = resolve_project_path(
        root, file_path, allowed_suffixes=EDITABLE_SUFFIXES
    )
    if full_path.exists():
        # 错误消息使用相对路径（多用户脱敏）；完整路径只进服务端日志
        logger.info("Editor create conflict: %s (root=%s)", file_path, root)
        raise FileExistsError(f"File already exists: {file_path}")
    if not full_path.parent.is_dir():
        raise FileNotFoundError(f"Parent directory does not exist: {file_path}")
    full_path.write_bytes(content.encode("utf-8"))
    return file_path


async def delete_project_file(project, binding: dict, *, file_path: str) -> dict:
    """删除项目内文件（文件树操作，零 LLM、不进撤销记账）。

    任意非隐藏后缀均可删除（editor-file-upload-design §4.4：任意格式可传
    则必然要可删；隐藏组件被服务端权威拒绝，不依赖"不进文件树、无删除
    入口"的树层排除）；主文档受保护（400 main_file_protected，需先
    set-main-file 更换）。多用户模式删除后刷新用量记账（配额按磁盘重算
    随之释放）。响应携带重扫后的 file_tree。
    """
    try:
        main_file = await asyncio.to_thread(project.get_main_file)
    except RuntimeError as e:
        raise _http(400, "invalid_workspace", str(e)) from e
    if main_file and main_file == file_path:
        raise _http(
            400,
            "main_file_protected",
            "Cannot delete the main file; set another main file first",
        )

    try:
        await asyncio.to_thread(_delete_file_sync, project, file_path)
    except FileNotFoundError as e:
        raise _http(404, "file_not_found", str(e)) from e
    except ValueError as e:
        raise _http(400, "invalid_path", str(e)) from e
    except RuntimeError as e:
        raise _http(400, "invalid_workspace", str(e)) from e

    _, project_id = _binding_owner(binding)
    if project_id:
        await asyncio.to_thread(refresh_owner_usage_after_write, project_id)
    invalidate_project_outline_cache(project)
    logger.info("Editor file deleted: %s", file_path)
    return {
        "status": "ok",
        "file_path": file_path,
        "file_tree": _refreshed_tree(project),
    }


def _delete_file_sync(project, file_path: str) -> None:
    """同步删除：隐藏组件拒绝（§4.4「非隐藏」约束）→ 路径校验（不限
    后缀）→ 存在性检查 → unlink。"""
    root = Path(project.root_path)
    _reject_hidden_components(file_path)
    full_path = resolve_project_path(root, file_path)
    if not full_path.is_file():
        logger.info("Editor delete miss: %s (root=%s)", file_path, root)
        raise FileNotFoundError(f"File not found: {file_path}")
    full_path.unlink()


# ── 文件树上传（editor-file-upload-design §4） ────────────────────────────
def _validate_upload_target_name(file_path: str) -> None:
    r"""上传目标文件名消毒（editor-file-upload-design §3-6）：拒绝空名、
    控制字符与路径分隔符；超长文件名由 OS 层兜底报错。

    `\` 按 Windows 路径分隔符走私一并拒绝（POSIX 上虽是合法文件名字符，
    但跨平台目标语义不成立）；尾分隔符 / 纯点段（`Path()` 解析后会剥离，
    `Path.name` 为空）视为空文件名拒绝，避免落到目录语义上产生误导性
    的静默改写目标。
    """
    if not file_path or not file_path.strip():
        raise ValueError("file_path must not be empty")
    if "\\" in file_path:
        raise ValueError(
            f"Target file name must not contain path separators: {file_path}"
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in file_path):
        raise ValueError(
            f"Target file name must not contain control characters: {file_path}"
        )
    if file_path.endswith("/") or not Path(file_path).name:
        raise ValueError(f"Invalid target file name: {file_path}")


def _reject_hidden_components(file_path: str) -> None:
    """隐藏路径拒绝（editor-file-upload-design §3-3）：目标 file_path 任一
    组件以点开头即拒绝——覆盖 `.git/`、`.latexcellence/` 等隐藏目录与点
    开头的文件名；服务端权威校验，不依赖客户端同名消毒预检。

    path_guard 不做隐藏组件判定（`.latexcellence/main_file` 等内部机制
    自身要写隐藏路径），因此该策略由编辑器文件操作层在上传（§3-3）与
    删除（§4.4 / 决策 D9「任意非隐藏后缀」）两条入口各自强制执行。
    """
    if any(part.startswith(".") for part in Path(file_path).parts):
        raise ValueError(f"Hidden files and directories are not allowed: {file_path}")


def _stream_upload_to_temp(upload_file: UploadFile) -> tuple[Path, int]:
    """multipart 上传体流式分块落系统临时文件（editor-file-upload-design
    §4.1 校验链第 1 步 / §4.2）。

    逐块读取并累计字节数，超过 MAX_UPLOAD_FILE_BYTES 立即中止、清理
    临时文件并抛 413 file_too_large；返回 (暂存路径, 字节数)。暂存文件
    由调用方在校验链结束（成功落盘或任一步失败）后统一清理。
    """
    fd, name = tempfile.mkstemp(prefix="latexcellence-upload-")
    staged = Path(name)
    total = 0
    try:
        with os.fdopen(fd, "wb") as out:
            src = upload_file.file
            src.seek(0)
            while chunk := src.read(_UPLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_UPLOAD_FILE_BYTES:
                    raise _http(
                        413,
                        "file_too_large",
                        "File exceeds the upload size limit of "
                        f"{MAX_UPLOAD_FILE_BYTES} bytes",
                    )
                out.write(chunk)
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    return staged, total


def _prepare_upload_sync(project, file_path: str) -> Path:
    """同步校验阶段（editor-file-upload-design §4.1 校验链第 2–5 步）：
    文件名消毒 → 隐藏组件拒绝 → resolve_project_path（不限后缀，任意
    格式成立的路径边界不变）→ 冲突/父目录检查；返回目标绝对路径，
    落盘由 _commit_upload_sync 执行。
    """
    root = Path(project.root_path)
    _validate_upload_target_name(file_path)
    _reject_hidden_components(file_path)
    full_path = resolve_project_path(root, file_path)
    if full_path.exists():
        # 错误消息使用相对路径（多用户脱敏）；完整路径只进服务端日志
        logger.info("Editor upload conflict: %s (root=%s)", file_path, root)
        raise FileExistsError(f"File already exists: {file_path}")
    if not full_path.parent.is_dir():
        raise FileNotFoundError(f"Parent directory does not exist: {file_path}")
    return full_path


def _commit_upload_sync(target: Path, staged: Path) -> None:
    """原子落盘（editor-file-upload-design §4.1 校验链第 7 步 / §4.2）。

    暂存文件位于系统临时目录，与项目目录可能跨文件系统，直接 replace
    会 EXDEV 失败；与 file_ops.atomic_write 同模式：先复制到目标同目录
    临时文件（保证 rename 在同一文件系统上），再 Path.replace（os.replace
    原子语义）替换到目标路径，目标绝不出现半写状态。整条链路按字节
    搬运，二进制不走 write_text。
    """
    fd, tmp_name = tempfile.mkstemp(
        suffix=".tmp", prefix=f".{target.name}.", dir=target.parent
    )
    tmp = Path(tmp_name)
    try:
        try:
            out = os.fdopen(fd, "wb")
        except Exception:
            os.close(fd)
            raise
        with out, staged.open("rb") as src:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
        tmp.replace(target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


async def upload_project_file(
    project,
    binding: dict,
    *,
    file_path: str,
    upload_file: UploadFile,
) -> dict:
    """上传文件到项目内任意相对路径（文件树操作，零 LLM、不进撤销记账）。

    任意格式 + §3 安全护栏（回读边界/路径边界/隐藏路径拒绝/大小与配额/
    鉴权/文件名消毒）见设计文档；校验链按 §4.1 顺序执行。多用户模式
    属"已知增量"写路径：落盘前按实际上传字节数做项目配额预检（超限
    413 quota_exceeded），落盘后刷新用量记账。响应携带重扫后的
    file_tree，前端单次往返完成文件树刷新。
    """
    # 1. 大小流式校验：超限即中止（临时文件已在 _stream_upload_to_temp
    #    内清理）；无论后续成败，UploadFile 底层 spool 一律关闭
    try:
        staged, size = await asyncio.to_thread(_stream_upload_to_temp, upload_file)
    finally:
        await upload_file.close()

    # 校验链 2–7 步与暂存文件清理收口在同一 finally：任何一步失败都不
    # 残留暂存文件（成功 replace 后暂存路径已不存在，unlink 幂等）
    try:
        try:
            # 2–5. 消毒 → 隐藏组件 → resolve（不限后缀）→ 冲突/父目录
            target = await asyncio.to_thread(_prepare_upload_sync, project, file_path)
        except FileExistsError as e:
            raise _http(409, "file_exists", str(e)) from e
        except FileNotFoundError as e:
            raise _http(400, "parent_not_found", str(e)) from e
        except ValueError as e:
            raise _http(400, "invalid_path", str(e)) from e
        except RuntimeError as e:
            raise _http(400, "invalid_workspace", str(e)) from e

        # 6. 多用户配额预检（先于落盘；HTTPException 原样透传）
        owner_id, project_id = _binding_owner(binding)
        if owner_id and project_id:
            await asyncio.to_thread(enforce_project_quota, owner_id, size)

        # 7. 原子写盘（同目录临时文件 + Path.replace，二进制字节保真）
        await asyncio.to_thread(_commit_upload_sync, target, staged)
    finally:
        staged.unlink(missing_ok=True)

    # 8. 多用户用量刷新（单用户模式内部跳过）
    if project_id:
        await asyncio.to_thread(refresh_owner_usage_after_write, project_id)
    invalidate_project_outline_cache(project)
    logger.info("Editor file uploaded: %s (%d bytes)", file_path, size)
    return {
        "status": "ok",
        "file_path": file_path,
        "file_tree": _refreshed_tree(project),
    }


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


# ── 引用符号扫描（editor-productivity-design §5.2 / 决策 Q7，阶段四） ──────
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


# ── 一键格式化（editor-productivity-design §5.2 / 决策 Q8，阶段五） ──────
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


# ── 跨文件大纲扫描（editor-experience-design §4.4 / 决策 Q10，阶段四） ────
# 正则口径与前端 lib/editor/outlineScan.js 严格一致（命令集合与层级判定
# 同源）：分节命令 section / subsection / subsubsection / paragraph（含
# 星号变体与可选参数 [短标题]）、注释与 \verb 参数剔除、verbatim 类环境
# 豁免、跨行标题保守跳过、标题净化规则逐条对齐。前端改动口径时两侧须
# 同步修订。

# 大纲扫描扩展名白名单：跨文件大纲只看 .tex 分节结构
OUTLINE_SCAN_SUFFIXES: tuple[str, ...] = (".tex",)

# 单文件扫描的内容上限（与 MAX_EDITABLE_CHARS 同量级）：异常超大文件跳过
MAX_OUTLINE_SCAN_CHARS = 2_000_000

# 分节命令 → 大纲层级（长名在前，防止 subsection 误吞 section 前缀；
# (?![a-zA-Z]) 杜绝 \sectionfoo 误匹配）
_SECTION_COMMAND_RE = re.compile(
    r"\\(subsubsection|subsection|section|paragraph)(?![a-zA-Z])"
)

# 分节命令名 → 缩进层级（section 0 / subsection 1 / subsubsection 2 /
# paragraph 3，与前端 LEVEL_BY_NAME 一致）
_LEVEL_BY_NAME: dict[str, int] = {
    "section": 0,
    "subsection": 1,
    "subsubsection": 2,
    "paragraph": 3,
}

# verbatim 类环境起点（\begin{...}；环境名区分大小写，保守集合与前端一致）
_BEGIN_VERBATIM_RE = re.compile(
    r"\\begin\s*\{(verbatim\*?|Verbatim\*?|lstlisting|minted)\}"
)

# 连续空白折叠（标题净化收尾用）
_TITLE_WHITESPACE_RE = re.compile(r"\s+")


def _is_ascii_alpha(ch: str) -> bool:
    """JS /[a-zA-Z]/ 同口径的 ASCII 字母判定（Python isalpha 含 Unicode，不通用）。"""
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z")


def _strip_verb_spans(line: str) -> str:
    r"""移除一行中所有 \verb（含 \verb*）行内参数，防止参数文本被误认成大纲条目。

    与前端 outlineScan.stripVerbSpans 同语义：定界符为字母/空白（或缺失）
    视为非法 verb 形态原样保留；未闭合时余下内容整体视为 verb 内部排除。
    """
    out: list[str] = []
    i = 0
    n = len(line)
    while True:
        idx = line.find("\\verb", i)
        if idx == -1:
            out.append(line[i:])
            return "".join(out)
        out.append(line[i:idx])
        j = idx + 5
        if j < n and line[j] == "*":
            j += 1
        delim = line[j] if j < n else None
        if delim is None or delim.isspace() or _is_ascii_alpha(delim):
            out.append(line[idx : j + 1])
            i = j + 1
            continue
        end = line.find(delim, j + 1)
        i = n if end == -1 else end + 1


def _read_balanced_group(line: str, open_idx: int) -> str | None:
    r"""读取 line 中自 open_idx（指向 `{`）起的配平花括号组，返回组内文本。

    未配平（如跨行标题）返回 None；`\{` / `\}` 等转义字符不参与配平计数
    （与前端 outlineScan.readBalancedGroup 同语义）。
    """
    depth = 0
    i = open_idx
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == "\\":
            i += 2  # 跳过转义字符的下一个字符
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return line[open_idx + 1 : i]
        i += 1
    return None


def _title_to_plain_text(raw: str) -> str:
    r"""标题原文 → 展示用纯文本（与前端 outlineScan.titleToPlainText 逐条对齐）。

    - 命令名后紧跟花括号参数：丢弃命令名，参数内容由外层继续处理
      （`\textbf{粗}` → 粗）；
    - 无参数命令：保留命令名去反斜杠（`\LaTeX` → LaTeX，含星号变体）；
    - 转义符号（`\%` / `\_` / `\{` 等）：保留字面字符；
    - `~`：空格；花括号定界符丢弃；连续空白折叠为单个空格。
    """
    if not isinstance(raw, str):
        return ""
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\":
            if i + 1 >= n:
                break
            nxt = raw[i + 1]
            if _is_ascii_alpha(nxt):
                j = i + 1
                while j < n and _is_ascii_alpha(raw[j]):
                    j += 1
                if j < n and raw[j] == "*":
                    j += 1
                k = j
                while k < n and raw[k] in (" ", "\t"):
                    k += 1
                if k < n and raw[k] == "{":
                    # 带参数命令：丢弃命令名（参数内容与其后的 `}` 由主循环处理）
                    i = j + 1
                else:
                    out.append(raw[i + 1 : j])
                    i = j
            else:
                out.append(nxt)
                i += 2
            continue
        if ch in ("{", "}"):
            i += 1
            continue
        if ch == "~":
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    return _TITLE_WHITESPACE_RE.sub(" ", "".join(out)).strip()


def _collect_section_entries(line: str, line_no: int, entries: list[dict]) -> None:
    r"""在已净化的单行文本中收集分节条目（与前端 collectSectionEntries 同语义）。

    星号变体跳过、可选参数 [...] 跳过后取强制参数、跨行标题（花括号未在
    同行配平）保守跳过；标题内部再出现分节命令字样时整段跳过不重复计入。
    """
    pos = 0
    n = len(line)
    while True:
        m = _SECTION_COMMAND_RE.search(line, pos)
        if m is None:
            return
        i = m.end()
        if i < n and line[i] == "*":
            i += 1  # 星号变体（\section*）
        if i < n and line[i] == "[":
            close = line.find("]", i + 1)
            if close == -1:
                pos = m.end()
                continue
            i = close + 1
        while i < n and line[i] in (" ", "\t"):
            i += 1
        if i >= n or line[i] != "{":
            pos = m.end()
            continue
        raw = _read_balanced_group(line, i)
        if raw is None:
            pos = m.end()
            continue
        title = _title_to_plain_text(raw)
        if title:
            entries.append(
                {"level": _LEVEL_BY_NAME[m.group(1)], "title": title, "line": line_no}
            )
        # 跳过整个已消费的花括号组（组内分节命令字样不重复计入）
        pos = i + len(raw) + 2


def scan_tex_outline(content: str) -> list[dict]:
    r"""扫描单个 .tex 文件全文，产出大纲条目（保持文档出现顺序）。

    返回 [{level, title, line}]（line 为 1 基行号）；注释、\verb 参数与
    verbatim 类环境内不产生条目（逐行单趟扫描，口径与前端 outlineScan.scanOutline
    一致）。纯函数，供服务层与测试复用。
    """
    entries: list[dict] = []
    if not isinstance(content, str) or not content:
        return entries
    verbatim_env: str | None = None
    for line_no, raw_line in enumerate(content.split("\n"), 1):
        if verbatim_env:
            # verbatim 内一切字面（含 % 注释），只找环境结束标记
            if re.search(rf"\\end\s*\{{{re.escape(verbatim_env)}\}}", raw_line):
                verbatim_env = None
            continue
        # 剥离顺序：先剥 \verb 行内参数（其参数内 % 为字面字符），再剥注释
        # （被注释掉的 \begin{verbatim} 不应进入 verbatim 态），最后检查
        # verbatim 环境起点（起点之后同行内容均在 verbatim 内）
        work = _strip_verb_spans(raw_line)
        work = _strip_comment(work)
        begin_match = _BEGIN_VERBATIM_RE.search(work)
        if begin_match:
            verbatim_env = begin_match.group(1)
            work = work[: begin_match.start()]
        _collect_section_entries(work, line_no, entries)
    return entries


# 项目级跨文件大纲缓存（决策 Q10：沿袭 latex-symbols 缓存模式——以项目为
# 键、保存成功后失效重扫；键为项目根路径字符串，值为最近一次扫描结果）。
# 上限封顶防长驻进程在大量项目间切换时无界膨胀（FIFO 逐出最旧项目）。
_outline_cache: dict[str, dict] = {}
_OUTLINE_CACHE_MAX_ENTRIES = 16


def invalidate_project_outline_cache(project) -> None:
    """保存失效入口：清掉该项目的跨文件大纲缓存（下次请求全量重扫）。

    save-file 落盘成功后调用；项目未打开（RuntimeError）时静默跳过。
    """
    try:
        root = str(Path(project.root_path))
    except RuntimeError:
        return
    _outline_cache.pop(root, None)


def _scan_outline_sync(project) -> dict:
    """全项目 .tex 分节扫描（同步实现，由 asyncio.to_thread 调度）。

    只读扫描：遍历项目内 .tex（隐藏目录跳过），每个文件经
    resolve_project_path() 校验（扩展名白名单 + 逐组件符号链接检测，
    指向项目外的文件静默跳过）；响应只含项目内相对路径（多用户脱敏）。
    无大纲条目的文件不进响应（空分组对目录无信息量）。
    """
    root = Path(project.root_path)  # 未打开项目时 RuntimeError → invalid_workspace
    files: list[dict] = []
    total_entries = 0
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in OUTLINE_SCAN_SUFFIXES or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        # 隐藏目录（.git 等）下的文件不参与大纲扫描
        if any(part.startswith(".") for part in Path(rel).parts):
            continue
        try:
            full_path = resolve_project_path(
                root, rel, allowed_suffixes=OUTLINE_SCAN_SUFFIXES
            )
        except ValueError as e:
            logger.debug("Outline scan skip %s: %s", rel, e)
            continue
        # 超限预检在读入前按字节大小进行（UTF-8 字节数 ≥ 字符数）
        try:
            size = full_path.stat().st_size
        except OSError as e:
            logger.info("Outline scan unreadable file %s: %s", rel, e)
            continue
        if size > MAX_OUTLINE_SCAN_CHARS:
            logger.info("Outline scan skip oversized file %s (%d bytes)", rel, size)
            continue
        try:
            content = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.info("Outline scan unreadable file %s: %s", rel, e)
            continue
        entries = scan_tex_outline(content)
        if not entries:
            continue
        files.append({"file_path": rel, "entries": entries})
        total_entries += len(entries)

    logger.info(
        "Outline scan done: %d files, %d entries",
        len(files),
        total_entries,
    )
    return {"files": files}


async def scan_project_outline(project) -> dict:
    """全项目只读 .tex 分节扫描（跨文件大纲数据源，零 LLM、零写盘）。

    返回 {files: [{file_path, entries: [{level, title, line}]}]}；
    file_path 一律为项目内相对路径（多用户脱敏）。项目级缓存命中直接
    返回，save-file 成功后由 invalidate_project_outline_cache 失效。
    """
    try:
        cache_key = str(Path(project.root_path))
    except RuntimeError as e:
        raise _http(400, "invalid_workspace", str(e)) from e
    cached = _outline_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        result = await asyncio.to_thread(_scan_outline_sync, project)
    except RuntimeError as e:
        raise _http(400, "invalid_workspace", str(e)) from e
    _outline_cache[cache_key] = result
    if len(_outline_cache) > _OUTLINE_CACHE_MAX_ENTRIES:
        _outline_cache.pop(next(iter(_outline_cache)))
    return result
