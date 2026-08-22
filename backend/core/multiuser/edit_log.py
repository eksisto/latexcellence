"""
多用户反向补丁编辑日志（v2 P3，方案 §8）。

职责：
    1. 写入记账：多用户模式下，所有经 LatexProject.apply_change /
       apply_whole_files 落盘的项目文件写操作，按 (project, actor) 记一条
       操作索引（workspace_edit_ops 表）+ 磁盘全文快照
       （<users_root>/<owner_id>/.edit_logs/<project_id>/<op_id>/）
    2. 反向补丁撤销/重做：LIFO 按操作逆序；撤销前校验组内每文件当前 MD5 ==
       after_md5（冲突即拒绝，绝不静默覆盖他人修改），恢复后不超项目配额；
       redo 对称重放
    3. 生命周期：每 (project, actor) 条数上限淘汰 + TTL 过期清理 +
       项目永久删除时级联清理

设计要点：
    - 单用户模式完全不落盘（BackupManager 内存栈行为保持现状，两套语义互不
      干扰）；actor 取自 userspace.current_user_id ContextVar，无当前用户
      （后台任务 / 绕过路由直调）时不记账
    - 快照 owner 维度放置在用户空间 .edit_logs/ 下：不进项目目录、不进下载包、
      不占衍生数据配额（方案 §16 备份口径）
    - 单 op 快照总量超过 edit_log_max_op_mb 时只记索引（non_undoable=1），
      不写快照文件——撤销列表/状态接口展示"过大不可撤"
    - 本模块为同步核心；FastAPI 层经 asyncio.to_thread 调度
    - 记账失败只打日志不抛错：编辑日志是辅助能力，绝不能影响写入主流程
"""

import hashlib
import json
import shutil
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

from fastapi import HTTPException

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.utils.file_ops import atomic_write, compute_md5
from backend.utils.path_guard import resolve_project_path

# 同包内使用相对导入（AGENTS.md 规范，与 maintenance.py 风格一致）
from . import userspace
from .db import (
    db_count,
    db_execute,
    db_query_all,
    db_query_one,
    transaction,
    utcnow,
)

logger = get_logger("multiuser.edit_log")

# 用户空间内快照根目录名（owner 维度：<users_root>/<owner_id>/.edit_logs/）
EDIT_LOGS_DIRNAME = ".edit_logs"

# ── 请求级上下文 ──────────────────────────────────────────────────────────
# 撤销/重做恢复写回期间抑制记账（否则回滚本身会被记成新 op，形成自环）
_suppress_recording: ContextVar[bool] = ContextVar(
    "edit_log_suppress_recording", default=False
)
# 写入来源模块标签（可选诊断信息；由调用方通过 source_scope 设置）
_source_module: ContextVar[str] = ContextVar("edit_log_source_module", default="")
# 显式 op 分组：同一逻辑动作的多次写入共享同一 op_group_id，一次撤销整组回滚。
# 缺省时每条 op 自成一组。
_op_group: ContextVar[str | None] = ContextVar("edit_log_op_group", default=None)

# op 状态机：active --undo--> undone --redo--> active；failed 终态（快照损坏/
# 文件缺失等不可恢复错误，仅防御性出现）
STATUS_ACTIVE = "active"
STATUS_UNDONE = "undone"
STATUS_FAILED = "failed"


def _http(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message})


@contextmanager
def source_scope(name: str) -> Iterator[None]:
    """标记当前上下文内写入的来源模块（诊断用，如 "writing_chat"）。"""
    token = _source_module.set(name)
    try:
        yield
    finally:
        _source_module.reset(token)


@contextmanager
def suppress_recording() -> Iterator[None]:
    """抑制记账：撤销/重做的恢复写回、配额直接恢复等内部路径使用。"""
    token = _suppress_recording.set(True)
    try:
        yield
    finally:
        _suppress_recording.reset(token)


@contextmanager
def op_group_scope(group_id: str | None = None) -> Iterator[str]:
    """把作用域内的多次写入归入同一 op_group（一次撤销整组回滚）。

    group_id 缺省时自动生成；嵌套作用沿用最外层分组。
    """
    if _op_group.get():
        # 已处于分组中（外层负责 reset）：直接复用
        yield _op_group.get()
        return
    gid = group_id or uuid.uuid4().hex
    token = _op_group.set(gid)
    try:
        yield gid
    finally:
        _op_group.reset(token)


# ── 快照目录布局 ──────────────────────────────────────────────────────────
def edit_logs_root(owner_id: str) -> Path:
    """返回指定用户的编辑日志根目录（user_root 内含 id 防穿越校验）。"""
    return userspace.user_root(owner_id) / EDIT_LOGS_DIRNAME


