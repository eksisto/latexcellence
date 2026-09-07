"""
多用户模式邮件服务（v2 P6，纯同步投递接口 + 异步发送协程）。

职责：
    1. SMTP 配置就绪判断与优雅降级：`smtp.enabled=false`（或缺 host）时
       所有通知点静默跳过（debug 日志），不阻塞业务主流程
    2. 事件模板（中/英两套，跟随 smtp.template_language 站点默认语言，
       集中管理于本模块）；找回链接前缀取 site_base_url，未配置退化为
       相对路径提示文案
    3. email_outbox 队列：业务方 `enqueue*()` 投递即返回；发送协程
       `outbox_loop()`（main.py lifespan 启动）消费，指数退避重试
       retry_attempts 次，失败留档 failed（last_error）供运维排查；
       全局限速 max_send_per_minute（滚动 60s 窗口）
    4. 事件矩阵（方案 §11.2）：
       - 新用户待审批 → 全部 admin，每 5 分钟聚合为一封（去抖状态存
         user_alert_state kind='pending_digest'，取 MAX(sent_at) 判定）
       - 审批通过/拒绝/禁用、管理员重置密码 → 该用户，即时
       - 自助找回令牌 → 该用户邮箱，每邮箱 5 分钟 1 次
       - 配额预警 80% / 95% → 该用户（95% 加送 admin），迟滞：低于
         quota_alert_reset_percent 清除标记后可再次发送
    5. 配额预警检查 `evaluate_quota_alerts()`：读 projects.size_bytes 与
       user_derived_usage 缓存（不新增全盘扫描），挂入维护循环对账之后

约定：
    - 入队与状态更新为同步函数（asyncio.to_thread 调度）；实际 SMTP 发送
      在事件循环内由 aiosmtplib 异步完成
    - 所有通知入口吞掉自身异常（记 warning），邮件失败绝不影响业务主流程
    - 方法内实时调用 get_config()，不缓存配置
"""

import asyncio
import uuid
from email.message import EmailMessage
from email.utils import formataddr

import aiosmtplib

from backend.core.config import get_config
from backend.core.logger import get_logger

from .db import (
    db_execute,
    db_query_all,
    db_query_one,
    is_initialized,
    utcnow,
    utcnow_after,
)

logger = get_logger("multiuser.mail")

# ── 常量 ────────────────────────────────────────────────────────
# 待审批注册聚合去抖窗口（秒）：窗口内多个注册合并为一封
PENDING_DIGEST_INTERVAL_SECONDS = 300
# 自助找回令牌邮件每邮箱投递去抖窗口（秒）
RESET_TARGET_DEBOUNCE_SECONDS = 300
# 找回/重置端点 IP 维度限流（复用 rate_limit_events 表 kind 机制）
FORGOT_IP_LIMIT_PER_MINUTE = 5
RESET_IP_LIMIT_PER_MINUTE = 10
_RATE_WINDOW_SECONDS = 60
# 发送协程轮询间隔（秒）：队列空闲时的休眠步长
_POLL_INTERVAL_SECONDS = 5.0
# 指数退避基数（秒）：第 n 次失败后等待 base * 2^(n-1)，上限 60s
_BACKOFF_BASE_SECONDS = 2.0
_BACKOFF_MAX_SECONDS = 60.0
# outbox 行保留天数（sent / failed 留档供排查，超期由维护循环清理）
OUTBOX_RETENTION_DAYS = 7
# sending 认领的滞留回收阈值（秒）：副本在发送中途崩溃时，行会停留在
# 'sending'；超过该时长仍无进展即视为残留，由维护循环回收回 queued 重投
# （v2 P7 多实例：发送前原子认领防双副本重复投递，本阈值兜底认领者崩溃）
SENDING_STALE_SECONDS = 600

# 配额预警迟滞标记 kind（user_alert_state 表）：{维度}_{级别}
_QUOTA_KIND = "{dimension}_{level}"
_QUOTA_DIMENSIONS = ("project", "derived")
_QUOTA_LEVEL_WARN = "warn"
_QUOTA_LEVEL_CRITICAL = "critical"

