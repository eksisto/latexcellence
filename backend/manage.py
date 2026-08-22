"""
多用户模式管理命令行工具。

用法示例：
    python -m backend.manage create-admin admin --password 'secret'
    python -m backend.manage create-admin admin            # 交互式输入密码
    python -m backend.manage reset-password alice --password 'new-pass'
    python -m backend.manage list-users
    python -m backend.manage set-status alice active       # 审批通过
    python -m backend.manage set-status alice disabled     # 禁用/拒绝

说明：
    - --config 指定配置文件（默认 config.json）
    - 环境变量 LATEXCELLENCE_ADMIN_PASSWORD 可替代 --password / 交互输入
    - 依赖多用户模式（multi_user.enabled=true）；未开启时提示先修改配置
    - 例外声明：CLI 是终端工具，stdout/stderr 是其唯一输出通道，用 print()
      输出结果属 AGENTS.md"禁止 print() 调试"的合法例外（非调试用途）。
"""

import argparse
import getpass
import os
import sys
from typing import Sequence

from backend.core.config import get_config, load_config
from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import db


def _init(config_path: str) -> None:
    load_config(config_path)
    cfg = get_config()
    if not cfg.multi_user.enabled:
        print(
            "multi_user.enabled must be true to manage users. "
            f"Update your config ({config_path}) and retry.",
            file=sys.stderr,
        )
        sys.exit(1)
    db.init_db()


def _resolve_password(password: str | None) -> str:
    if password:
        return password
    env = os.environ.get(get_config().multi_user.admin_password_env, "")
    if env:
        return env
    return getpass.getpass("Password: ")


def cmd_create_admin(args: argparse.Namespace) -> None:
    _init(args.config)
    username = args.username.strip()
    password = _resolve_password(args.password)
    existing = auth_core.get_user_by_username(username)
    if existing is not None:
        auth_core.set_user_role(existing["id"], "admin")
        auth_core.set_user_status(existing["id"], "active")
        auth_core.reset_password(existing["id"], password)
        print(f"Admin '{username}' updated (role=admin, status=active).")
    else:
        auth_core.create_user(username, password, role="admin", status="active")
        print(f"Admin '{username}' created (role=admin, status=active).")


def cmd_reset_password(args: argparse.Namespace) -> None:
    _init(args.config)
    user = auth_core.get_user_by_username(args.username)
    if user is None:
        print(f"User not found: {args.username}", file=sys.stderr)
        sys.exit(1)
    password = _resolve_password(args.password)
    auth_core.reset_password(user["id"], password)
    print(f"Password reset for '{args.username}' (all sessions revoked).")


def cmd_list_users(args: argparse.Namespace) -> None:
    _init(args.config)
    users = auth_core.list_users()
    if not users:
        print("No users yet.")
        return
    header = f"{'USERNAME':<24} {'ROLE':<6} {'STATUS':<10} {'CREATED_AT':<25} QUOTA(B)"
    print(header)
    print("-" * len(header))
    for u in users:
        print(
            f"{u['username']:<24} {u['role']:<6} {u['status']:<10} "
            f"{u['created_at']:<25} "
            f"P={u['project_quota_bytes'] or 'default'} D={u['derived_quota_bytes'] or 'default'}"
        )


def cmd_set_status(args: argparse.Namespace) -> None:
    _init(args.config)
    user = auth_core.get_user_by_username(args.username)
    if user is None:
        print(f"User not found: {args.username}", file=sys.stderr)
        sys.exit(1)
    auth_core.set_user_status(user["id"], args.status)
    print(f"'{args.username}' status -> {args.status}.")