def project_edit_logs_dir(owner_id: str, project_id: str) -> Path:
    """返回指定项目在其 owner 空间下的编辑日志目录。"""
    return edit_logs_root(owner_id) / userspace._validate_id_component(
        "project_id", project_id
    )


def op_snapshot_dir(owner_id: str, project_id: str, op_id: str) -> Path:
    """返回单个 op 的快照目录。"""
    return project_edit_logs_dir(owner_id, project_id) / op_id


def resolve_project_identity(root_path: str | Path) -> tuple[str, str] | None:
    """从项目根路径反解 (owner_id, project_id)。

    多用户模式项目目录规范为 <users_root>/<owner_id>/projects/<project_id>，
    据此解析并经 userspace.project_dir 校验组件安全性；非用户空间内的路径
    （单用户本地目录等）返回 None。
    """
    try:
        root = Path(root_path).resolve()
        users_root = userspace.get_users_root().resolve()
        rel = root.relative_to(users_root)
    except (ValueError, OSError):
        return None
    parts = rel.parts
    if len(parts) < 3 or parts[1] != "projects":
        return None
    owner_id, project_id = parts[0], parts[2]
    try:
        # 借统一入口校验组件（防穿越），不关心结果路径
        userspace.project_dir(owner_id, project_id)
    except ValueError:
        return None
    return owner_id, project_id


def _lookup_workspace_id(project_id: str, actor_user_id: str) -> str:
    """尽力反查绑定该 (project, user) 的工作区 id（仅诊断用途）。"""
    try:
        # 局部导入避免 workspace_manager → project_manager → edit_log 环
        from backend.core.workspace_manager import get_workspace_manager

        for info in get_workspace_manager().list_workspaces():
            if (
                info.get("project_id") == project_id
                and info.get("user_id") == actor_user_id
            ):
                return info["workspace_id"]
    except Exception:
        pass
    return ""


# ── 写入记账 ──────────────────────────────────────────────────────────────
def _detect_source_module() -> str:
    """尽力推断写入来源模块（诊断用，失败静默返回空串）。

    帧链：_detect_source_module(0) → record_write(1) → LatexProject 写方法(2)
    → 业务调用方(3)，如 backend.modules.writing_chat.service.edits →
    "writing_chat"。
    """
    import sys

    try:
        frame = sys._getframe(3)
        module = frame.f_globals.get("__name__", "") or ""
        for prefix in ("backend.modules.", "backend.core.", "backend.utils."):
            if module.startswith(prefix):
                module = module[len(prefix) :]
                break
        if module.startswith("backend."):
            module = module[len("backend.") :]
        return module.split(".", 1)[0]
    except Exception:
        return ""


def record_write(
    root_path: str | Path,
    files: list[tuple[str, str, str]],
) -> str | None:
    """记录一次成功写入对应的反向补丁 op（方案 §8.2）。

    参数：
        root_path: 项目根目录绝对路径
        files: [(相对路径, before 全文, after 全文)]；before == after 的
               文件视为无变化跳过，全部无变化则不产生 op

    返回：
            op_id（多用户模式下实际记账时）；未记账返回 None。
            供配额超限回滚撤销本次记账（discard_op）使用。

    失败安全：任何异常只记 error 日志，不影响已完成的写入主流程。
    """
    if not files or _suppress_recording.get() or not userspace.is_multiuser():
        return
    actor_user_id = userspace.get_current_user_id()
    if not actor_user_id:
        return
    identity = resolve_project_identity(root_path)
    if identity is None:
        logger.debug("Edit log skipped: root %s is not in user space", root_path)
        return
    owner_id, project_id = identity

    changed: list[tuple[str, str, str, str, int, int]] = []
    # (rel_path, before_md5, after_md5, before_text, after_text, size_before, size_after)
    total_bytes = 0
    for rel_path, before_text, after_text in files:
        if before_text == after_text:
            continue
        size_before = len(before_text.encode("utf-8"))
        size_after = len(after_text.encode("utf-8"))
        changed.append(
            (
                rel_path,
                hashlib.md5(before_text.encode("utf-8")).hexdigest(),
                hashlib.md5(after_text.encode("utf-8")).hexdigest(),
                before_text,
                after_text,
                size_before,
                size_after,
            )
        )
        total_bytes += size_before + size_after
    if not changed:
        return

    max_op_bytes = get_config().multi_user.edit_log_max_op_mb * 1024 * 1024
    non_undoable = total_bytes > max_op_bytes
    now = utcnow()
    op_id = uuid.uuid4().hex
    group_id = _op_group.get() or op_id
    source_module = _source_module.get() or _detect_source_module()
    snapshot_dir: Path | None = None

    try:
        snapshot_dir = op_snapshot_dir(owner_id, project_id, op_id)
        manifest = {
            "op_id": op_id,
            "op_group_id": group_id,
            "project_id": project_id,
            "owner_id": owner_id,
            "actor_user_id": actor_user_id,
            "workspace_id": _lookup_workspace_id(project_id, actor_user_id),
            "source_module": source_module,
            "created_at": now,
            "files": [
                {
                    "path": c[0],
                    "before_md5": c[1],
                    "after_md5": c[2],
                    "size_before": c[5],
                    "size_after": c[6],
                }
                for c in changed
            ],
        }
        if non_undoable:
            # 只留索引与清单（供状态接口展示"过大不可撤"），不写全文快照
            snapshot_dir.mkdir(parents=True, exist_ok=True)
        else:
            snapshot_dir.mkdir(parents=True, exist_ok=False)
            for idx, c in enumerate(changed):
                atomic_write(snapshot_dir / f"{idx}.before", c[3])
                atomic_write(snapshot_dir / f"{idx}.after", c[4])
        atomic_write(snapshot_dir / "manifest.json", json.dumps(manifest, indent=2))
        db_execute(
            "INSERT INTO workspace_edit_ops (op_id, op_group_id, project_id, "
            "owner_id, actor_user_id, workspace_id, source_module, status, "
            "non_undoable, file_count, total_bytes, created_at, undone_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                op_id,
                group_id,
                project_id,
                owner_id,
                actor_user_id,
                manifest["workspace_id"],
                source_module,
                STATUS_ACTIVE,
                1 if non_undoable else 0,
                len(changed),
                total_bytes,
                now,
            ),
        )
    except Exception:
        logger.exception(
            "Edit log recording failed (write already committed): "
            "project=%s actor=%s files=%d",
            project_id,
            actor_user_id,
            len(changed),
        )
        # 失败安全：清掉半成品快照目录，避免无索引孤儿残留
        if snapshot_dir is not None:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        return

    logger.info(
        "Edit op recorded: op=%s project=%s actor=%s files=%d bytes=%d%s",
        op_id[:8],
        project_id[:8],
        actor_user_id[:8],
        len(changed),
        total_bytes,
        " [non_undoable]" if non_undoable else "",
    )
    _evict_overflow(project_id, actor_user_id)
    return op_id