# 待审批聚合去抖标记 kind（每个 admin 一行，全局取 MAX(sent_at) 判定）
_KIND_PENDING_DIGEST = "pending_digest"


def smtp_ready() -> bool:
    """SMTP 是否可用：enabled 且 host 非空。未就绪时所有通知点静默跳过。"""
    cfg = get_config().smtp
    return bool(cfg.enabled and cfg.host.strip())


# ── 模板（中/英两套，集中管理）────────────────────────────────────
_TEMPLATES: dict[str, dict[str, dict[str, str]]] = {
    "pending_registration": {
        "zh": {
            "subject": "[LaTeXcellence] 新用户待审批",
            "body": (
                "您好，管理员：\n\n"
                "LaTeXcellence 站点有 {count} 个新用户注册等待审批：\n"
                "{usernames}\n"
                "请登录管理面板处理。"
            ),
        },
        "en": {
            "subject": "[LaTeXcellence] New users awaiting approval",
            "body": (
                "Hello admin,\n\n"
                "{count} new user(s) on LaTeXcellence are awaiting approval:\n"
                "{usernames}\n"
                "Please review them in the admin panel."
            ),
        },
    },
    "account_approved": {
        "zh": {
            "subject": "[LaTeXcellence] 账号已开通",
            "body": (
                "您好，{username}：\n\n"
                "您的 LaTeXcellence 账号已通过审批，现在可以登录使用。\n"
                "登录地址：{site_link}"
            ),
        },
        "en": {
            "subject": "[LaTeXcellence] Account approved",
            "body": (
                "Hello {username},\n\n"
                "Your LaTeXcellence account has been approved. You can sign in now.\n"
                "Sign in at: {site_link}"
            ),
        },
    },
    "account_rejected": {
        "zh": {
            "subject": "[LaTeXcellence] 注册申请未通过",
            "body": (
                "您好，{username}：\n\n"
                "很抱歉，您的 LaTeXcellence 注册申请未获通过。"
                "如有疑问请联系管理员。"
            ),
        },
        "en": {
            "subject": "[LaTeXcellence] Registration rejected",
            "body": (
                "Hello {username},\n\n"
                "Unfortunately your LaTeXcellence registration was not approved. "
                "Contact the administrator if you have questions."
            ),
        },
    },
    "account_disabled": {
        "zh": {
            "subject": "[LaTeXcellence] 账号已被禁用",
            "body": (
                "您好，{username}：\n\n"
                "您的 LaTeXcellence 账号已被管理员禁用，会话已全部注销。"
                "如有疑问请联系管理员。"
            ),
        },
        "en": {
            "subject": "[LaTeXcellence] Account disabled",
            "body": (
                "Hello {username},\n\n"
                "Your LaTeXcellence account has been disabled by an administrator "
                "and all sessions were signed out. Contact the administrator "
                "if you have questions."
            ),
        },
    },
    "admin_password_reset": {
        "zh": {
            "subject": "[LaTeXcellence] 密码已由管理员重置",
            "body": (
                "您好，{username}：\n\n"
                "管理员已重置您的密码，临时密码为：\n\n"
                "    {temp_password}\n\n"
                "请尽快登录并在设置中修改为自己的密码。"
                "登录地址：{site_link}"
            ),
        },
        "en": {
            "subject": "[LaTeXcellence] Password reset by administrator",
            "body": (
                "Hello {username},\n\n"
                "An administrator reset your password. Temporary password:\n\n"
                "    {temp_password}\n\n"
                "Please sign in soon and change it in settings.\n"
                "Sign in at: {site_link}"
            ),
        },
    },
    "password_reset_token": {
        "zh": {
            "subject": "[LaTeXcellence] 重置密码请求",
            "body": (
                "您好，{username}：\n\n"
                "我们收到了您（或他人）重置 {username} 账号密码的请求。"
                "点击下方链接设置新密码（1 小时内有效，仅可使用一次）：\n\n"
                "{reset_link}\n\n"
                "若非本人操作，请忽略本邮件，账号不会受影响。"
            ),
        },
        "en": {
            "subject": "[LaTeXcellence] Password reset request",
            "body": (
                "Hello {username},\n\n"
                "We received a request to reset the password of the account "
                '"{username}". Open the link below to set a new password '
                "(valid for 1 hour, single use):\n\n"
                "{reset_link}\n\n"
                "If this wasn't you, ignore this email — your account is safe."
            ),
        },
    },
    "quota_warn": {
        "zh": {
            "subject": "[LaTeXcellence] 空间用量达到 {percent}%",
            "body": (
                "您好，{username}：\n\n"
                "您的{dimension_label}用量已达到配额的 {percent}%"
                "（{used} / {quota}），请注意清理或申请扩容。"
            ),
        },
        "en": {
            "subject": "[LaTeXcellence] {dimension_label} usage at {percent}%",
            "body": (
                "Hello {username},\n\n"
                "Your {dimension_label} usage has reached {percent}% of the quota "
                "({used} / {quota}). Consider cleaning up or requesting more space."
            ),
        },
    },
    "quota_critical": {
        "zh": {
            "subject": "[LaTeXcellence] 空间用量告急 {percent}%",
            "body": (
                "您好，{username}：\n\n"
                "您的{dimension_label}用量已达配额的 {percent}%"
                "（{used} / {quota}），即将写满，新文件写入可能被拒绝，请尽快清理。"
            ),
        },
        "en": {
            "subject": "[LaTeXcellence] {dimension_label} almost full: {percent}%",
            "body": (
                "Hello {username},\n\n"
                "Your {dimension_label} usage has reached {percent}% of the quota "
                "({used} / {quota}). New writes may be rejected soon — "
                "please free up space."
            ),
        },
    },
}