def cmd_migrate_legacy(args: argparse.Namespace) -> None:
    """将存量 data/ 全局数据复制到管理员用户空间（幂等，详见 migrate.py）。"""
    from backend.core.multiuser import migrate as migrate_core

    _init(args.config)
    admin = migrate_core.find_first_admin()
    if admin is None:
        print(
            "No admin account exists yet. Create one first with "
            "'python -m backend.manage create-admin <username>'.",
            file=sys.stderr,
        )
        sys.exit(1)
    report = migrate_core.migrate_legacy(
        admin["id"], dry_run=bool(getattr(args, "dry_run", False))
    )
    print(f"Migration report (admin: {admin['username']} / {admin['id']})")
    print(f"{'DIR':<24} {'STATUS':<14} COPIED  SKIPPED  FILES  VERIFIED")
    print("-" * 78)
    for d in report["dirs"]:
        verify = d.get("verify", {})
        verified = (
            f"{verify.get('present', 0)}/"
            f"{(verify.get('present', 0) + verify.get('missing', 0) + verify.get('mismatch', 0))}"
            if d["status"] == "copied" and not report["dry_run"]
            else "-"
        )
        print(
            f"{d['name']:<24} {d['status']:<14} "
            f"{d.get('copied', 0):<6} {d.get('skipped', 0):<7} "
            f"{d.get('files', 0):<5} {verified}"
        )
    print("-" * 78)
    print(
        f"total copied: {report['total_copied']}, skipped (already present): "
        f"{report['total_skipped']}, all_verified: {report['all_verified']}"
    )
    mismatched = [d for d in report["dirs"] if d.get("verify", {}).get("mismatch")]
    if mismatched:
        print(
            "\nWARNING: some target files differ in size from their source "
            "(leftover of an interrupted earlier run, or modified after "
            "migration):",
            file=sys.stderr,
        )
        for d in mismatched:
            v = d["verify"]
            print(
                f"  {d['name']}: {v['mismatch']} file(s), e.g. "
                f"{', '.join(v['mismatch_samples'])}",
                file=sys.stderr,
            )
        print(
            "Fix manually (delete the corrupt target file and re-run "
            "'migrate-legacy') BEFORE removing any source data.",
            file=sys.stderr,
        )
    if report["dry_run"]:
        print("This was a dry run — no files were written.")


def cmd_db_status(args: argparse.Namespace) -> None:
    """查看 schema 版本与待应用迁移步骤（v2 P0 迁移框架自检）。"""
    _init(args.config)
    # 说明：_init 已通过 init_db() 幂等应用全部迁移，正常情况下此处恒为最新；
    # pending 非空只可能出现在"代码版本回退到旧 schema"等异常场景。
    status = db.schema_status()
    print(f"schema user_version: {status['user_version']}")
    print(f"latest schema version: {status['latest_version']}")
    if status["up_to_date"]:
        print("Database schema is up to date.")
    else:
        print(
            "WARNING: database is OLDER than this code's schema version "
            "(unexpected after init_db). Pending migrations:"
        )
        for step in status["pending"]:
            print(f"  v{step['version']}: {step['description']}")
        print(
            "Re-run any manage command (or restart the server) to apply "
            "pending migrations; if it persists, check whether multiple "
            "code versions share the same data directory."
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.manage", description="多用户模式管理工具"
    )
    parser.add_argument(
        "--config", default="config.json", help="配置文件路径（默认 config.json）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create-admin", help="创建/提升管理员账号")
    p_create.add_argument("username")
    p_create.add_argument("--password", default=None)
    p_create.set_defaults(func=cmd_create_admin)

    p_reset = sub.add_parser("reset-password", help="重置用户密码（并吊销其会话）")
    p_reset.add_argument("username")
    p_reset.add_argument("--password", default=None)
    p_reset.set_defaults(func=cmd_reset_password)

    p_list = sub.add_parser("list-users", help="列出所有用户")
    p_list.set_defaults(func=cmd_list_users)

    p_status = sub.add_parser(
        "set-status", help="设置用户状态（pending/active/disabled，用于审批/禁用）"
    )
    p_status.add_argument("username")
    p_status.add_argument("status", choices=("pending", "active", "disabled"))
    p_status.set_defaults(func=cmd_set_status)

    p_migrate = sub.add_parser(
        "migrate-legacy", help="将存量 data/ 数据复制到管理员用户空间（幂等）"
    )
    p_migrate.add_argument(
        "--dry-run", action="store_true", help="仅预演统计，不实际复制"
    )
    p_migrate.set_defaults(func=cmd_migrate_legacy)

    p_dbstatus = sub.add_parser(
        "db-status", help="查看 schema 版本与待应用的数据库迁移步骤"
    )
    p_dbstatus.set_defaults(func=cmd_db_status)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
