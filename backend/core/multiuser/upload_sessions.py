"""
多用户模式大文件分片上传会话核心（纯同步函数层，v2 P5，方案 §10）。

职责：
    1. 会话生命周期：init（manifest 结构预检 + 剩余配额预检 + staging 目录
       创建）→ part 幂等落盘 → status 位图 → complete 校验拼装落位 →
       abort 中止清理
    2. TTL：从最后活动（last_activity_at，分片与状态查询成功即续期）算起，
       超过 upload_session_ttl_hours 由维护循环清理会话行与 staging 目录，
       并顺带清扫"有标记无数据行"的孤儿目录
    3. 安全：会话归属校验（只能操作自己的 sid，他人一律 404 不泄露存在性）、
       manifest 相对路径复用解压管线的穿越校验规则、分片头 X-Part-MD5 校验、
       complete 数量齐全 + 逐文件全量 MD5 复核

约定：
    - 本模块为同步核心；路由/服务层必须经 asyncio.to_thread 调度
    - 分片阶段不占配额：init 只预检剩余配额 ≥ total_bytes；init 与 complete
      之间的并发竞争由 complete 的二次复核兜底，超限即整包拒绝并清场
    - 错误统一用 upload.ArchiveError 表达（稳定错误码），由服务层映射 HTTP：
      upload_session_not_found → 404、upload_session_expired → 410、
      其余 → 400（quota_exceeded / upload_too_large 类 → 413）

staging 布局：
    <staging_root>/<sid>/
        ├── .upload_session          # 会话目录标记（孤儿识别 / 通用清理豁免）
        ├── parts/<file>-<chunk>.part
        └── extracted/               # complete 拼装工作区（压缩包/目录布局）
"""

import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path

from backend.core.config import get_config
from backend.core.logger import get_logger

from . import userspace
from .db import db_execute, db_query_one, utcnow
from .quota import (
    create_project_row,
    dir_size,
    effective_project_quota,
    enforce_project_quota,
    project_usage_for_user,
)
from .upload import (
    ArchiveError,
    build_limits,
    extract_archive,
    finalize_directory_layout,
    move_into_user_space,
    validate_member_path,
    validate_no_case_conflicts,
)

logger = get_logger("multiuser.upload_sessions")

# 会话 staging 目录标记文件：区分分片会话目录与一次性上传暂存目录。
# 通用 clean_staging（mtime 兜底）跳过带标记的目录，其生命周期完全由
# 本模块的 TTL 清理 / 孤儿清扫接管。
SESSION_MARKER_FILENAME = ".upload_session"

# kind → 压缩包扩展名白名单（directory 为逐文件直传，无扩展名约束）。
# rar 虽不在方案 §10.1 的三 kind 枚举内，但 P2 已交付 rar 解压管线，
# 前端压缩包直传统一走分片后若剔除 rar 将造成功能回退，故一并纳入
# 白名单（拼装后走同一条 extract_archive 链路，偏离已记录实施说明）。
_ARCHIVE_KIND_SUFFIXES = {"zip": ".zip", "7z": ".7z", "rar": ".rar"}
ALL_KINDS = ("zip", "7z", "rar", "directory")

_MD5_RE = re.compile(r"^[0-9a-f]{32}$")
_PART_NAME_RE = re.compile(r"^(\d+)-(\d+)\.part$")


# ── 会话行与 staging 目录 ────────────────────────────────────────
def _session_dir(sid: str) -> Path:
    """返回会话 staging 目录（sid 为服务端生成的 UUID hex）。"""
    return userspace.get_staging_root() / sid


def _parts_dir(session_dir: Path) -> Path:
    return session_dir / "parts"


def _write_marker(session_dir: Path) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / SESSION_MARKER_FILENAME).write_text(
        json.dumps({"id": session_dir.name}), encoding="utf-8"
    )


def is_session_staging_dir(path: Path) -> bool:
    """判断 staging 子目录是否为分片上传会话目录（供通用清理豁免/孤儿清扫）。"""
    return (path / SESSION_MARKER_FILENAME).is_file()


def _get_session_row(user_id: str, sid: str) -> dict | None:
    """按归属查询会话行；他人 sid 与不存在 sid 一律 None（404 不泄露存在性）。"""
    return db_query_one(
        "SELECT * FROM upload_sessions WHERE id = ? AND user_id = ?",
        (sid, user_id),
    )


def _expired_cutoff() -> str:
    ttl_hours = get_config().multi_user.upload_session_ttl_hours
    from datetime import datetime, timedelta, timezone

    return (
        datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=ttl_hours)
    ).isoformat()