def discard_op(op_id: str, project_id: str, actor_user_id: str) -> bool:
    """按 id 删除一条仍为 active 的 op（连同快照目录）。

    供配额超限回滚使用：写入被直接恢复后，其对应的编辑日志 op 一并
    撤销，保证撤销栈不被失败写入污染（方案 §8.4）。仅当 op 属于
    (project, actor) 且仍为 active 时生效；返回是否删除。
    """
    if not op_id:
        return False
    row = db_query_one(
        "SELECT owner_id, status FROM workspace_edit_ops "
        "WHERE op_id = ? AND project_id = ? AND actor_user_id = ?",
        (op_id, project_id, actor_user_id),
    )
    if row is None or row["status"] != STATUS_ACTIVE:
        return False
    db_execute(
        "DELETE FROM workspace_edit_ops WHERE op_id = ?",
        (op_id,),
    )
    shutil.rmtree(
        op_snapshot_dir(row["owner_id"], project_id, op_id), ignore_errors=True
    )
    _prune_empty_dirs(row["owner_id"], project_id)
    return True


def op_after_md5s(
    op_id: str, project_id: str, actor_user_id: str
) -> dict[str, str] | None:
    """返回指定 op 的 {相对路径: 写入后 MD5}（读快照清单）。

    供配额超限回滚构造乐观锁 expected_md5s：只有当文件当前内容仍是本次
    写入的结果时才允许覆盖恢复，防止静默抹掉配额检查窗口期落盘的他人
    修改。op 不存在（未记账 / 已删除 / 清单损坏）返回 None。
    """
    if not op_id:
        return None
    row = db_query_one(
        "SELECT owner_id FROM workspace_edit_ops "
        "WHERE op_id = ? AND project_id = ? AND actor_user_id = ?",
        (op_id, project_id, actor_user_id),
    )
    if row is None:
        return None
    try:
        manifest = _read_manifest(
            {"owner_id": row["owner_id"], "project_id": project_id, "op_id": op_id}
        )
    except HTTPException:
        return None
    return {f["path"]: f["after_md5"] for f in manifest["files"]}


