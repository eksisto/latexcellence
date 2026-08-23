"""多用户后台维护任务（P3：配额对账 + 回收站清理 + 暂存区清理；P4/P6 扩充；
P7 选主）。

- reconcile_project_sizes()：重算各项目磁盘大小，修正 projects.size_bytes 缓存漂移
- reconcile_derived_usage()：全量重算四类衍生数据用量，修正 user_derived_usage
  缓存漂移（v2 P4；检查点增量刷新之间的兜底校正）
- clean_expired_recycle_bin()：永久删除超过 recycle_bin_days 的回收站项目
- clean_staging()：清理上传中断/失败遗留的暂存目录
- purge_expired_sandbox_slots()：清理崩溃残留的沙箱并发租约（v2 P1a）
- purge_expired_llm_leases() / purge_old_rate_limit_events()：
  清理过期 LLM 并发租约与超过保留期的限流事件（v2 P1b）
- purge_expired_edit_logs()：清理超过 edit_log_ttl_hours 无活动的
  反向补丁编辑日志栈与快照目录（v2 P3）
- purge_expired_sessions()：删除已过期的登录会话行（v2 P4，修复无限增长）
- purge_expired_writing_chats()：按 writing_chat_retention_days 清理闲置写作
  会话（v2 P4；默认 0 = 不自动删，仅统计）
- purge_expired_upload_sessions()：清理超过 upload_session_ttl_hours 无活动的
  分片上传会话（行 + staging 目录）与孤儿会话目录（v2 P5）
- evaluate_quota_alerts()：配额预警检查（v2 P6，双对账之后执行；只读用量
  缓存与 projects.size_bytes，迟滞标记存 user_alert_state，邮件经 outbox 队列）
- purge_expired_reset_tokens() / purge_old_outbox()：自助找回令牌与邮件
  留档清理（v2 P6）
- purge_expired_operation_contexts() / purge_expired_pending_interactions()：
  多实例外置状态的 TTL 清理（v2 P7）
- requeue_stale_outbox_claims()：回收副本崩溃残留的 sending 邮件行（v2 P7）

v2 P7 多实例选主：多副本部署时**持 maintenance_lease 租约者才执行循环体**
（配额对账 / 各类 TTL 清理 / 配额预警检查），租约到期自动易主；模块各自的
cleanup_loop（template/style_modify/plot 等）幂等无害，保持独立运行不收编。

由 main.py lifespan 通过 asyncio.Task 启动；方法内实时读取配置。
"""

import asyncio

from backend.core.logger import get_logger
from backend.modules.writing_chat.cleanup import purge_expired_writing_chats

from . import mail, operation_contexts, pending_interactions, quota
from . import maintenance_lease as leases
from .auth import purge_expired_reset_tokens, purge_expired_sessions
from .db import init_db
from .edit_log import purge_expired_edit_logs
from .llm_governance import (
    purge_expired_llm_leases,
    purge_old_rate_limit_events,
)
from .sandbox_leases import purge_expired_sandbox_slots
from .upload_sessions import purge_expired_upload_sessions

logger = get_logger("multiuser.maintenance")

DEFAULT_INTERVAL_SECONDS = 3600
# 选主租约 TTL：大于循环间隔（崩溃副本的残留租约最迟一个 TTL 后易主；
# 正常情况下每轮由持有人续约）
LEASE_TTL_SECONDS = DEFAULT_INTERVAL_SECONDS * 2


