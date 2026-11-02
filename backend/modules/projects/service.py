"""
多用户项目服务层（P3：上传 / 下载 / 回收站 / 配额记账）。

- 业务逻辑：压缩包接收 → 安全解压 → 布局归一化 → 用户空间落位 →
  唯一命名 → 配额记账；以及重命名 / 回收站 / 恢复 / 永久删除 / 下载
- 本层通过 asyncio.to_thread 调度阻塞 I/O（文件读写、解压、统计），不阻塞事件循环
- 权限复用 backend.core.access_control（实时查库）；管理型动作仅 owner
- 错误统一映射为 HTTPException（结构化 detail：{"error": code, "message": ...}，
  便于前端 translateError 翻译）
"""

import asyncio
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from pathlib import Path

from fastapi import HTTPException, Request, UploadFile

from backend.core import access_control
from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import upload_sessions, userspace
from backend.core.multiuser.db import db_execute, db_query_all, db_query_one, utcnow
from backend.core.multiuser.dependencies import forbidden
from backend.core.multiuser.quota import (
    create_project_row,
    dir_size,
    effective_project_quota,
    enforce_git_dir_cap,
    enforce_project_quota,
    next_available_name,
    project_usage_for_user,
    refresh_owner_usage_after_write,
)
from backend.core.multiuser.upload import (
    ArchiveError,
    build_limits,
    extract_archive,
    move_into_user_space,
)
from backend.core.workspace_manager import get_workspace_manager
from backend.modules.git_manage import service as git_manage_service
from backend.modules.projects.models import (
    COLLABORATOR_ROLES,
    UploadSessionInitRequest,
)
from backend.utils.sandbox import to_http_exception

logger = get_logger("projects.service")

MAX_NAME_LENGTH = 200


def _http(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message})


def _map_archive_error(err: ArchiveError) -> HTTPException:
    """把解压校验错误映射为稳定 HTTP 错误。

    路径穿越 / 压缩炸弹 / 加密包等安全拒绝在此统一记录审计日志。
    分片会话错误码（v2 P5）：upload_session_not_found → 404（不泄露
    存在性）、upload_session_expired → 410（引导重新选择）。"""
    logger.warning("Upload rejected: code=%s message=%s", err.code, err.message)
    if err.code == "upload_session_not_found":
        return _http(404, err.code, err.message)
    if err.code == "upload_session_expired":
        return _http(410, err.code, err.message)
    if err.code in ("quota_exceeded", "upload_too_large"):
        return _http(413, err.code, err.message)
    return _http(400, err.code, err.message)


async def list_projects(user: dict) -> dict:
    """我的项目 + 被共享的项目（不含回收站）。"""
    projects = await access_control.get_projects_for_user_async(user["id"])
    return {"projects": projects}


async def list_recycle_bin(user_id: str) -> dict:
    """回收站列表：当前用户已删除（回收站）的项目，按删除时间倒序。"""
    rows = await asyncio.to_thread(
        db_query_all,
        "SELECT * FROM projects WHERE owner_id = ? AND status = 'deleted' "
        "ORDER BY deleted_at DESC, id",
        (user_id,),
    )
    views = await asyncio.to_thread(lambda: [_project_row_view(r) for r in rows])
    return {"projects": views}