_DIMENSION_LABELS = {
    "zh": {"project": "项目空间", "derived": "衍生数据空间"},
    "en": {"project": "project space", "derived": "derived data space"},
}


def _template_language() -> str:
    lang = get_config().smtp.template_language
    return lang if lang in ("zh", "en") else "zh"


def render_template(kind: str, **params) -> tuple[str, str]:
    """渲染事件模板，返回（主题, 正文）。语言跟随 smtp.template_language。"""
    lang = _template_language()
    tpl = _TEMPLATES[kind][lang]
    return tpl["subject"].format(**params), tpl["body"].format(**params)


def build_reset_link(token: str) -> str:
    """找回链接：site_base_url 为前缀；未配置时退化为相对路径（正文中
    附带"若为相对路径请在浏览器站点地址后拼接"的提示由模板语境自明）。"""
    base = get_config().smtp.site_base_url.strip().rstrip("/")
    path = f"/reset-password?token={token}"
    return f"{base}{path}" if base else path


def _site_link() -> str:
    base = get_config().smtp.site_base_url.strip().rstrip("/")
    return base if base else "/（站点首页）"


def _site_base_configured() -> bool:
    return bool(get_config().smtp.site_base_url.strip())


# ── 队列底层 ────────────────────────────────────────────────────
def enqueue(recipient: str, subject: str, body_text: str, body_html: str = "") -> bool:
    """投递一封邮件到 outbox 队列（业务方调用后立即返回）。

    SMTP 未就绪时静默跳过（debug 日志）并返回 False；成功入队返回 True。
    """
    if not recipient or not smtp_ready():
        logger.debug(
            "Mail skipped (smtp disabled or no recipient): to=%s subject=%s",
            recipient,
            subject,
        )
        return False
    db_execute(
        "INSERT INTO email_outbox (id, recipient, subject, body_text, body_html, "
        "status, attempts, created_at) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?)",
        (uuid.uuid4().hex, recipient.strip(), subject, body_text, body_html, utcnow()),
    )
    logger.info("Mail queued: to=%s subject=%s", recipient, subject)
    return True


def _backoff_seconds(attempts: int) -> float:
    """第 attempts 次失败后的退避等待（指数，封顶 60s）。"""
    return min(
        _BACKOFF_MAX_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1))
    )


def _sent_count_in_window() -> int:
    """滚动 60s 窗口内已发送条数（全局限速依据）。"""
    window_start = utcnow_after(-_RATE_WINDOW_SECONDS)
    return int(
        db_query_one(
            "SELECT COUNT(*) AS n FROM email_outbox "
            "WHERE status = 'sent' AND sent_at >= ?",
            (window_start,),
        )["n"]
    )