def _purge_session(sid: str) -> None:
    """删除会话行与 staging 目录（幂等；顺序先目录后行，崩溃残留交孤儿清扫）。"""
    shutil.rmtree(_session_dir(sid), ignore_errors=True)
    db_execute("DELETE FROM upload_sessions WHERE id = ?", (sid,))


def _require_active_session(user_id: str, sid: str) -> tuple[dict, Path]:
    """归属 + TTL 校验：返回 (row, session_dir)。

    - 不存在 / 他人会话 → ArchiveError('upload_session_not_found')（404）
    - 已过期 → 就地清理并抛 ArchiveError('upload_session_expired')（410）
    """
    row = _get_session_row(user_id, sid)
    if row is None:
        raise ArchiveError("upload_session_not_found", "Upload session not found")
    if row["last_activity_at"] < _expired_cutoff():
        logger.info(
            "Upload session %s expired (user %s); purging on access", sid, user_id
        )
        _purge_session(sid)
        raise ArchiveError("upload_session_expired", "Upload session has expired")
    return row, _session_dir(sid)


def _touch(sid: str, session_dir: Path) -> None:
    """续期会话：刷新 last_activity_at 与 staging 目录 mtime（双口径一致）。"""
    db_execute(
        "UPDATE upload_sessions SET last_activity_at = ? WHERE id = ?",
        (utcnow(), sid),
    )
    try:
        os.utime(session_dir, None)
    except OSError:
        pass


# ── init ────────────────────────────────────────────────────────
def create_session(
    user_id: str, name: str, kind: str, total_bytes: int, files: list[dict]
) -> dict:
    """创建分片上传会话：manifest 结构预检 + 剩余配额预检 + staging 目录。

    files 为已过 Pydantic 形状校验的条目列表（path/size/md5/chunks）。
    返回会话行视图（session_id / chunk_size / status 等）。
    业务失败抛 ArchiveError（服务层映射 HTTP）。
    """
    cfg = get_config().multi_user
    if kind not in ALL_KINDS:
        raise ArchiveError("invalid_request", f"Unsupported upload kind: {kind}")
    chunk_size = cfg.chunk_part_max_mb * 1024 * 1024

    # 扩展名白名单：压缩包 kind 的唯一清单条目必须匹配对应扩展名
    if kind in _ARCHIVE_KIND_SUFFIXES:
        if len(files) != 1:
            raise ArchiveError(
                "invalid_request",
                f"Archive upload kind '{kind}' expects exactly one file entry",
            )
        suffix = _ARCHIVE_KIND_SUFFIXES[kind]
        if not files[0]["path"].lower().endswith(suffix):
            raise ArchiveError(
                "invalid_archive",
                f"Upload kind '{kind}' requires a {suffix} file",
            )

    # manifest 路径校验：复用解压管线的穿越/绝对路径/'..' 规则
    paths: list[str] = []
    for entry in files:
        rel = validate_member_path(entry["path"])
        if rel.endswith("/"):
            raise ArchiveError(
                "invalid_request", f"Manifest entry must be a file path: {rel}"
            )
        paths.append(rel)
    if len(set(paths)) != len(paths):
        raise ArchiveError("invalid_request", "Duplicate manifest entry path")
    if kind == "directory":
        # 大小写不敏感重名（含目录前缀冲突）与压缩包管线同口径
        validate_no_case_conflicts(paths)

    # 结构与限额校验
    if total_bytes > cfg.max_upload_archive_mb * 1024 * 1024:
        raise ArchiveError(
            "upload_too_large",
            f"Upload exceeds archive size limit ({cfg.max_upload_archive_mb} MB)",
        )
    if len(files) > cfg.max_upload_files:
        raise ArchiveError(
            "upload_too_large",
            f"Too many files (limit {cfg.max_upload_files})",
        )
    declared_total = 0
    for entry, rel in zip(files, paths):
        size = int(entry["size"])
        if size > cfg.max_upload_file_size_mb * 1024 * 1024:
            raise ArchiveError(
                "upload_too_large",
                f"Single file exceeds size limit: {rel}",
            )
        expected_chunks = (size + chunk_size - 1) // chunk_size
        if int(entry["chunks"]) != expected_chunks:
            raise ArchiveError(
                "invalid_request",
                f"Chunk count mismatch for {rel}: expected {expected_chunks}",
            )
        if not _MD5_RE.fullmatch(str(entry["md5"])):
            raise ArchiveError(
                "invalid_request", f"Invalid md5 for manifest entry: {rel}"
            )
        declared_total += size
    if declared_total != int(total_bytes):
        raise ArchiveError(
            "invalid_request",
            f"total_bytes mismatch: files sum to {declared_total}",
        )

    # 剩余配额预检（分片阶段不占配额，仅做准入；竞争由 complete 二次复核兜底）
    remaining = effective_project_quota(user_id) - project_usage_for_user(user_id)
    if int(total_bytes) > remaining:
        raise ArchiveError(
            "quota_exceeded",
            "Upload exceeds remaining project quota",
        )

    sid = uuid.uuid4().hex
    now = utcnow()
    manifest = {
        "chunk_size": chunk_size,
        "files": [
            {
                "path": rel,
                "size": int(entry["size"]),
                "md5": str(entry["md5"]).lower(),
                "chunks": int(entry["chunks"]),
            }
            for entry, rel in zip(files, paths)
        ],
    }
    db_execute(
        "INSERT INTO upload_sessions (id, user_id, name, kind, total_bytes, "
        "file_count, status, manifest, created_at, last_activity_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'uploading', ?, ?, ?)",
        (
            sid,
            user_id,
            name,
            kind,
            int(total_bytes),
            len(files),
            json.dumps(manifest, ensure_ascii=False),
            now,
            now,
        ),
    )
    # 先建行后建目录：崩溃窗口只可能留下"有行无目录"（part 落盘时自愈补建），
    # 不会产生误判为孤儿的"有目录无行"
    session_dir = _session_dir(sid)
    _write_marker(session_dir)
    _parts_dir(session_dir).mkdir(parents=True, exist_ok=True)
    logger.info(
        "Upload session created: id=%s user=%s kind=%s files=%d total=%d",
        sid,
        user_id,
        kind,
        len(files),
        int(total_bytes),
    )
    return {
        "session_id": sid,
        "name": name,
        "kind": kind,
        "total_bytes": int(total_bytes),
        "chunk_size": chunk_size,
        "status": "uploading",
    }