async def upload_create_project(
    user: dict, archive: UploadFile, requested_name: str | None = None
) -> dict:
    """接收 multipart 压缩包，安全解压并创建项目。"""
    cfg = get_config()
    if not cfg.multi_user.enabled:
        raise _http(404, "not_found", "Not found")

    # 扩展名校验最前置：非法格式直接映射为 400 invalid_archive，且不创建
    # 任何暂存目录（此前在暂存目录创建之后、try 块之外抛出，导致
    # 500 + 暂存目录泄漏，只能等后台 clean_staging 兜底）
    archive_name = archive.filename or ""
    try:
        suffix = _archive_suffix(archive_name)
    except ArchiveError as e:
        raise _map_archive_error(e)

    staging_root = userspace.get_staging_root()
    staging_root.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex
    staging_dir = staging_root / upload_id
    staging_dir.mkdir(parents=True, exist_ok=False)

    base_name = sanitize_project_name(
        requested_name or _stem_from_filename(archive_name) or "project"
    )

    try:
        archive_path = staging_dir / f"upload{suffix}"
        await asyncio.to_thread(_write_upload, archive, archive_path, cfg.multi_user)
    except ArchiveError as e:
        _cleanup_staging(staging_dir)
        raise _map_archive_error(e)
    except Exception:
        _cleanup_staging(staging_dir)
        raise

    extract_dir = staging_dir / "extracted"
    try:
        extract_dir.mkdir(parents=True, exist_ok=False)
        owner_id = user["id"]
        remaining = await asyncio.to_thread(
            lambda: effective_project_quota(owner_id) - project_usage_for_user(owner_id)
        )
        limits = build_limits(remaining)
        extracted = await asyncio.to_thread(
            extract_archive, archive_path, extract_dir, limits
        )
    except ArchiveError as e:
        _cleanup_staging(staging_dir)
        raise _map_archive_error(e)
    except Exception as e:
        # rar 解压经 SandboxRunner 执行（docker 模式 daemon 不可达等）抛出
        # 带 sandbox_code 的错误：按既有语义映射 503/429，不落入通用 400
        sandbox_http = to_http_exception(e)
        if sandbox_http is not None:
            logger.warning("Sandbox error during upload %s: %s", upload_id, e)
            _cleanup_staging(staging_dir)
            raise sandbox_http
        logger.exception("Unexpected extraction failure for upload %s", upload_id)
        _cleanup_staging(staging_dir)
        # 解压异常原文可能携带 staging 绝对路径，仅进服务端日志
        raise _http(400, "invalid_archive", "Failed to extract archive")

    # 配额最终复核（解压后总量 + 现有用量不得超过有效配额）
    project_id = uuid.uuid4().hex
    dest: Path | None = None

    def _discard_partial_dest() -> None:
        """落位失败后按确定性路径清理用户空间残留。

        move_into_user_space 中途失败时不返回 dest，但目标路径由
        (user_id, project_id) 唯一确定，可直接清理——否则部分落位的
        孤儿项目目录不在暂存区，clean_staging 永远清不到。"""
        dest_path = userspace.project_dir(user["id"], project_id)
        if dest_path.exists():
            shutil.rmtree(dest_path, ignore_errors=True)

    try:
        await asyncio.to_thread(enforce_project_quota, user["id"], extracted.size)
        dest = await asyncio.to_thread(
            move_into_user_space,
            extract_dir,
            extracted.root,
            user["id"],
            project_id,
        )
        # .git 超独立上限 → 整包拒绝（.git 不占项目配额，改按专上限约束）
        await asyncio.to_thread(enforce_git_dir_cap, dest)
        # .git 不计入项目配额口径（豁免语义，与对账一致）
        size = await asyncio.to_thread(lambda: dir_size(dest, exclude_dirs={".git"}))
        await asyncio.to_thread(enforce_project_quota, user["id"], size)
        row = await asyncio.to_thread(
            create_project_row, user["id"], base_name, size, project_id=project_id
        )
    except ArchiveError as e:
        _cleanup_staging(staging_dir)
        _discard_partial_dest()
        raise _map_archive_error(e)
    except HTTPException:
        _cleanup_staging(staging_dir)
        _discard_partial_dest()
        raise
    except Exception:
        # 落位阶段兜底清理：move_into_user_space 中途失败（跨分区复制磁盘满等
        # OSError）可能在用户空间留下部分落位、无 DB 行的孤儿项目目录——
        # 该目录不在暂存区，clean_staging 清不到，必须就地删除后再上抛
        logger.exception(
            "Unexpected failure placing project %s into user space", project_id
        )
        _cleanup_staging(staging_dir)
        _discard_partial_dest()
        raise
    finally:
        _cleanup_staging(staging_dir)

    # 落位成功后异步自动初始化 Git 版本库（best-effort；自带 .git 原样保留）
    git_manage_service.schedule_auto_init(user["id"], row["id"])

    return {"project": _project_row_view(row)}


def _write_upload(archive: UploadFile, archive_path: Path, mu_cfg) -> None:
    """把上传内容流式写入暂存文件，并实时校验压缩包大小上限。

    archive 为 FastAPI UploadFile（同步读由 asyncio.to_thread 调度）。
    """
    max_bytes = mu_cfg.max_upload_archive_mb * 1024 * 1024
    written = 0
    with open(archive_path, "wb") as dst:
        while True:
            chunk = archive.file.read(1 << 20)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                raise ArchiveError(
                    "upload_too_large",
                    f"Archive exceeds upload size limit ({mu_cfg.max_upload_archive_mb} MB)",
                )
            dst.write(chunk)