async def run_maintenance_cycle() -> bool:
    """执行一轮完整维护（配额对账 + 各类 TTL 清理 + 配额预警检查）。

    返回本轮是否有任何清理/修正动作发生。供 maintenance_loop（持租约时）
    与测试直接驱动。
    """
    did_work = False
    reconciled = await asyncio.to_thread(quota.reconcile_project_sizes)
    derived_fixed = await asyncio.to_thread(quota.reconcile_derived_usage)
    # 配额预警检查挂在双对账之后：直接读刚修正过的 size_bytes 与
    # 衍生用量缓存，不新增全盘扫描（v2 P6）
    quota_alerts = await asyncio.to_thread(mail.evaluate_quota_alerts)
    purged = await asyncio.to_thread(quota.clean_expired_recycle_bin)
    cleaned = await asyncio.to_thread(quota.clean_staging)
    archived = await asyncio.to_thread(quota.clean_expired_archives)
    slots = await asyncio.to_thread(purge_expired_sandbox_slots)
    llm_leases = await asyncio.to_thread(purge_expired_llm_leases)
    rl_events = await asyncio.to_thread(purge_old_rate_limit_events)
    edit_ops = await asyncio.to_thread(purge_expired_edit_logs)
    sessions = await asyncio.to_thread(purge_expired_sessions)
    chats = await asyncio.to_thread(purge_expired_writing_chats)
    upload_sessions_purged = await asyncio.to_thread(purge_expired_upload_sessions)
    reset_tokens = await asyncio.to_thread(purge_expired_reset_tokens)
    outbox_purged = await asyncio.to_thread(mail.purge_old_outbox)
    # v2 P7：多实例外置状态的 TTL 清理 + 崩溃残留的 sending 邮件行回收
    op_ctx = await asyncio.to_thread(operation_contexts.purge_expired)
    pending = await asyncio.to_thread(pending_interactions.purge_expired)
    requeued = await asyncio.to_thread(mail.requeue_stale_outbox_claims)
    if (
        reconciled
        or derived_fixed
        or purged
        or cleaned
        or archived
        or slots
        or llm_leases
        or rl_events
        or edit_ops
        or sessions
        or chats
        or upload_sessions_purged
        or reset_tokens
        or outbox_purged
        or op_ctx
        or pending
        or requeued
    ):
        logger.info(
            "Multi-user maintenance done: reconciled=%d derived_fixed=%d "
            "purged=%d cleaned_staging=%d cleaned_archives=%d "
            "sandbox_slots=%d llm_leases=%d rate_limit_events=%d "
            "edit_log_ops=%d expired_sessions=%d writing_chats=%d "
            "upload_sessions=%d reset_tokens=%d outbox=%d "
            "operation_contexts=%d pending_interactions=%d "
            "outbox_requeued=%d quota_alert_mails=%d",
            reconciled,
            derived_fixed,
            purged,
            cleaned,
            archived,
            slots,
            llm_leases,
            rl_events,
            edit_ops,
            sessions,
            chats,
            upload_sessions_purged,
            reset_tokens,
            outbox_purged,
            op_ctx,
            pending,
            requeued,
            quota_alerts,
        )
        did_work = True
    return did_work


async def maintenance_loop() -> None:
    """后台循环：持维护租约者周期执行配额对账与各类清理（v2 P7 选主）。

    未持租约的副本本轮直接休眠（租约到期自动易主）；进程被取消时尽力
    释放租约，让存活副本立即接管。
    """
    logger.info("Multi-user maintenance loop started")
    holder = leases.make_holder()
    holds_lease = False
    try:
        while True:
            try:
                await asyncio.to_thread(init_db)
                holds_lease = await asyncio.to_thread(
                    leases.try_acquire,
                    leases.LEASE_KEY_MAINTENANCE,
                    holder,
                    LEASE_TTL_SECONDS,
                )
                if holds_lease:
                    await run_maintenance_cycle()
                else:
                    logger.debug(
                        "Maintenance lease held by another replica; skipping cycle"
                    )
            except Exception:
                logger.exception("Multi-user maintenance cycle failed")
            await asyncio.sleep(DEFAULT_INTERVAL_SECONDS)
    finally:
        if holds_lease:
            try:
                leases.release(leases.LEASE_KEY_MAINTENANCE, holder)
            except Exception:  # noqa: BLE001 - 关闭路径尽力而为
                pass