def _evict_overflow(project_id: str, actor_user_id: str) -> int:
    """条数上限淘汰：每 (project, actor) 只保留最近 edit_log_max_ops 条。

    以完整 op_group 为粒度淘汰（不拆组）：从最新端累积，放入即超限的组
    连同其更旧的组整体淘汰，连带删除快照目录。返回删除条数。
    """
    cap = get_config().multi_user.edit_log_max_ops
    rows = db_query_all(
        "SELECT seq, op_id, op_group_id FROM workspace_edit_ops "
        "WHERE project_id = ? AND actor_user_id = ? ORDER BY seq DESC",
        (project_id, actor_user_id),
    )
    if len(rows) <= cap:
        return 0
    # rows 按 seq 降序（最新在前）；按组从最新端累积容量
    group_order: list[str] = []  # 最新组 → 最旧组
    group_seqs: dict[str, list[int]] = {}
    for row in rows:
        gid = row["op_group_id"]
        if gid not in group_seqs:
            group_seqs[gid] = []
            group_order.append(gid)
        group_seqs[gid].append(row["seq"])

    kept = 0
    evict_from: int | None = None  # group_order 中首个被淘汰组的下标
    for idx, gid in enumerate(group_order):
        size = len(group_seqs[gid])
        if kept + size > cap:
            evict_from = idx
            break
        kept += size
    if evict_from is None:
        return 0

    # 淘汰边界：保留组的最小 seq 之前的所有行（含越界组自身）
    kept_groups = group_order[:evict_from]
    if kept_groups:
        cutoff_seq = min(min(group_seqs[g]) for g in kept_groups)
    else:
        # 单组即超限：整栈清空
        cutoff_seq = rows[-1]["seq"] + 1
    stale = db_query_all(
        "SELECT op_id, owner_id FROM workspace_edit_ops "
        "WHERE project_id = ? AND actor_user_id = ? AND seq < ?",
        (project_id, actor_user_id, cutoff_seq),
    )
    if not stale:
        return 0
    db_execute(
        "DELETE FROM workspace_edit_ops "
        "WHERE project_id = ? AND actor_user_id = ? AND seq < ?",
        (project_id, actor_user_id, cutoff_seq),
    )
    owner_id = stale[0]["owner_id"]
    for item in stale:
        shutil.rmtree(
            op_snapshot_dir(owner_id, project_id, item["op_id"]),
            ignore_errors=True,
        )
    _prune_empty_dirs(owner_id, project_id)
    logger.info(
        "Edit log evicted %d stale op(s): project=%s actor=%s",
        len(stale),
        project_id[:8],
        actor_user_id[:8],
    )
    return len(stale)


def _prune_empty_dirs(owner_id: str, project_id: str) -> None:
    """清理空的 <op_id> 与 <project_id> 快照目录（防御性，忽略失败）。"""
    proj_dir = project_edit_logs_dir(owner_id, project_id)
    try:
        if proj_dir.is_dir() and not any(proj_dir.iterdir()):
            proj_dir.rmdir()
        root = edit_logs_root(owner_id)
        if root.is_dir() and not any(root.iterdir()):
            root.rmdir()
    except OSError:
        pass


# ── 清单读取与校验 ────────────────────────────────────────────────────────
class _EditFileMissing(HTTPException):
    """op 记录的文件已不存在（防御性：项目内无删除入口）。

    携带 op_id 供 perform_undo 捕获后把该 op 标记为 failed（方案 §8.4）。
    """

    def __init__(self, message: str, op_id: str):
        super().__init__(
            status_code=409, detail={"error": "file_conflict", "message": message}
        )
        self.op_id = op_id


def _read_manifest(op: dict) -> dict:
    """读取 op 的 manifest.json；缺失/损坏抛 409 file_conflict。"""
    path = op_snapshot_dir(op["owner_id"], op["project_id"], op["op_id"]) / (
        "manifest.json"
    )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(manifest.get("files"), list):
            raise ValueError("manifest.files missing")
        return manifest
    except (OSError, ValueError) as exc:
        raise _http(
            409,
            "file_conflict",
            f"Edit log snapshot for operation {op['op_id'][:8]} is missing or "
            f"corrupted ({exc})",
        ) from exc


def _conflict(rel_path: str, expected_md5: str, current_md5: str) -> HTTPException:
    """构造 409 file_conflict（复用 FileConflictError 的既有文案结构）。"""
    from backend.core.project_manager import FileConflictError

    return _http(
        409,
        "file_conflict",
        str(
            FileConflictError(
                file_path=rel_path,
                expected_md5=expected_md5,
                current_md5=current_md5,
            )
        ),
    )


def _aggregate_group_entries(ops: list[dict]) -> dict[str, dict]:
    """把组内各 op 的清单项按文件路径聚合（方案 §8.3 组级回滚语义）。

    同一 path 在组内出现多条 op（同组对同一文件的链式写入 v0→v1→v2）时，
    整组一次撤销必须回到进组前的状态：以最小 seq 的 before 为恢复基准、
    最大 seq 的 after 为"当前应处于"的预期。调用方保证 ops 按 seq 升序。
    """
    entries: dict[str, dict] = {}
    for op in ops:
        manifest = _read_manifest(op)
        snap_dir = op_snapshot_dir(op["owner_id"], op["project_id"], op["op_id"])
        for idx, meta in enumerate(manifest["files"]):
            entry = entries.setdefault(meta["path"], {})
            item = {"op": op, "snap_dir": snap_dir, "idx": idx, "meta": meta}
            if "first" not in entry:
                entry["first"] = item
            entry["last"] = item
    return entries