# ── part / status ───────────────────────────────────────────────
def store_part(
    user_id: str,
    sid: str,
    file_index: int,
    chunk_index: int,
    data: bytes,
    expected_md5: str,
) -> dict:
    """校验并幂等落盘单个分片；成功即续期会话。

    - 归属 / TTL / 状态（仅 uploading 接受分片）校验；
    - 索引越界、体积超 chunk_part_max_mb、X-Part-MD5 不符均拒绝；
    - 同 (file, chunk) 重复上传直接覆盖（幂等，支持客户端重试）。
    """
    row, session_dir = _require_active_session(user_id, sid)
    if row["status"] != "uploading":
        raise ArchiveError("invalid_state", "Upload session is not accepting parts")
    manifest = json.loads(row["manifest"])
    files = manifest["files"]
    if file_index < 0 or file_index >= len(files):
        raise ArchiveError("invalid_request", "file_index out of range")
    entry = files[file_index]
    if chunk_index < 0 or chunk_index >= entry["chunks"]:
        raise ArchiveError("invalid_request", "chunk_index out of range")

    chunk_size = int(manifest["chunk_size"])
    if len(data) > chunk_size:
        raise ArchiveError(
            "upload_too_large",
            f"Part exceeds chunk size limit ({chunk_size} bytes)",
        )
    digest = hashlib.md5(data).hexdigest()
    if digest != str(expected_md5).strip().lower():
        raise ArchiveError(
            "part_md5_mismatch", "Part body does not match X-Part-MD5 header"
        )

    parts = _parts_dir(session_dir)
    parts.mkdir(parents=True, exist_ok=True)
    # 原子落盘：先写唯一临时文件再 os.replace，避免并发重传同一
    # (file, chunk) 时交错写产生撕裂分片（临时名不匹配 _PART_NAME_RE，
    # 位图扫描与 complete 校验均不会误认；崩溃残留随目录一并回收）
    final_path = parts / f"{file_index}-{chunk_index}.part"
    tmp_path = parts / f"{file_index}-{chunk_index}.{uuid.uuid4().hex}.tmp"
    try:
        tmp_path.write_bytes(data)
        os.replace(tmp_path, final_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    _touch(sid, session_dir)
    return {
        "received": True,
        "file_index": file_index,
        "chunk_index": chunk_index,
    }


def _scan_received_chunks(parts: Path) -> dict[int, set[int]]:
    """扫描 parts 目录，返回 {file_index: {received chunk_index}}。"""
    received: dict[int, set[int]] = {}
    if not parts.is_dir():
        return received
    with os.scandir(parts) as it:
        for entry in it:
            m = _PART_NAME_RE.match(entry.name)
            if m:
                received.setdefault(int(m.group(1)), set()).add(int(m.group(2)))
    return received


def session_status(user_id: str, sid: str) -> dict:
    """返回会话状态与每文件已收分块位图（前端据此断点续传）；同时续期。"""
    row, session_dir = _require_active_session(user_id, sid)
    manifest = json.loads(row["manifest"])
    chunk_size = int(manifest["chunk_size"])
    received = _scan_received_chunks(_parts_dir(session_dir))
    files_view: list[dict] = []
    received_bytes = 0
    for i, entry in enumerate(manifest["files"]):
        got = received.get(i, set())
        bitmap = [c in got for c in range(entry["chunks"])]
        files_view.append(
            {
                "index": i,
                "path": entry["path"],
                "size": entry["size"],
                "chunks_total": entry["chunks"],
                "received_count": len(got),
                "bitmap": bitmap,
            }
        )
        for c in got:
            received_bytes += min(chunk_size, entry["size"] - c * chunk_size)
    _touch(sid, session_dir)
    return {
        "session_id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "status": row["status"],
        "total_bytes": int(row["total_bytes"]),
        "chunk_size": chunk_size,
        "received_bytes": received_bytes,
        "files": files_view,
    }


# ── complete ────────────────────────────────────────────────────
def _verify_all_parts(row: dict, session_dir: Path) -> None:
    """complete 前置校验：分块数量齐全 + 逐文件流式全量 MD5 复核。

    失败抛 part_missing / part_md5_mismatch（可续传：保留会话现场）。"""
    manifest = json.loads(row["manifest"])
    parts = _parts_dir(session_dir)
    received = _scan_received_chunks(parts)
    for i, entry in enumerate(manifest["files"]):
        got = received.get(i, set())
        missing = [c for c in range(entry["chunks"]) if c not in got]
        if missing:
            raise ArchiveError(
                "part_missing",
                f"Missing chunks for {entry['path']}: {missing[:5]}",
            )
    # 全量复核（方案 §10.1：数量与 md5 全量复核）。
    # 分片文件在读盘中途消失（并发 abort / 维护 TTL 清场等竞态）按
    # part_missing 表达，避免裸 OSError 逃逸成 500
    for i, entry in enumerate(manifest["files"]):
        h = hashlib.md5()
        for c in range(entry["chunks"]):
            try:
                with open(parts / f"{i}-{c}.part", "rb") as src:
                    while True:
                        block = src.read(1 << 20)
                        if not block:
                            break
                        h.update(block)
            except OSError as e:
                raise ArchiveError(
                    "part_missing",
                    f"Part file vanished during verification: {i}-{c}",
                ) from e
        if h.hexdigest() != entry["md5"]:
            raise ArchiveError(
                "part_md5_mismatch",
                f"Assembled content md5 mismatch for {entry['path']}",
            )


def _concatenate_chunks(parts: Path, file_index: int, chunks: int, dst) -> None:
    """按 chunk_index 升序把分片流式拼接到目标文件对象。

    分片文件中途消失（并发 abort / TTL 清场竞态）映射为 part_missing，
    由 complete 的整包拒绝清场路径给出干净错误码而非 500。"""
    for c in range(chunks):
        try:
            with open(parts / f"{file_index}-{c}.part", "rb") as src:
                shutil.copyfileobj(src, dst, length=1 << 20)
        except OSError as e:
            raise ArchiveError(
                "part_missing",
                f"Part file vanished during assembly: {file_index}-{c}",
            ) from e


def _assemble_and_place(
    user_id: str,
    name: str,
    kind: str,
    manifest: dict,
    session_dir: Path,
    project_id: str,
) -> dict:
    """拼装 → 解压/布局校验 → 配额二次复核 → 落位 → 建项目行。

    成功时清理 staging 与会话行并返回 projects 行；任何失败向上抛出
    （调用方负责整包清场：staging + 会话行 + 用户空间残留）。"""
    files = manifest["files"]
    parts = _parts_dir(session_dir)
    extract_dir = session_dir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=False)

    remaining = effective_project_quota(user_id) - project_usage_for_user(user_id)
    limits = build_limits(remaining)

    if kind in _ARCHIVE_KIND_SUFFIXES:
        # 压缩包 kind：拼出压缩包后走既有 extract_archive 全链路
        entry = files[0]
        archive_path = session_dir / Path(entry["path"]).name
        with open(archive_path, "wb") as dst:
            _concatenate_chunks(parts, 0, entry["chunks"], dst)
        extracted = extract_archive(archive_path, extract_dir, limits)
    else:
        # directory kind：按 manifest 拼出 extracted 布局后走同一校验链
        for i, entry in enumerate(files):
            target = (extract_dir / entry["path"]).resolve()
            if not target.is_relative_to(extract_dir.resolve()):
                raise ArchiveError(
                    "archive_unsafe", "Manifest path escapes extraction root"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as dst:
                _concatenate_chunks(parts, i, entry["chunks"], dst)
        extracted = finalize_directory_layout(extract_dir, limits)

    # 配额二次复核（init 预检与 complete 之间竞争的兜底；失败整包拒绝）
    enforce_project_quota(user_id, extracted.size)
    dest = move_into_user_space(extract_dir, extracted.root, user_id, project_id)
    size = dir_size(dest)
    enforce_project_quota(user_id, size)
    project_row = create_project_row(user_id, name, size, project_id=project_id)

    # 成功清场：staging 目录 + 会话行
    shutil.rmtree(session_dir, ignore_errors=True)
    db_execute("DELETE FROM upload_sessions WHERE id = ?", (session_dir.name,))
    logger.info(
        "Upload session completed: id=%s user=%s project=%s size=%d",
        session_dir.name,
        user_id,
        project_id,
        size,
    )
    return project_row


def complete_session(user_id: str, sid: str) -> dict:
    """完成会话：校验齐全 → 拼装落位 → 返回 projects 行。

    - 并发/重复 complete 由 uploading → assembling 条件更新拦截（409 语义）；
    - 分块缺失 / 全量 MD5 不符：恢复 uploading，保留现场供断点续传；
    - 进入拼装后的任何失败（解压拒绝、配额二次复核超限、落位异常等）：
      整包拒绝——清理用户空间残留、staging 与会话行。
    """
    row, session_dir = _require_active_session(user_id, sid)
    updated = db_execute(
        "UPDATE upload_sessions SET status = 'assembling', last_activity_at = ? "
        "WHERE id = ? AND status = 'uploading'",
        (utcnow(), sid),
    )
    if updated != 1:
        raise ArchiveError(
            "invalid_state", "Upload session is being completed or already finished"
        )
    try:
        _verify_all_parts(row, session_dir)
    except ArchiveError:
        # 可续传失败（part_missing / part_md5_mismatch）：恢复 uploading，
        # 客户端按 status 位图补传后重试 complete
        db_execute(
            "UPDATE upload_sessions SET status = 'uploading', "
            "last_activity_at = ? WHERE id = ?",
            (utcnow(), sid),
        )
        raise
    project_id = uuid.uuid4().hex
    try:
        return _assemble_and_place(
            user_id,
            row["name"],
            row["kind"],
            json.loads(row["manifest"]),
            session_dir,
            project_id,
        )
    except BaseException:
        # 整包拒绝清场：用户空间残留（确定性路径）+ staging + 会话行
        dest = userspace.project_dir(user_id, project_id)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        _purge_session(sid)
        raise


# ── abort ───────────────────────────────────────────────────────
def abort_session(user_id: str, sid: str) -> None:
    """中止会话并清理会话行与 staging 目录。"""
    _row, _ = _require_active_session(user_id, sid)
    _purge_session(sid)
    logger.info("Upload session aborted: id=%s user=%s", sid, user_id)


# ── 维护：TTL 过期清理 + 孤儿清扫（v2 P5）────────────────────────
def purge_expired_upload_sessions() -> int:
    """清理过期会话（行 + staging 目录）与"有标记无数据行"的孤儿目录。

    由维护循环周期调用；幂等。返回清理的目录数（含过期会话与孤儿）。"""
    from .db import db_query_all

    cutoff = _expired_cutoff()
    removed = 0
    for row in db_query_all(
        "SELECT id FROM upload_sessions WHERE last_activity_at < ?", (cutoff,)
    ):
        removed += 1
        _purge_session(row["id"])
    # 孤儿清扫：带会话标记但已无数据行的 staging 目录（崩溃残留等）
    staging = userspace.get_staging_root()
    if staging.is_dir():
        for child in staging.iterdir():
            try:
                if not child.is_dir() or not is_session_staging_dir(child):
                    continue
                exists = db_query_one(
                    "SELECT 1 FROM upload_sessions WHERE id = ?", (child.name,)
                )
                if exists is None:
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    if removed:
        logger.info("Purged %d expired/orphan upload session dir(s)", removed)
    return removed