def _stem_from_filename(filename: str) -> str:
    """从上传文件名提取项目基名（去扩展名）。"""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    for suffix in (".zip", ".7z", ".rar"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip()


def _archive_suffix(filename: str) -> str:
    """校验并返回上传文件扩展名（.zip / .7z / .rar），否则抛 ArchiveError。"""
    low = filename.lower()
    if low.endswith(".zip"):
        return ".zip"
    if low.endswith(".7z") or low.endswith(".7z.001"):
        # 分卷 7z（.001 起始卷）不提供；命名按 .7z 保存后由解压管线拒绝
        return ".7z"
    if low.endswith(".rar"):
        # 分卷 rar（.partN.rar 起始卷）按 .rar 保存后由解压管线拒绝
        return ".rar"
    raise ArchiveError(
        "invalid_archive",
        "Unsupported archive type (only .zip, .7z and .rar)",
    )


def sanitize_project_name(name: str, fallback: str = "project") -> str:
    """清洗项目名：去掉路径分隔符/控制字符，压缩空白，限制长度。"""
    if not name:
        return fallback
    cleaned = re.sub(r"[\r\n\t/\\:\*\?\"<>|]+", " ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        return fallback
    return cleaned[:MAX_NAME_LENGTH]


# ── 分片上传会话（v2 P5：大文件断点续传） ──────────────────────────────────
async def upload_session_init(user: dict, req: UploadSessionInitRequest) -> dict:
    """创建分片上传会话（manifest 预检 + 剩余配额预检；分片阶段不占配额）。"""
    name = sanitize_project_name(req.name)
    if not name:
        raise _http(400, "invalid_name", "Project name cannot be empty")
    files = [entry.model_dump() for entry in req.files]
    try:
        return await asyncio.to_thread(
            upload_sessions.create_session,
            user["id"],
            name,
            req.kind,
            int(req.total_bytes),
            files,
        )
    except ArchiveError as e:
        raise _map_archive_error(e)


async def _read_capped_body(request: Request, cap_bytes: int) -> bytes:
    """流式读取分片二进制体，超过 cap 立即拒绝（413 upload_too_large）。"""
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > cap_bytes:
            raise _http(
                413,
                "upload_too_large",
                f"Part body exceeds chunk size limit ({cap_bytes} bytes)",
            )
    return bytes(buf)


async def upload_session_put_part(
    user: dict,
    sid: str,
    file_index: int,
    chunk_index: int,
    part_md5: str | None,
    request: Request,
) -> dict:
    """接收单个分片（X-Part-MD5 校验、幂等覆盖、成功续期会话）。"""
    cfg = get_config().multi_user
    data = await _read_capped_body(request, cfg.chunk_part_max_mb * 1024 * 1024)
    try:
        return await asyncio.to_thread(
            upload_sessions.store_part,
            user["id"],
            sid,
            file_index,
            chunk_index,
            data,
            part_md5 or "",
        )
    except ArchiveError as e:
        raise _map_archive_error(e)


async def upload_session_status(user: dict, sid: str) -> dict:
    """查询会话状态与每文件已收分块位图（断点续传对位）；同时续期。"""
    try:
        return await asyncio.to_thread(upload_sessions.session_status, user["id"], sid)
    except ArchiveError as e:
        raise _map_archive_error(e)


async def upload_session_complete(user: dict, sid: str) -> dict:
    """完成会话：分块齐全 + 全量 MD5 复核 → 拼装 → 配额二次复核 → 落位建档。"""
    try:
        row = await asyncio.to_thread(upload_sessions.complete_session, user["id"], sid)
    except ArchiveError as e:
        raise _map_archive_error(e)
    except Exception as e:
        # rar 分片会话解压经 SandboxRunner 执行：sandbox 错误按既有语义
        # 映射 503/429（complete_session 内部已整包清场后原样上抛）
        sandbox_http = to_http_exception(e)
        if sandbox_http is not None:
            raise sandbox_http
        raise
    # 落位成功后异步自动初始化 Git 版本库（best-effort；自带 .git 原样保留）。
    # 须在事件循环上调度——complete_session 在工作线程执行，线程内无 loop。
    git_manage_service.schedule_auto_init(user["id"], row["id"])
    return {"project": _project_row_view(row)}


async def upload_session_abort(user: dict, sid: str) -> dict:
    """中止会话并清理会话行与 staging 目录。"""
    try:
        await asyncio.to_thread(upload_sessions.abort_session, user["id"], sid)
    except ArchiveError as e:
        raise _map_archive_error(e)
    return {"status": "ok", "session_id": sid}


def _project_row_view(row: dict) -> dict:
    """把 projects 行转成前端项目视图（含 owner 信息与角色）。"""
    owner = db_query_one(
        "SELECT username, email FROM users WHERE id = ?", (row["owner_id"],)
    )
    return {
        "project_id": row["id"],
        "name": row["name"],
        "role": "owner",
        "owner_id": row["owner_id"],
        "owner_username": owner["username"] if owner else "",
        "size_bytes": row["size_bytes"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "is_shared": False,
    }


async def _require_owner(user_id: str, project_id: str) -> dict:
    """要求用户对该项目拥有 owner 权限；否则 403/404。"""
    access = await access_control.require_project_access_async(user_id, project_id)
    if access is None or access["role"] != "owner":
        raise forbidden(
            "no_permission", "Only the project owner can manage this project"
        )
    return access


async def rename_project(user_id: str, project_id: str, new_name: str) -> dict:
    await _require_owner(user_id, project_id)
    name = sanitize_project_name(new_name)
    if not name:
        raise _http(400, "invalid_name", "Project name cannot be empty")
    available = await asyncio.to_thread(next_available_name, user_id, name)
    row = await asyncio.to_thread(
        db_query_one, "SELECT * FROM projects WHERE id = ?", (project_id,)
    )
    if row is None:
        raise _http(404, "not_found", "Project not found")
    # 若新名字与自身当前名相同，直接放行
    if available != name and row["name"] != name:
        raise _http(
            409,
            "name_conflict",
            f"A project named '{name}' already exists",
        )
    await asyncio.to_thread(
        db_execute,
        "UPDATE projects SET name = ?, updated_at = ? WHERE id = ?",
        (name, utcnow(), project_id),
    )
    updated = await asyncio.to_thread(
        db_query_one, "SELECT * FROM projects WHERE id = ?", (project_id,)
    )
    return {"project": _project_row_view(updated)}


async def delete_project(user_id: str, project_id: str) -> dict:
    """进入回收站（status='deleted'）；删除即释放项目配额（不计入用量）。"""
    await _require_owner(user_id, project_id)
    await asyncio.to_thread(
        db_execute,
        "UPDATE projects SET status = 'deleted', deleted_at = ?, updated_at = ? "
        "WHERE id = ? AND status = 'active'",
        (utcnow(), utcnow(), project_id),
    )
    logger.info("Project %s moved to recycle bin by user %s", project_id, user_id)
    return {"status": "ok", "project_id": project_id}


async def restore_project(user_id: str, project_id: str) -> dict:
    """从回收站恢复；恢复前校验项目空间配额（超限禁止恢复）。

    撞名处理：回收站期间同名活动项目可能已占用名称，与上传/转交一致
    自动去重为 `xx (2)`——恢复语句命中 (owner, name) 部分唯一索引时
    未捕获 IntegrityError 会表现为 500（审查 Required#1）。
    """
    row = await asyncio.to_thread(
        db_query_one, "SELECT * FROM projects WHERE id = ?", (project_id,)
    )
    if row is None:
        raise _http(404, "not_found", "Project not found")
    if row["owner_id"] != user_id:
        raise forbidden(
            "no_permission", "Only the project owner can restore this project"
        )
    if row["status"] != "deleted":
        raise _http(400, "invalid_state", "Project is not in the recycle bin")

    # 恢复前重算实际磁盘大小（.git 豁免口径），并校验在计入后不超配额
    size = await asyncio.to_thread(
        lambda: dir_size(
            userspace.project_dir(user_id, project_id), exclude_dirs={".git"}
        )
    )
    await asyncio.to_thread(enforce_project_quota, user_id, size)
    # 项目自身仍是 deleted 状态，不计入活动名占用；无冲突时原样返回
    restored_name = await asyncio.to_thread(next_available_name, user_id, row["name"])
    try:
        await asyncio.to_thread(
            db_execute,
            "UPDATE projects SET status = 'active', deleted_at = NULL, "
            "size_bytes = ?, name = ?, updated_at = ? "
            "WHERE id = ? AND status = 'deleted'",
            (size, restored_name, utcnow(), project_id),
        )
    except sqlite3.IntegrityError:
        # 并发窗口内名称被其他活动项目占用：重取名后重试一次
        restored_name = await asyncio.to_thread(
            next_available_name, user_id, row["name"]
        )
        await asyncio.to_thread(
            db_execute,
            "UPDATE projects SET status = 'active', deleted_at = NULL, "
            "size_bytes = ?, name = ?, updated_at = ? "
            "WHERE id = ? AND status = 'deleted'",
            (size, restored_name, utcnow(), project_id),
        )
    logger.info("Project %s restored from recycle bin by user %s", project_id, user_id)
    return {"status": "ok", "project_id": project_id}


async def permanent_delete_project(user_id: str, project_id: str) -> dict:
    """提前永久删除回收站项目（目录 + 数据行）。"""
    row = await asyncio.to_thread(
        db_query_one, "SELECT * FROM projects WHERE id = ?", (project_id,)
    )
    if row is None:
        raise _http(404, "not_found", "Project not found")
    if row["owner_id"] != user_id:
        raise forbidden(
            "no_permission", "Only the project owner can manage this project"
        )
    if row["status"] != "deleted":
        raise _http(
            400, "invalid_state", "Only deleted projects can be permanently removed"
        )
    root = userspace.project_dir(user_id, project_id)
    await asyncio.to_thread(shutil.rmtree, root, ignore_errors=True)
    # 级联清理该项目的反向补丁编辑日志（行 + .edit_logs 快照目录，v2 P3）
    from backend.core.multiuser import edit_log

    await asyncio.to_thread(edit_log.purge_project_ops, project_id)
    await asyncio.to_thread(
        db_execute, "DELETE FROM projects WHERE id = ?", (project_id,)
    )
    logger.info("Project %s permanently deleted by user %s", project_id, user_id)
    return {"status": "ok", "project_id": project_id}


async def download_project(user_id: str, project_id: str) -> tuple[str, str, Path]:
    """打包项目根目录为 zip，返回（临时文件路径, 下载文件名, 临时目录）。

    仅校验读权限（owner / editor / viewer 均可下载）。
    """
    await access_control.require_project_access_async(user_id, project_id)
    row = await asyncio.to_thread(
        db_query_one, "SELECT * FROM projects WHERE id = ?", (project_id,)
    )
    if row is None:
        raise _http(404, "not_found", "Project not found")
    root = userspace.project_dir(row["owner_id"], project_id)
    temp_root = Path(tempfile.mkdtemp(prefix="latexcellence_dl_"))
    zip_path = temp_root / f"{sanitize_project_name(row['name'])}.zip"
    try:
        await asyncio.to_thread(_build_zip, str(root), str(zip_path))
    except Exception:
        # 打包异常原文（OSError 等）携带用户空间绝对路径，仅进服务端日志；
        # 客户端按 download_failed 错误码取固定文案
        shutil.rmtree(temp_root, ignore_errors=True)
        logger.exception("Failed to build download zip for project %s", project_id)
        raise _http(400, "download_failed", "Failed to package project")
    download_name = f"{sanitize_project_name(row['name'])}.zip"
    return str(zip_path), download_name, temp_root


def _build_zip(source_dir: str, zip_path: str) -> None:
    """把 source_dir 内容写入 zip_path（不打包父目录本身）。"""
    src = Path(source_dir)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_symlink():
                continue
            if p.is_file():
                zf.write(str(p), arcname=str(p.relative_to(src)))


def _cleanup_staging(staging_dir: Path) -> None:
    shutil.rmtree(staging_dir, ignore_errors=True)


# ── 写回配额（apply-change 超限回滚） ──────────────────────────────────────
async def enforce_write_quota(
    binding: dict,
    project,
    file_path: str,
    *,
    before_content: str | None = None,
    edit_op_id: str | None = None,
    actor_user_id: str | None = None,
) -> None:
    """写回后配额记账：超限则以写前内容直接恢复并抛 413 quota_exceeded。

    仅多用户模式下生效（单用户模式不设配额，直接放行）。
    v2 P3 起回滚不再走 project.undo 撤销通道（避免误回退他人修改），
    改为以写前全文经抑制记账的 apply_whole_files 恢复，并以本次写入的
    编辑日志 after_md5 作乐观锁版本守卫——配额检查窗口期内若他人已
    落盘新修改，则放弃覆盖（绝不静默覆盖他人修改），只撤销本次记账并
    如实报错。撤销栈不被失败写入污染（方案 §8.4），调用方须在写入前
    捕获 before_content 并透传 apply_change 返回的 edit_op_id。
    回滚失败时记录异常并以 500 如实上报——超限写入仍在磁盘上，
    不得向客户端谎报已回滚。
    """
    if not binding or not binding.get("multi_user") or not binding.get("project_id"):
        return
    project_id = binding["project_id"]
    owner_id = binding.get("owner_id")
    if not owner_id:
        return

    await asyncio.to_thread(refresh_owner_usage_after_write, project_id)
    usage = await asyncio.to_thread(project_usage_for_user, owner_id)
    quota = await asyncio.to_thread(effective_project_quota, owner_id)
    if usage <= quota:
        return

    # 超限：以写前全文直接恢复（不经撤销通道），并删除本次写入的编辑日志 op
    if before_content is None:
        logger.error(
            "Quota rollback skipped, no before-content captured: " "project=%s file=%s",
            project_id,
            file_path,
        )
        raise _http(
            500,
            "quota_exceeded",
            "Project space quota exceeded; automatic rollback failed",
        )
    from backend.core.multiuser import edit_log
    from backend.core.project_manager import FileConflictError

    guard_md5s: dict[str, str] | None = None
    if edit_op_id and actor_user_id:
        # 版本守卫来源：本次写入的编辑日志清单（写入后 MD5 的权威记录）
        guard_md5s = await asyncio.to_thread(
            edit_log.op_after_md5s, edit_op_id, project_id, actor_user_id
        )
    expected_md5s = (
        {file_path: guard_md5s[file_path]}
        if guard_md5s and file_path in guard_md5s
        else None
    )

    rollback_skipped_by_conflict = False
    try:
        if expected_md5s is not None:
            try:
                # 仅当文件仍是本次写入的结果时才覆盖；期间他人落盘新修改
                # 则放弃覆盖，绝不静默回退他人内容（方案 §8.4）
                with edit_log.suppress_recording():
                    await asyncio.to_thread(
                        project.apply_whole_files,
                        {file_path: before_content},
                        expected_md5s=expected_md5s,
                    )
            except FileConflictError:
                rollback_skipped_by_conflict = True
                logger.error(
                    "Quota rollback aborted, file changed during quota check: "
                    "project=%s file=%s",
                    project_id,
                    file_path,
                )
        else:
            # 无记账清单可用（记录失败等罕见场景）：退化为无守卫直接恢复
            logger.warning(
                "Quota rollback without version guard (no edit-log manifest): "
                "project=%s file=%s",
                project_id,
                file_path,
            )
            await asyncio.to_thread(
                project.restore_project_file, file_path, before_content
            )
        if edit_op_id and actor_user_id:
            await asyncio.to_thread(
                edit_log.discard_op, edit_op_id, project_id, actor_user_id
            )
    except Exception:
        logger.exception(
            "Quota rollback failed after oversized write: project=%s file=%s",
            project_id,
            file_path,
        )
        await asyncio.to_thread(refresh_owner_usage_after_write, project_id)
        raise _http(
            500,
            "quota_exceeded",
            "Project space quota exceeded; automatic rollback failed",
        )
    await asyncio.to_thread(refresh_owner_usage_after_write, project_id)
    if rollback_skipped_by_conflict:
        # 覆盖已放弃、超限写入仍在磁盘上：如实上报，不得谎称已回滚
        raise _http(
            500,
            "quota_exceeded",
            "Project space quota exceeded; the file changed during the "
            "quota check, so automatic rollback was skipped",
        )
    await asyncio.to_thread(refresh_owner_usage_after_write, project_id)
    raise _http(
        413,
        "quota_exceeded",
        "Project space quota exceeded; write was rolled back",
    )


# ── 权限协作（P4：协作者管理 + 转交） ──────────────────────────────────────
def _require_collab_role(role: str) -> None:
    """协作者角色白名单校验。"""
    if role not in COLLABORATOR_ROLES:
        raise _http(
            400,
            "invalid_role",
            f"Invalid collaborator role (must be one of {', '.join(COLLABORATOR_ROLES)})",
        )


def _collaborator_view(row: dict) -> dict:
    """把 project_collaborators 行（已 JOIN users）转成前端视图。"""
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "email": row.get("email", ""),
        "role": row["role"],
        "created_at": row["created_at"],
    }


def _get_collaborator_row(project_id: str, collab_user_id: str) -> dict | None:
    """按用户 id 查协作者行（含用户名/邮箱）。"""
    return db_query_one(
        "SELECT pc.user_id, pc.role, pc.created_at, u.username, u.email "
        "FROM project_collaborators pc JOIN users u ON u.id = pc.user_id "
        "WHERE pc.project_id = ? AND pc.user_id = ?",
        (project_id, collab_user_id),
    )


async def list_permissions(user_id: str, project_id: str) -> dict:
    """项目权限列表（owner + 协作者）。仅 owner 可查看管理界面。"""
    access = await _require_owner(user_id, project_id)
    owner_id = access["owner_id"]

    def _load_permissions() -> tuple[list[dict], dict | None]:
        collabs = db_query_all(
            "SELECT pc.user_id, pc.role, pc.created_at, u.username, u.email "
            "FROM project_collaborators pc JOIN users u ON u.id = pc.user_id "
            "WHERE pc.project_id = ? ORDER BY pc.created_at, pc.user_id",
            (project_id,),
        )
        return collabs, auth_core.get_user_by_id(owner_id)

    collabs, owner = await asyncio.to_thread(_load_permissions)
    return {
        "project_id": project_id,
        "owner": {
            "user_id": owner_id,
            "username": owner["username"] if owner else "",
            "email": owner.get("email", "") if owner else "",
        },
        "collaborators": [_collaborator_view(c) for c in collabs],
    }


async def add_collaborator(
    user_id: str, project_id: str, username: str, role: str
) -> dict:
    """按用户名添加协作者；已存在则更新角色（owner 专属）。"""
    await _require_owner(user_id, project_id)
    _require_collab_role(role)
    target = await asyncio.to_thread(auth_core.get_user_by_username, username)
    if target is None:
        raise _http(404, "user_not_found", f"User not found: {username}")
    if target["status"] != "active":
        raise _http(
            400, "invalid_user", "Only active users can be added as collaborators"
        )
    if target["id"] == user_id:
        raise _http(400, "invalid_user", "The project owner cannot be a collaborator")
    now = utcnow()
    await asyncio.to_thread(
        db_execute,
        "INSERT INTO project_collaborators (project_id, user_id, role, created_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(project_id, user_id) DO UPDATE SET role = excluded.role",
        (project_id, target["id"], role, now),
    )
    logger.info(
        "User %s (%s) added/updated as '%s' of project %s",
        target["username"],
        target["id"],
        role,
        project_id,
    )
    row = await asyncio.to_thread(_get_collaborator_row, project_id, target["id"])
    return {"collaborator": _collaborator_view(row)}


async def update_collaborator(
    user_id: str, project_id: str, collab_user_id: str, role: str
) -> dict:
    """修改协作者角色（owner 专属）。"""
    await _require_owner(user_id, project_id)
    _require_collab_role(role)
    if collab_user_id == user_id:
        raise _http(
            400, "invalid_user", "The project owner's role cannot be changed here"
        )
    row = await asyncio.to_thread(_get_collaborator_row, project_id, collab_user_id)
    if row is None:
        raise _http(404, "not_found", "Collaborator not found")
    await asyncio.to_thread(
        db_execute,
        "UPDATE project_collaborators SET role = ? WHERE project_id = ? AND user_id = ?",
        (role, project_id, collab_user_id),
    )
    logger.info(
        "Project %s collaborator %s role -> %s", project_id, collab_user_id, role
    )
    updated = await asyncio.to_thread(_get_collaborator_row, project_id, collab_user_id)
    return {"collaborator": _collaborator_view(updated)}


async def remove_collaborator(
    user_id: str, project_id: str, collab_user_id: str
) -> dict:
    """移除协作者（owner 专属）。"""
    await _require_owner(user_id, project_id)
    if collab_user_id == user_id:
        raise _http(
            400, "invalid_user", "The project owner cannot be removed as a collaborator"
        )
    row = await asyncio.to_thread(_get_collaborator_row, project_id, collab_user_id)
    if row is None:
        raise _http(404, "not_found", "Collaborator not found")
    await asyncio.to_thread(
        db_execute,
        "DELETE FROM project_collaborators WHERE project_id = ? AND user_id = ?",
        (project_id, collab_user_id),
    )
    logger.info("Project %s collaborator %s removed", project_id, collab_user_id)
    return {"status": "ok", "project_id": project_id, "user_id": collab_user_id}


def _transfer_update_row_sync(
    from_owner: str,
    to_owner: str,
    project_id: str,
    base_name: str,
    now: str,
) -> str:
    """守卫式 UPDATE 项目归属（DB 先行，运行于工作线程）。

    仅当项目仍为 active 且归属未变时生效；同名唯一索引冲突（预检后的
    并发窗口）重取名称重试。返回最终项目名；项目已被删除/恢复/转交时
    抛 404，连续命名冲突抛 409。"""
    for _ in range(3):
        new_name = next_available_name(to_owner, base_name)
        try:
            rowcount = db_execute(
                "UPDATE projects SET owner_id = ?, name = ?, updated_at = ? "
                "WHERE id = ? AND status = 'active' AND owner_id = ?",
                (to_owner, new_name, now, project_id, from_owner),
            )
        except sqlite3.IntegrityError:
            continue  # 并发命中唯一命名，重取名称重试
        if rowcount != 1:
            # 项目已被删除/恢复/转交，当前状态不再可转交
            raise _http(404, "not_found", "Project not found")
        return new_name
    raise _http(409, "file_conflict", "Project rename conflict; try again")


async def transfer_project(
    user_id: str,
    project_id: str,
    username: str,
    keep_access: bool = False,
) -> dict:
    """转交项目 owner 给其他用户。

    - 项目目录沿 `data/users/<owner_id>/projects/<project_id>/` 规范迁移到新 owner 空间；
    - 若新 owner 已有同名活动项目，自动生成 `xx (2)`（沿用唯一命名逻辑）；
    - keep_access=True：原 owner 转交后保留为 viewer；
    - keep_access=False：原 owner 直接退出协作；
    - 已打开该项目的 workspace 绑定实时更新（owner_id / root_path / 重开文件树）。
    """
    await _require_owner(user_id, project_id)

    target = await asyncio.to_thread(auth_core.get_user_by_username, username)
    if target is None:
        raise _http(404, "user_not_found", f"User not found: {username}")
    if target["status"] != "active":
        raise _http(400, "invalid_user", "Only active users can receive a project")
    if target["id"] == user_id:
        raise _http(400, "invalid_user", "The project owner already owns this project")

    row = await asyncio.to_thread(
        db_query_one, "SELECT * FROM projects WHERE id = ?", (project_id,)
    )
    if row is None or row["status"] != "active":
        raise _http(404, "not_found", "Project not found")

    # 接收方配额校验：转交是项目空间的一条 ingress 路径，超限拒绝（与恢复一致）
    await asyncio.to_thread(enforce_project_quota, target["id"], int(row["size_bytes"]))

    old_root = userspace.project_dir(user_id, project_id)
    new_root = userspace.project_dir(target["id"], project_id)

    # 守卫式 UPDATE（DB 先行）：仅当项目仍为 active 且归属未变时生效——
    # 与删除/恢复并发时拒绝转交，避免把已入回收站的项目改归属；
    # 同名唯一索引冲突（预检后的并发窗口）则重取名称重试。
    now = utcnow()
    new_name = await asyncio.to_thread(
        _transfer_update_row_sync,
        user_id,
        target["id"],
        project_id,
        row["name"],
        now,
    )

    # 目录迁移后置：UPDATE 成功而迁移失败时回滚 DB（补偿），避免
    # "文件已搬 / 库未改"的失联中间态
    try:
        await asyncio.to_thread(_move_project_dir, old_root, new_root)
    except Exception:
        await asyncio.to_thread(
            db_execute,
            "UPDATE projects SET owner_id = ?, name = ?, updated_at = ? "
            "WHERE id = ? AND owner_id = ?",
            (user_id, row["name"], utcnow(), project_id, target["id"]),
        )
        logger.exception(
            "Transfer directory move failed, reverted DB ownership: project=%s",
            project_id,
        )
        raise

    # 新 owner 此前若是协作者，转正后移除其协作者行；按 keep_access 处置原 owner
    def _update_collab_rows() -> None:
        db_execute(
            "DELETE FROM project_collaborators WHERE project_id = ? AND user_id = ?",
            (project_id, target["id"]),
        )
        if keep_access:
            db_execute(
                "INSERT INTO project_collaborators (project_id, user_id, role, created_at) "
                "VALUES (?, ?, 'viewer', ?) "
                "ON CONFLICT(project_id, user_id) DO UPDATE SET role = 'viewer'",
                (project_id, user_id, now),
            )
        else:
            db_execute(
                "DELETE FROM project_collaborators WHERE project_id = ? AND user_id = ?",
                (project_id, user_id),
            )

    await asyncio.to_thread(_update_collab_rows)

    await _refresh_workspace_bindings(project_id, target["id"], str(new_root))
    logger.info(
        "Project %s transferred from %s to %s (keep_access=%s)",
        project_id,
        user_id,
        target["id"],
        keep_access,
    )
    return {
        "status": "ok",
        "project_id": project_id,
        "new_owner_id": target["id"],
        "new_owner_username": target["username"],
        "new_name": new_name,
        "keep_access": keep_access,
    }


def _move_project_dir(old_root: Path, new_root: Path) -> None:
    """把项目目录迁移到新 owner 空间（同分区 rename，跨分区复制兜底）。"""
    new_root.parent.mkdir(parents=True, exist_ok=True)
    if old_root == new_root:
        return
    if new_root.exists():
        # 目标目录已存在：极端残留场景，拒绝覆盖以确保数据安全
        raise _http(
            500,
            "transfer_failed",
            "Destination project directory already exists; transfer aborted",
        )
    if old_root.exists():
        shutil.move(str(old_root), str(new_root))
    else:
        new_root.mkdir(parents=True, exist_ok=True)


async def _refresh_workspace_bindings(
    project_id: str, new_owner_id: str, new_root: str
) -> None:
    """转交后同步在内存中仍绑定该项目的 workspace（owner / 根路径 / 文件树）。

    v2 P7 多实例：`workspace_bindings` 行是全副本共享的权威，先按
    project_id 一次性刷新全部行（覆盖其他副本持有的 workspace——本进程
    `list_workspaces()` 只能看到本地实例），再对本副本内存中的 workspace
    重开项目并同步进程内缓存。
    """
    wm = get_workspace_manager()
    wm.refresh_project_bindings(project_id, owner_id=new_owner_id, root_path=new_root)
    for info in wm.list_workspaces():
        binding = wm.get_binding(info["workspace_id"])
        if not binding or binding.get("project_id") != project_id:
            continue
        project = wm.get(info["workspace_id"])
        if project is not None:
            try:
                await asyncio.to_thread(project.open, new_root)
            except Exception:
                logger.exception(
                    "Failed to re-open workspace %s after transfer",
                    info["workspace_id"],
                )
        wm.bind(
            info["workspace_id"],
            user_id=binding.get("user_id"),
            project_id=project_id,
            owner_id=new_owner_id,
            root_path=new_root,
        )