def _validate_and_load_undo(
    ops: list[dict], root_path: str
) -> tuple[dict[str, str], dict[str, str], int]:
    """组级校验并加载回滚计划，返回 (updates, expected_md5s, grow_bytes)。

    校验项（方案 §8.3）：
        - 组内每个文件存在于项目内且当前 MD5 == 该路径最大 seq 的
          after_md5，否则 409 file_conflict；
        - 最小 seq 的 before 快照可读且内容哈希一致（快照损坏视同冲突）。
    grow_bytes 为恢复后项目体积增量（字节，可为负）。
    """
    updates: dict[str, str] = {}
    expected: dict[str, str] = {}
    grow_bytes = 0
    root = Path(root_path)
    for rel_path, entry in _aggregate_group_entries(ops).items():
        first, last = entry["first"], entry["last"]
        full_path = resolve_project_path(root, rel_path)
        if not full_path.is_file():
            raise _EditFileMissing(
                f"File {rel_path} recorded in the operation no longer exists",
                op_id=last["op"]["op_id"],
            )
        expected_md5 = last["meta"]["after_md5"]
        current_md5 = compute_md5(full_path)
        if current_md5 != expected_md5:
            raise _conflict(rel_path, expected_md5, current_md5)

        before_path = first["snap_dir"] / f"{first['idx']}.before"
        try:
            before_text = before_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise _http(
                409,
                "file_conflict",
                f"Undo snapshot for {rel_path} is unreadable ({exc})",
            ) from exc
        actual = hashlib.md5(before_text.encode("utf-8")).hexdigest()
        if actual != first["meta"]["before_md5"]:
            raise _http(
                409,
                "file_conflict",
                f"Undo snapshot for {rel_path} failed integrity check",
            )
        updates[rel_path] = before_text
        expected[rel_path] = expected_md5
        grow_bytes += first["meta"].get(
            "size_before", len(before_text.encode("utf-8"))
        ) - (last["meta"].get("size_after", 0))
    return updates, expected, grow_bytes


def _check_undo_applicable(ops: list[dict], root_path: str) -> None:
    """状态接口用的轻量可撤销检查：只做流式哈希，不加载全文。

    与 _validate_and_load_undo 同一组级聚合语义；MD5 校验失败视为不可
    撤销但保留 op——文件回到对应状态后即可再撤（方案 §8.3）。
    """
    root = Path(root_path)
    for rel_path, entry in _aggregate_group_entries(ops).items():
        first, last = entry["first"], entry["last"]
        full_path = resolve_project_path(root, rel_path)
        if not full_path.is_file():
            raise _http(409, "file_conflict", f"File {rel_path} is missing")
        expected_md5 = last["meta"]["after_md5"]
        current_md5 = compute_md5(full_path)
        if current_md5 != expected_md5:
            raise _conflict(rel_path, expected_md5, current_md5)
        # before 快照完整性：流式哈希，避免把全文读进内存
        before_path = first["snap_dir"] / f"{first['idx']}.before"
        try:
            actual = compute_md5(before_path)
        except OSError as exc:
            raise _http(
                409,
                "file_conflict",
                f"Undo snapshot for {rel_path} is unreadable ({exc})",
            ) from exc
        if actual != first["meta"]["before_md5"]:
            raise _http(
                409,
                "file_conflict",
                f"Undo snapshot for {rel_path} failed integrity check",
            )


def _load_redo_updates(
    ops: list[dict], root_path: str
) -> tuple[dict[str, str], dict[str, str], int]:
    """组级校验并加载重放计划，返回 (updates, expected_md5s, grow_bytes)。

    对称语义：当前 MD5 == 该路径最小 seq 的 before_md5（整组已撤销的
    状态）后，重放到最大 seq 的 after 内容。
    """
    updates: dict[str, str] = {}
    expected: dict[str, str] = {}
    grow_bytes = 0
    root = Path(root_path)
    for rel_path, entry in _aggregate_group_entries(ops).items():
        first, last = entry["first"], entry["last"]
        full_path = resolve_project_path(root, rel_path)
        if not full_path.is_file():
            raise _EditFileMissing(
                f"File {rel_path} recorded in the operation no longer exists",
                op_id=last["op"]["op_id"],
            )
        base_md5 = first["meta"]["before_md5"]
        current_md5 = compute_md5(full_path)
        if current_md5 != base_md5:
            raise _conflict(rel_path, base_md5, current_md5)

        after_path = last["snap_dir"] / f"{last['idx']}.after"
        try:
            after_text = after_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise _http(
                409,
                "file_conflict",
                f"Redo snapshot for {rel_path} is unreadable ({exc})",
            ) from exc
        if (
            hashlib.md5(after_text.encode("utf-8")).hexdigest()
            != last["meta"]["after_md5"]
        ):
            raise _http(
                409,
                "file_conflict",
                f"Redo snapshot for {rel_path} failed integrity check",
            )
        updates[rel_path] = after_text
        # 重放 after 的前置状态是整组撤销后的内容（最小 seq 的 before）
        expected[rel_path] = base_md5
        grow_bytes += last["meta"].get("size_after", 0) - first["meta"].get(
            "size_before", len(after_text.encode("utf-8"))
        )
    return updates, expected, grow_bytes