async def _smtp_send(
    recipient: str, subject: str, body_text: str, body_html: str
) -> None:
    """经 aiosmtplib 实际发送（事件循环内异步执行，不阻塞）。"""
    cfg = get_config().smtp
    message = EmailMessage()
    message["From"] = formataddr(
        (cfg.from_name, cfg.from_address or cfg.username or "noreply@localhost")
    )
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body_text)
    if body_html:
        message.add_alternative(body_html, subtype="html")

    await aiosmtplib.send(
        message,
        hostname=cfg.host.strip(),
        port=cfg.port,
        username=cfg.username.strip() or None,
        password=cfg.password or None,
        use_tls=cfg.encryption == "ssl",
        start_tls=cfg.encryption == "starttls",
        timeout=30,
    )


def _claim_message(message_id: str) -> bool:
    """原子认领一条 queued 消息（queued → sending），返回是否抢到。

    多实例部署下各副本独立运行发送协程：条件 UPDATE 保证同一封邮件只被
    一个副本投递（先到先得），认领时间戳供崩溃残留回收。
    """
    return (
        db_execute(
            "UPDATE email_outbox SET status = 'sending', claimed_at = ? "
            "WHERE id = ? AND status = 'queued'",
            (utcnow(), message_id),
        )
        == 1
    )


async def _deliver_one(row: dict) -> bool:
    """发送单条队列消息并落库状态。成功 True；失败按重试策略处理（False）。

    调用前置条件：消息已被本副本认领（status='sending'）。失败且未达
    重试上限时恢复 queued 释放认领；达到上限转 failed 留档；成功置 sent。
    """
    try:
        await _smtp_send(
            row["recipient"], row["subject"], row["body_text"], row["body_html"]
        )
    except Exception as e:  # noqa: BLE001 - 任何发送失败都走重试/留档路径
        attempts = int(row["attempts"]) + 1
        retry_limit = get_config().smtp.retry_attempts
        if attempts >= retry_limit:
            db_execute(
                "UPDATE email_outbox SET status = 'failed', attempts = ?, "
                "last_error = ?, claimed_at = NULL WHERE id = ?",
                (attempts, f"{type(e).__name__}: {e}", row["id"]),
            )
            logger.error(
                "Mail delivery failed permanently (id=%s to=%s attempts=%d): %s",
                row["id"],
                row["recipient"],
                attempts,
                e,
            )
        else:
            db_execute(
                "UPDATE email_outbox SET status = 'queued', attempts = ?, "
                "last_error = ?, claimed_at = NULL WHERE id = ?",
                (attempts, f"{type(e).__name__}: {e}", row["id"]),
            )
            logger.warning(
                "Mail delivery failed (id=%s to=%s attempt %d/%d): %s",
                row["id"],
                row["recipient"],
                attempts,
                retry_limit,
                e,
            )
        return False
    db_execute(
        "UPDATE email_outbox SET status = 'sent', attempts = attempts + 1, "
        "sent_at = ?, last_error = NULL, claimed_at = NULL WHERE id = ?",
        (utcnow(), row["id"]),
    )
    logger.info("Mail sent: to=%s subject=%s", row["recipient"], row["subject"])
    return True


async def process_outbox() -> int:
    """消费一轮队列：按 created_at 顺序处理 queued 消息，尊重全局限速与
    指数退避。返回本轮成功投递条数。供 outbox_loop 与测试直接驱动。

    v2 P7：取出后先原子认领（queued → sending），认领失败的行说明另一
    副本正在投递，跳过继续取下一条。
    """
    if not smtp_ready() or not is_initialized():
        return 0
    sent_total = 0
    while True:
        limit = get_config().smtp.max_send_per_minute
        if _sent_count_in_window() >= limit:
            logger.debug("Outbox rate limit reached (%d/min); pausing", limit)
            break
        row = db_query_one(
            "SELECT * FROM email_outbox WHERE status = 'queued' "
            "ORDER BY created_at, id LIMIT 1"
        )
        if row is None:
            break
        if not _claim_message(row["id"]):
            # 已被其他副本认领（status 已非 queued）：下一轮 SELECT 自然
            # 跳过该行，直接继续取下一条
            continue
        if await _deliver_one(row):
            sent_total += 1
        else:
            # 失败：退避后由下一轮重试（消息保持 queued），避免热循环
            attempts = int(row["attempts"]) + 1
            if attempts < get_config().smtp.retry_attempts:
                await asyncio.sleep(_backoff_seconds(attempts))
            break
    return sent_total


