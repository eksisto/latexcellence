"""
多用户模式存量数据迁移工具（P6，对应方案 §13.2）。

将单用户模式的存量全局目录（`data/writing_chat_sessions/` 等）复制到
管理员用户空间对应子目录，使旧数据归入管理员账号，供多用户模式下（按用户
隔离的业务层）读取。

设计要点：
    - 仅复制不复用：源目录默认保留，确认无误后由人工清理；
    - 幂等：目标文件已存在则跳过（不覆盖迁移后新产生的数据），可重复执行；
    - 原子落盘：先写临时文件再 os.replace——直接 copy2 到目标路径时，
      磁盘满 / Ctrl-C 中断会留下截断的目标文件，重跑被幂等跳过、校验又
      误报通过，管理员清理源目录后数据静默丢失；并发执行同样安全；
    - 复制完成后校验目录树：逐一核对目标侧存在性 + 文件大小一致。
      大小不一致（历史中断残留 / 迁移后被修改）计入校验失败并给出样本，
      不自动覆盖（遵守"不覆盖新数据"约定），由人工处置后重跑。

说明：
    - formats/prompts 虽属配置级全局（§4.3），其存量数据同样应归入管理员
      账号以便管理与保留，故一并迁移（运行时业务层仍读全局路径）。
    - 本模块为纯同步函数层；CLI 入口在 backend/manage.py（migrate-legacy）。
"""

import os
import shutil
import time
from pathlib import Path
from typing import Any

from backend.core.config import get_config
from backend.core.logger import get_logger
from . import userspace

logger = get_logger("multiuser.migrate")

# 复制临时文件后缀（含进程号，并发迁移互不冲突）
_TEMP_SUFFIX = ".migrate-tmp"
# 超过该时长的残留临时文件视为被强杀进程的遗留，下次运行开头清扫
_STALE_TEMP_SECONDS = 3600


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p


def legacy_source_dirs(cfg: Any | None = None) -> dict[str, Path]:
    """返回存量全局目录 → 源路径映射（key 同时是管理员用户空间子目录名）。

    与方案 §13.2 列表一致：writing_chat_sessions / style_modify_sessions /
    templates / plots / formats / prompts。
    """
    cfg = cfg or get_config()
    f = cfg.features
    prompts_parent = _resolve(f.expression_opt.custom_prompts_dir).parent
    return {
        "writing_chat_sessions": _resolve(f.writing_chat.sessions_dir),
        "style_modify_sessions": _resolve(f.style_modify.agent.sessions_dir),
        "templates": _resolve(f.template_gen.output_dir),
        "plots": _resolve(f.plot_gen.output_dir),
        "formats": _resolve(f.bib_manager.format_configs_dir),
        "prompts": prompts_parent,
    }