def _check_redo_applicable(ops: list[dict], root_path: str) -> None:
    """状态接口用的轻量可重做检查：只做流式哈希与 .after 存在性检查
    （内容完整性在执行时强校验）。"""
    root = Path(root_path)
    for rel_path, entry in _aggregate_group_entries(ops).items():
        first, last = entry["first"], entry["last"]
        full_path = resolve_project_path(root, rel_path)
        if not full_path.is_file():
            raise _http(409, "file_conflict", f"File {rel_path} is missing")
        base_md5 = first["meta"]["before_md5"]
        current_md5 = compute_md5(full_path)
        if current_md5 != base_md5:
            raise _conflict(rel_path, base_md5, current_md5)
        if not (last["snap_dir"] / f"{last['idx']}.after").is_file():
            raise _http(
                409,
                "file_conflict",
                f"Redo snapshot for {rel_path} is missing",
            )


def _enforce_recover_quota(owner_id: str, grow_bytes: int) -> None:
    """恢复导致项目体积增长时校验配额；超限抛 413 quota_exceeded_recover。

    op 保持 active/undone 可重试（调用方不得改状态）。

    注意：本函数运行在调用方的 DB 事务内，只做**纯读**预检（SELECT）——
    绝不调用会隐式 COMMIT 的 db_execute（如 refresh_owner_usage_after_write
    的 size_bytes 回写）。size_bytes 由各写路径事后刷新，此处允许缓存口径
    的轻微滞后；恢复落盘后调用方仍会再刷新一次。
    """
    if grow_bytes <= 0:
        return
    from .quota import effective_project_quota, project_usage_for_user

    used = project_usage_for_user(owner_id)
    quota = effective_project_quota(owner_id)
    if used + grow_bytes > quota:
        raise _http(
            413,
            "quota_exceeded_recover",
            "Restoring this operation would exceed the project space quota; "
            "free some space and retry",
        )


def _restore_via_project(
    project, updates: dict[str, str], expected: dict[str, str]
) -> None:
    """以 before 内容经 apply_whole_files 回滚/重放（事务乐观锁保护）。

    suppress_recording 防止恢复写回被再次记账。
    """
    with suppress_recording():
        project.apply_whole_files(updates, expected_md5s=expected)


def _group_members(conn, group_id: str, status: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM workspace_edit_ops WHERE op_group_id = ? AND status = ? "
        "ORDER BY seq",
        (group_id, status),
    ).fetchall()
    return [dict(r) for r in rows]


def _newest_active_blocks_redo(conn, project_id: str, redo_group_seq_max: int) -> bool:
    """同项目内存在比待重做组更新的 active op 时，redo 视为已被取代。

    注意必须按 project_id 过滤：seq 为全库自增，跨项目的新写入不应影响
    本项目栈的重做可用性。
    """
    row = conn.execute(
        "SELECT seq FROM workspace_edit_ops "
        "WHERE project_id = ? AND status = 'active' AND seq > ? "
        "LIMIT 1",
        (project_id, redo_group_seq_max),
    ).fetchone()
    return row is not None