async def outbox_loop() -> None:
    """后台发送协程（main.py lifespan 启动/关闭）：周期消费 outbox 队列。"""
    logger.info("Mail outbox loop started")
    while True:
        try:
            await process_outbox()
        except Exception:  # noqa: BLE001 - 循环永不退出
            logger.exception("Mail outbox processing failed")
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


def purge_old_outbox() -> int:
    """清理超过保留期的 sent / failed 邮件留档（并入维护循环）。"""
    if not is_initialized():
        return 0
    cutoff = utcnow_after(-OUTBOX_RETENTION_DAYS * 86400)
    removed = db_execute(
        "DELETE FROM email_outbox WHERE status IN ('sent', 'failed') AND created_at < ?",
        (cutoff,),
    )
    if removed:
        logger.info("Purged %d old outbox mail(s)", removed)
    return removed


def requeue_stale_outbox_claims(
    stale_seconds: int = SENDING_STALE_SECONDS,
) -> int:
    """回收滞留过久的 sending 认领行（副本崩溃残留），恢复 queued 重投。

    v2 P7 多实例：发送前原子认领后若认领者进程死亡，该邮件会永久停留在
    'sending'；维护循环把超过 stale_seconds 仍无进展的认领行放回队列
    （重投至多重复一次"已发出但未及落库"的邮件，属崩溃恢复的合理代价）。
    并入维护循环（持租约者执行）。
    """
    if not is_initialized():
        return 0
    cutoff = utcnow_after(-int(stale_seconds))
    requeued = db_execute(
        "UPDATE email_outbox SET status = 'queued', claimed_at = NULL "
        "WHERE status = 'sending' AND claimed_at IS NOT NULL AND claimed_at < ?",
        (cutoff,),
    )
    if requeued:
        logger.warning(
            "Requeued %d stale sending mail(s) (crashed replica residue)", requeued
        )
    return requeued


# ── 收件人辅助 ──────────────────────────────────────────────────
def _admin_recipients() -> list[dict]:
    """全部 active 管理员中已绑定邮箱者。"""
    return db_query_all(
        "SELECT id, username, email FROM users "
        "WHERE role = 'admin' AND status = 'active' AND email != ''"
    )


def _user_email(user: dict) -> str:
    return (user.get("email") or "").strip()


# ── 事件通知 ────────────────────────────────────────────────────
def notify_pending_registrations() -> int:
    """新用户待审批通知（全部 admin，每 5 分钟聚合为一封）。

    注册成功后调用；去抖状态存 user_alert_state(kind='pending_digest')，
    取全部行 MAX(sent_at) 判定窗口（多 admin 多行不影响全局判定）。
    返回入队邮件数。"""
    if not smtp_ready() or not is_initialized():
        return 0
    pending = db_query_all(
        "SELECT username, created_at FROM users WHERE status = 'pending' "
        "ORDER BY created_at"
    )
    if not pending:
        return 0
    now = utcnow()
    last = db_query_one(
        "SELECT MAX(sent_at) AS last FROM user_alert_state WHERE kind = ?",
        (_KIND_PENDING_DIGEST,),
    )["last"]
    if last and last > utcnow_after(-PENDING_DIGEST_INTERVAL_SECONDS):
        return 0
    usernames = "\n".join(f"  - {row['username']}" for row in pending)
    subject, body = render_template(
        "pending_registration", count=len(pending), usernames=usernames
    )
    queued = 0
    for admin in _admin_recipients():
        if enqueue(admin["email"], subject, body):
            queued += 1
    # 去抖标记：为全部 admin 各写一行（PK 去重；无 admin 邮箱时也留一行，
    # user_id 取空串会违反 FK —— 此时跳过标记，下轮注册重新尝试投递）
    for admin in _admin_recipients():
        db_execute(
            "INSERT INTO user_alert_state (user_id, kind, sent_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, kind) DO UPDATE SET sent_at = excluded.sent_at",
            (admin["id"], _KIND_PENDING_DIGEST, now),
        )
    if queued:
        logger.info("Pending registration digest queued to %d admin(s)", queued)
    return queued