def _copy_file_atomic(src: Path, target: Path) -> None:
    """原子复制单个文件：写临时文件后 os.replace 落盘。

    中断（磁盘满 / Ctrl-C / 并发竞争）只影响临时文件，目标路径要么不存在、
    要么是完整文件，绝不出现截断的半写状态。"""
    tmp = target.with_name(f"{target.name}{_TEMP_SUFFIX}-{os.getpid()}")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _sweep_stale_temps(dst: Path) -> int:
    """清扫被强杀进程遗留的临时文件（mtime 早于保留期），返回清理数。"""
    cutoff = time.time() - _STALE_TEMP_SECONDS
    removed = 0
    for p in dst.rglob(f"*{_TEMP_SUFFIX}*"):
        try:
            if p.is_file() and not p.is_symlink() and p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _copy_tree_idempotent(
    src: Path, dst: Path, dry_run: bool = False
) -> dict[str, int]:
    """幂等复制目录树：目标已存在文件跳过，返回 {copied, skipped, files}。

    dry_run=True 只统计不实际复制。
    """
    copied = 0
    skipped = 0
    files = 0
    for p in sorted(src.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        files += 1
        rel = p.relative_to(src)
        target = dst / rel
        if target.exists():
            skipped += 1
            continue
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_file_atomic(p, target)
        copied += 1
    return {"copied": copied, "skipped": skipped, "files": files}


def verify_tree(src: Path, dst: Path) -> dict[str, Any]:
    """校验迁移后的目录树：目标侧存在性 + 文件大小一致。

    大小不一致（历史中断残留的截断文件 / 迁移后被修改）计入 mismatch，
    不自动覆盖——避免破坏"不覆盖迁移后新产生数据"的约定；报告给出样本，
    由人工删除损坏目标文件后重跑迁移补齐。"""
    present = 0
    missing = 0
    mismatch = 0
    missing_samples: list[str] = []
    mismatch_samples: list[str] = []
    for p in sorted(src.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        rel = p.relative_to(src)
        target = dst / rel
        if not target.exists():
            missing += 1
            if len(missing_samples) < 5:
                missing_samples.append(rel.as_posix())
            continue
        try:
            same_size = target.stat().st_size == p.stat().st_size
        except OSError:
            same_size = False
        if same_size:
            present += 1
        else:
            mismatch += 1
            if len(mismatch_samples) < 5:
                mismatch_samples.append(rel.as_posix())
    return {
        "present": present,
        "missing": missing,
        "missing_samples": missing_samples,
        "mismatch": mismatch,
        "mismatch_samples": mismatch_samples,
    }


def migrate_legacy(admin_id: str, dry_run: bool = False) -> dict[str, Any]:
    """将存量全局目录复制到管理员用户空间，返回迁移报告（幂等）。

    参数：
        admin_id: 目标管理员用户 ID（该账号承担存量数据归属）
        dry_run: 只统计不实际复制（预演）

    返回报告结构：
        {
          "admin_id": ...,
          "dirs": [
             {"name": ..., "source": ..., "target": ...,
              "status": "copied"|"source_missing",
              "copied": int, "skipped": int, "files": int, "verify": {...}}
          ],
          "total_copied": int, "total_skipped": int, "all_verified": bool
        }
    """
    cfg = get_config()
    admin = userspace.user_root(admin_id)
    dirs_report: list[dict[str, Any]] = []
    total_copied = 0
    total_skipped = 0

    for name, src in legacy_source_dirs(cfg).items():
        if not src.is_dir():
            dirs_report.append(
                {
                    "name": name,
                    "source": str(src),
                    "target": str(admin / name),
                    "status": "source_missing",
                    "copied": 0,
                    "skipped": 0,
                    "files": 0,
                    "verify": {
                        "present": 0,
                        "missing": 0,
                        "missing_samples": [],
                        "mismatch": 0,
                        "mismatch_samples": [],
                    },
                }
            )
            continue
        dst = admin / name
        if not dry_run:
            dst.mkdir(parents=True, exist_ok=True)
            swept = _sweep_stale_temps(dst)
            if swept:
                logger.info(
                    "migrate-legacy: swept %d stale temp files under %s", swept, name
                )
        stats = _copy_tree_idempotent(src, dst, dry_run=dry_run)
        verify = (
            verify_tree(src, dst)
            if not dry_run
            else {
                "present": 0,
                "missing": 0,
                "missing_samples": [],
                "mismatch": 0,
                "mismatch_samples": [],
            }
        )
        total_copied += stats["copied"]
        total_skipped += stats["skipped"]
        dirs_report.append(
            {
                "name": name,
                "source": str(src),
                "target": str(dst),
                "status": "copied",
                "copied": stats["copied"],
                "skipped": stats["skipped"],
                "files": stats["files"],
                "verify": verify,
            }
        )
        logger.info(
            "migrate-legacy: %s copied=%d skipped=%d verified=(%d present / "
            "%d missing / %d size-mismatch)",
            name,
            stats["copied"],
            stats["skipped"],
            verify["present"],
            verify["missing"],
            verify["mismatch"],
        )

    all_verified = all(
        d.get("verify", {}).get("missing", 0) == 0
        and d.get("verify", {}).get("mismatch", 0) == 0
        for d in dirs_report
        if d["status"] == "copied"
    )
    if not dry_run:
        # v2 P4 检查点：本次复制向管理员用户空间的四类衍生目录写入了数据，
        # 即时重扫刷新 user_derived_usage 缓存行——否则在缓存表非空时
        # （无首读引导可依赖），迁移量要到下一次维护循环对账才可见，
        # 期间衍生配额校验会读到偏小的旧值
        from .quota import resync_user_derived_usage

        total = resync_user_derived_usage(admin_id)
        logger.info(
            "migrate-legacy: refreshed derived usage cache for admin "
            "%s (total=%d bytes)",
            admin_id,
            total,
        )
    return {
        "admin_id": admin_id,
        "dry_run": dry_run,
        "dirs": dirs_report,
        "total_copied": total_copied,
        "total_skipped": total_skipped,
        "all_verified": all_verified,
    }


def find_first_admin() -> dict | None:
    """返回第一个管理员账号（role=admin），供 migrate-legacy 缺省目标使用。"""
    from .db import db_query_all

    rows = db_query_all(
        "SELECT id, username FROM users WHERE role = 'admin' ORDER BY created_at, id"
    )
    return rows[0] if rows else None