# ── 撤销 / 重做 ───────────────────────────────────────────────────────────
def perform_undo(
    project, *, project_id: str, actor_user_id: str, root_path: str
) -> dict:
    """撤销当前用户在该项目的最新一组修改（反向补丁，方案 §8.3）。

    返回 {"undone": bool, ...}；无可撤销操作时 undone=False。
    冲突（当前 MD5 != after_md5 / 快照损坏）→ 409 file_conflict；
    文件缺失（防御性）→ 该 op 标记 failed 后抛 409；
    配额超限 → 413 quota_exceeded_recover（op 保持 active 可重试）。
    """
    try:
        with transaction() as conn:
            newest = conn.execute(
                "SELECT * FROM workspace_edit_ops "
                "WHERE project_id = ? AND actor_user_id = ? AND status = 'active' "
                "ORDER BY seq DESC LIMIT 1",
                (project_id, actor_user_id),
            ).fetchone()
            if newest is None:
                return {"undone": False}
            newest = dict(newest)
            if newest["non_undoable"]:
                raise _http(
                    409,
                    "op_not_undoable",
                    "The latest change is too large to be undone automatically",
                )
            ops = _group_members(conn, newest["op_group_id"], STATUS_ACTIVE)

            updates, expected, grow = _validate_and_load_undo(ops, root_path)

            _enforce_recover_quota(newest["owner_id"], grow)
            _restore_via_project(project, updates, expected)

            marked = conn.execute(
                "UPDATE workspace_edit_ops SET status = 'undone', undone_at = ? "
                "WHERE op_group_id = ? AND status = 'active'",
                (utcnow(), newest["op_group_id"]),
            ).rowcount
    except _EditFileMissing as exc:
        # 文件缺失（理论上无删除入口，防御性处理）：标记 failed 并提示（§8.4）。
        # 事务已回滚，标记在事务外落库。
        db_execute(
            "UPDATE workspace_edit_ops SET status = 'failed' WHERE op_id = ?",
            (exc.op_id,),
        )
        logger.warning("Edit op marked failed (file missing): op=%s", exc.op_id[:8])
        raise

    from .quota import refresh_owner_usage_after_write

    refresh_owner_usage_after_write(project_id)
    logger.info(
        "Undo applied: project=%s actor=%s group=%s ops=%d files=%d",
        project_id[:8],
        actor_user_id[:8],
        newest["op_group_id"][:8],
        marked,
        len(updates),
    )
    return {"undone": True, "files": sorted(updates.keys())}


def perform_redo(
    project, *, project_id: str, actor_user_id: str, root_path: str
) -> dict:
    """对称重放最新一组被撤销的修改（方案 §8.3）。

    校验当前 MD5 == before_md5 后重放 after 内容；有更新的 active op 时
    重做视为已被取代（返回 redone=False）。
    """
    with transaction() as conn:
        # 重放"最近一次被撤销"的组：按 undone_at 降序（同一秒内多次撤销时
        # 后撤销的 op seq 更小，故以 seq 升序破平），而非 seq 最大者
        newest_undone = conn.execute(
            "SELECT * FROM workspace_edit_ops "
            "WHERE project_id = ? AND actor_user_id = ? AND status = 'undone' "
            "ORDER BY undone_at DESC, seq ASC LIMIT 1",
            (project_id, actor_user_id),
        ).fetchone()
        if newest_undone is None:
            return {"redone": False}
        newest_undone = dict(newest_undone)
        group_rows = conn.execute(
            "SELECT MAX(seq) AS max_seq FROM workspace_edit_ops WHERE op_group_id = ?",
            (newest_undone["op_group_id"],),
        ).fetchone()
        if _newest_active_blocks_redo(conn, project_id, int(group_rows["max_seq"])):
            # 新修改已覆盖该操作的产物：不再提供重做（保留 op 供审计）
            return {"redone": False}
        ops = _group_members(conn, newest_undone["op_group_id"], STATUS_UNDONE)

        updates, expected, grow = _load_redo_updates(ops, root_path)

        _enforce_recover_quota(newest_undone["owner_id"], grow)
        _restore_via_project(project, updates, expected)

        marked = conn.execute(
            "UPDATE workspace_edit_ops SET status = 'active', undone_at = NULL "
            "WHERE op_group_id = ? AND status = 'undone'",
            (newest_undone["op_group_id"],),
        ).rowcount

    from .quota import refresh_owner_usage_after_write

    refresh_owner_usage_after_write(project_id)
    logger.info(
        "Redo applied: project=%s actor=%s group=%s ops=%d files=%d",
        project_id[:8],
        actor_user_id[:8],
        newest_undone["op_group_id"][:8],
        marked,
        len(updates),
    )
    return {"redone": True, "files": sorted(updates.keys())}