def notify_account_status(user: dict, action: str) -> int:
    """审批通过（approve）/ 拒绝（reject）/ 禁用（disable）即时通知该用户。

    enable 不在方案 §11.2 事件矩阵内，不发信。用户未绑定邮箱时静默跳过。"""
    if action not in ("approve", "reject", "disable"):
        return 0
    kind = {
        "approve": "account_approved",
        "reject": "account_rejected",
        "disable": "account_disabled",
    }[action]
    email = _user_email(user)
    if not email:
        logger.debug("Account status mail skipped (no email): %s", user.get("username"))
        return 0
    subject, body = render_template(
        kind, username=user["username"], site_link=_site_link()
    )
    return 1 if enqueue(email, subject, body) else 0


def notify_admin_password_reset(user: dict, temp_password: str) -> int:
    """管理员重置密码通知：正文含临时密码与改密提醒（即时）。"""
    email = _user_email(user)
    if not email:
        return 0
    subject, body = render_template(
        "admin_password_reset",
        username=user["username"],
        temp_password=temp_password,
        site_link=_site_link(),
    )
    return 1 if enqueue(email, subject, body) else 0


def reset_target_debounced(email: str) -> bool:
    """找回令牌邮件每邮箱 5 分钟 1 次去抖（rate_limit_events 滑动窗口）。

    命中去抖（5 分钟内已发过）返回 True。事件行由 purge_old_rate_limit_events
    按保留期统一清理。"""
    identity = f"reset_target:{email.strip().lower()}"
    window_start = utcnow_after(-RESET_TARGET_DEBOUNCE_SECONDS)
    recent = db_query_one(
        "SELECT COUNT(*) AS n FROM rate_limit_events "
        "WHERE kind = 'pwd_reset' AND identity = ? AND ts >= ?",
        (identity, window_start),
    )["n"]
    if int(recent) > 0:
        return True
    db_execute(
        "INSERT INTO rate_limit_events (kind, identity, ts) VALUES ('pwd_reset', ?, ?)",
        (identity, utcnow()),
    )
    return False


def notify_password_reset_token(user: dict, token: str) -> int:
    """自助找回令牌邮件（即时投递）。每邮箱 5 分钟去抖由调用方在创建令牌
    前经 reset_target_debounced() 判定——避免去抖窗口内累积无用令牌行。"""
    email = _user_email(user)
    if not email:
        logger.debug("Reset token mail skipped (no email)")
        return 0
    subject, body = render_template(
        "password_reset_token",
        username=user["username"],
        reset_link=build_reset_link(token),
    )
    if not _site_base_configured():
        # site_base_url 未配置：链接退化为相对路径，正文附拼接提示
        hint = (
            "\n\n（站点地址未配置，以上为站内相对路径——"
            "请在浏览器中于本站点域名后拼接该路径打开。）"
            if _template_language() == "zh"
            else (
                "\n\n(Site base URL is not configured — the link above is a "
                "site-relative path; append it to this site's domain in your browser.)"
            )
        )
        body += hint
    return 1 if enqueue(email, subject, body) else 0


# ── 配额预警（迟滞）─────────────────────────────────────────────
def _alert_marker(user_id: str, kind: str) -> dict | None:
    return db_query_one(
        "SELECT sent_at, kind FROM user_alert_state WHERE user_id = ? AND kind = ?",
        (user_id, kind),
    )


def _set_alert_marker(user_id: str, kind: str) -> None:
    db_execute(
        "INSERT INTO user_alert_state (user_id, kind, sent_at) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, kind) DO UPDATE SET sent_at = excluded.sent_at",
        (user_id, kind, utcnow()),
    )


def _clear_alert_markers(user_id: str, kinds: list[str]) -> None:
    for kind in kinds:
        db_execute(
            "DELETE FROM user_alert_state WHERE user_id = ? AND kind = ?",
            (user_id, kind),
        )