# ── 实时状态计算 ──────────────────────────────────────────────────────────
def compute_status(project_id: str, actor_user_id: str, root_path: str) -> dict:
    """实时计算 can_undo / can_redo 及阻塞原因（方案 §8.3）。

    MD5 校验失败视为不可撤销但保留 op——文件回到对应状态后即可再撤。
    """
    result = {
        "can_undo": False,
        "can_redo": False,
        "undo_block_reason": None,
        "redo_block_reason": None,
        "failed_ops": 0,
    }
    result["failed_ops"] = db_count(
        "SELECT COUNT(*) FROM workspace_edit_ops "
        "WHERE project_id = ? AND actor_user_id = ? AND status = 'failed'",
        (project_id, actor_user_id),
    )
    newest_active = db_query_one(
        "SELECT * FROM workspace_edit_ops "
        "WHERE project_id = ? AND actor_user_id = ? AND status = 'active' "
        "ORDER BY seq DESC LIMIT 1",
        (project_id, actor_user_id),
    )
    if newest_active is not None:
        if newest_active["non_undoable"]:
            result["undo_block_reason"] = "too_large"
        else:
            try:
                members = db_query_all(
                    "SELECT * FROM workspace_edit_ops WHERE op_group_id = ? "
                    "AND status = 'active' ORDER BY seq",
                    (newest_active["op_group_id"],),
                )
                # 轻量组级检查：流式哈希，不把快照全文读进内存
                _check_undo_applicable(members, root_path)
                result["can_undo"] = True
            except HTTPException:
                result["undo_block_reason"] = "conflict"
    newest_undone = db_query_one(
        "SELECT * FROM workspace_edit_ops "
        "WHERE project_id = ? AND actor_user_id = ? AND status = 'undone' "
        "ORDER BY undone_at DESC, seq ASC LIMIT 1",
        (project_id, actor_user_id),
    )
    if newest_undone is not None:
        group_row = db_query_one(
            "SELECT MAX(seq) AS max_seq FROM workspace_edit_ops "
            "WHERE op_group_id = ?",
            (newest_undone["op_group_id"],),
        )
        superseded = db_query_one(
            "SELECT seq FROM workspace_edit_ops "
            "WHERE project_id = ? AND status = 'active' AND seq > ? "
            "LIMIT 1",
            (project_id, int(group_row["max_seq"])),
        )
        if superseded is not None:
            result["redo_block_reason"] = "superseded"
            return result
        try:
            members = db_query_all(
                "SELECT * FROM workspace_edit_ops WHERE op_group_id = ? "
                "AND status = 'undone' ORDER BY seq",
                (newest_undone["op_group_id"],),
            )
            # 轻量组级检查（聚合语义与执行路径一致；after 快照内容完整性
            # 在执行时强校验）
            _check_redo_applicable(members, root_path)
            result["can_redo"] = True
        except HTTPException:
            result["redo_block_reason"] = "conflict"
    return result


# ── 生命周期清理 ──────────────────────────────────────────────────────────
def purge_expired_edit_logs() -> int:
    """TTL 清理：整栈最近活动（MAX(created_at, undone_at)）超过
    edit_log_ttl_hours 的 (project, actor) 连同快照一并删除。

    由维护循环周期调用；返回清理的 op 条数。
    """
    ttl_hours = get_config().multi_user.edit_log_ttl_hours
    from datetime import datetime, timedelta, timezone

    cutoff = (
        datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=ttl_hours)
    ).isoformat()
    expired = db_query_all(
        "SELECT project_id, actor_user_id, "
        "MAX(COALESCE(undone_at, created_at)) AS last_activity "
        "FROM workspace_edit_ops GROUP BY project_id, actor_user_id "
        "HAVING last_activity < ?",
        (cutoff,),
    )
    removed = 0
    for stack in expired:
        removed += purge_stack(stack["project_id"], stack["actor_user_id"])
    if expired:
        logger.info(
            "Edit log TTL purge: %d stack(s), %d op(s) removed",
            len(expired),
            removed,
        )
    return removed


def purge_stack(project_id: str, actor_user_id: str) -> int:
    """删除指定 (project, actor) 的整个编辑日志栈（行 + 快照目录）。

    注意只逐 op 删除快照目录：同一共享项目其他 actor 的快照存放在同一个
    `<owner>/.edit_logs/<project_id>/` 下，绝不能整目录 rmtree 波及。
    """
    rows = db_query_all(
        "SELECT op_id, owner_id FROM workspace_edit_ops "
        "WHERE project_id = ? AND actor_user_id = ?",
        (project_id, actor_user_id),
    )
    if not rows:
        return 0
    db_execute(
        "DELETE FROM workspace_edit_ops " "WHERE project_id = ? AND actor_user_id = ?",
        (project_id, actor_user_id),
    )
    owner_id = rows[0]["owner_id"]
    for row in rows:
        shutil.rmtree(
            op_snapshot_dir(owner_id, project_id, row["op_id"]), ignore_errors=True
        )
    # 仅当目录已空时收尾清理；非空说明还有其他 actor 的快照，必须保留
    _prune_empty_dirs(owner_id, project_id)
    return len(rows)


def purge_project_ops(project_id: str) -> int:
    """项目永久删除/回收站清空时的级联清理：删除该项目全部 op 与快照。

    项目级语义下所有 actor 的行都会删除，因此最后可安全移除各 owner
    名下该项目的整个快照目录。
    """
    rows = db_query_all(
        "SELECT DISTINCT owner_id, actor_user_id FROM workspace_edit_ops "
        "WHERE project_id = ?",
        (project_id,),
    )
    if not rows:
        return 0
    removed = 0
    for row in rows:
        removed += purge_stack(project_id, row["actor_user_id"])
    for owner_id in {row["owner_id"] for row in rows}:
        shutil.rmtree(project_edit_logs_dir(owner_id, project_id), ignore_errors=True)
    logger.info(
        "Edit log purged for permanently removed project=%s (%d ops)",
        project_id[:8],
        removed,
    )
    return removed