def _quota_recipients(user: dict, critical: bool) -> list[dict]:
    """预警收件人：该用户（需有邮箱）；critical 额外加全部 admin。"""
    recipients = []
    if _user_email(user):
        recipients.append(user)
    if critical:
        recipients.extend(_admin_recipients())
    return recipients


def _send_quota_alert(
    user: dict, dimension: str, level: str, percent: int, used: int, quota_bytes: int
) -> int:
    kind_name = "quota_critical" if level == _QUOTA_LEVEL_CRITICAL else "quota_warn"
    lang = _template_language()
    subject, body = render_template(
        kind_name,
        username=user["username"],
        percent=percent,
        used=used,
        quota=quota_bytes,
        dimension_label=_DIMENSION_LABELS[lang][dimension],
    )
    queued = 0
    for r in _quota_recipients(user, level == _QUOTA_LEVEL_CRITICAL):
        if enqueue(r["email"], subject, body):
            queued += 1
    return queued


def evaluate_quota_alerts() -> int:
    """配额预警检查（维护循环对账之后调用；读缓存不扫盘）。

    对每个 active 用户的 project / derived 两个维度：
    - 用量 ≥ critical 阈值且标记非 critical → 发 critical（用户 + admin），
      标记升级为 critical；
    - 用量 ≥ warn 阈值且无标记 → 发 warn（用户），标记 warn；
    - 用量 < reset 阈值 → 清除该维度全部标记（迟滞回差，再次越线可重发）。
    实际入队 0 封（收件人均未绑邮箱）时不置任何标记，补绑后下一轮可发出。
    返回入队邮件数。"""
    if not smtp_ready() or not is_initialized():
        return 0
    from backend.core.multiuser.quota import (
        effective_derived_quota,
        effective_project_quota,
    )

    cfg_mu = get_config().multi_user
    warn_pct = cfg_mu.quota_warn_percent
    critical_pct = cfg_mu.quota_critical_percent
    reset_pct = cfg_mu.quota_alert_reset_percent

    users = db_query_all(
        "SELECT id, username, email FROM users WHERE status = 'active'"
    )
    queued = 0
    for user in users:
        project_used = int(
            db_query_one(
                "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM projects "
                "WHERE owner_id = ? AND status = 'active'",
                (user["id"],),
            )["total"]
        )
        derived_used = int(
            db_query_one(
                "SELECT COALESCE(SUM(size_bytes), 0) AS total "
                "FROM user_derived_usage WHERE user_id = ?",
                (user["id"],),
            )["total"]
        )
        usages = {
            "project": (project_used, effective_project_quota(user["id"])),
            "derived": (derived_used, effective_derived_quota(user["id"])),
        }
        for dimension, (used, quota_bytes) in usages.items():
            if quota_bytes <= 0:
                continue
            percent = int(used * 100 / quota_bytes)
            warn_kind = _QUOTA_KIND.format(dimension=dimension, level=_QUOTA_LEVEL_WARN)
            critical_kind = _QUOTA_KIND.format(
                dimension=dimension, level=_QUOTA_LEVEL_CRITICAL
            )
            if percent < reset_pct:
                _clear_alert_markers(user["id"], [warn_kind, critical_kind])
                continue
            marker = _alert_marker(user["id"], critical_kind) or _alert_marker(
                user["id"], warn_kind
            )
            current_level = marker["kind"].rsplit("_", 1)[-1] if marker else None
            if percent >= critical_pct and current_level != _QUOTA_LEVEL_CRITICAL:
                sent = _send_quota_alert(
                    user, dimension, _QUOTA_LEVEL_CRITICAL, percent, used, quota_bytes
                )
                # 零收件人（用户未绑邮箱且 admin 也均未绑）不留标记：
                # 补绑邮箱后下一轮维护可正常发出（同待审批聚合口径）
                if sent:
                    queued += sent
                    _set_alert_marker(user["id"], critical_kind)
            elif percent >= warn_pct and current_level is None:
                sent = _send_quota_alert(
                    user, dimension, _QUOTA_LEVEL_WARN, percent, used, quota_bytes
                )
                if sent:
                    queued += sent
                    _set_alert_marker(user["id"], warn_kind)
    if queued:
        logger.info("Quota alert mails queued: %d", queued)
    return queued
