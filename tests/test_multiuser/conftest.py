"""
多用户模式 P1 测试共享 fixtures。

mu_env：构造指向临时目录的已启用多用户配置 + 初始化数据库；
mu_client：带 AccessSecurityMiddleware + 认证路由的 TestClient。
"""

import json
import os
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import config as cfg_module
from backend.core.multiuser import auth as auth_module
from backend.core.multiuser import db as db_module
from backend.core.multiuser import userspace
from backend.core.security_middleware import AccessSecurityMiddleware

MINIMAL_CONFIG = {
    "server": {
        "host": "127.0.0.1",
        "port": 8000,
        "dev_mode": True,
        "allowed_origins": ["http://localhost:5173"],
    },
    "llm": {"api_key": "test-key", "base_url": "https://x/v1", "model": "m"},
    "logging": {"level": "INFO", "dir": "logs/"},
}

CSRF_HEADER = "X-Latexcellence-CSRF"
CSRF_COOKIE = "latexcellence_csrf"


@pytest.fixture(autouse=True)
def _clean_rate_limits():
    """清除进程内登录限流记录，避免测试之间相互影响。"""
    auth_module.reset_login_rate_limits()
    yield
    auth_module.reset_login_rate_limits()


@pytest.fixture(autouse=True)
def _reset_current_user_context():
    """请求级当前用户 ContextVar 在同步测试线程内不会自动复位，
    测试结束后显式清空，避免 actor 身份泄漏到后续用例。"""
    yield
    userspace.set_current_user_id(None)


def _reset_config_state():
    cfg_module._config = None
    cfg_module._config_path = None
    cfg_module._secrets_from_env = set()


def build_mu_app() -> FastAPI:
    """构造多用户测试应用：多用户中间件 + 认证/管理路由 + 一个受保护端点。"""
    from backend.modules.auth.router import admin_router, auth_router

    app = FastAPI()
    app.add_middleware(AccessSecurityMiddleware)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/config/status")
    async def config_status():
        return {"multi_user": True}

    @app.get("/api/protected")
    async def protected():
        # 该端点无依赖，仅用于验证中间件强制会话认证 / CSRF 行为
        return {"ok": True}

    from fastapi import Depends

    from backend.core.multiuser.dependencies import require_admin_if_multiuser

    @app.get("/api/config")
    async def get_config_route(_: None = Depends(require_admin_if_multiuser)):
        # 模拟敏感配置端点：多用户模式仅管理员可访问，单用户模式放行
        return {"secret": "shh"}

    app.include_router(auth_router)
    app.include_router(admin_router)
    return app


def build_mu_env(
    tmp_path,
    *,
    enabled: bool = True,
    registration_enabled: bool = True,
    login_limit: int = 5,
    llm_rate_limit_per_minute: int | None = None,
    llm_max_concurrency_per_user: int | None = None,
    mu_extra: dict | None = None,
    smtp_extra: dict | None = None,
) -> dict:
    """写入并加载多用户配置，初始化数据库。返回环境字典。

    mu_extra：追加/覆盖 multi_user 节字段（如分片上传测试的
    chunk_part_max_mb），键须为 MultiUserConfig 合法字段。
    smtp_extra：追加/覆盖 smtp 节字段（v2 P6 邮件测试；None 时使用默认值
    ——enabled=False，即全部通知静默跳过的降级形态）。"""
    db_path = tmp_path / "multiuser.db"
    users_root = tmp_path / "users"
    staging = tmp_path / "staging"
    data = dict(MINIMAL_CONFIG)
    mu_config = {
        "enabled": enabled,
        "db_path": str(db_path),
        "users_root": str(users_root) + "/",
        "staging_root": str(staging) + "/",
        "default_project_quota_mb": 500,
        "default_derived_quota_mb": 100,
        "session_ttl_days": 7,
        "remember_ttl_days": 30,
        "login_rate_limit_per_minute": login_limit,
        "registration_enabled": registration_enabled,
    }
    if llm_rate_limit_per_minute is not None:
        mu_config["llm_rate_limit_per_minute"] = llm_rate_limit_per_minute
    if llm_max_concurrency_per_user is not None:
        mu_config["llm_max_concurrency_per_user"] = llm_max_concurrency_per_user
    if mu_extra:
        mu_config.update(mu_extra)
    data["multi_user"] = mu_config
    smtp_config = {
        "enabled": False,
        "host": "",
        "port": 465,
        "encryption": "none",
        "from_address": "noreply@latexcellence.test",
        "from_name": "LateXcellence Test",
        "site_base_url": "",
        "max_send_per_minute": 100,
        "retry_attempts": 3,
    }
    if smtp_extra:
        smtp_config.update(smtp_extra)
    data["smtp"] = smtp_config
    config_path = tmp_path / "config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    with mock.patch.dict(os.environ, {}, clear=True):
        cfg_module.load_config(str(config_path))
    if enabled:
        db_module.init_db()
    env = {
        "tmp_path": tmp_path,
        "db_path": db_path,
        "users_root": users_root,
        "staging_root": staging,
        "config_path": config_path,
    }
    return env


@pytest.fixture
def mu_env(tmp_path):
    """已启用多用户的临时环境（含已初始化数据库）。"""
    env = build_mu_env(tmp_path)
    yield env
    db_module.close_db()
    _reset_config_state()


@pytest.fixture
def mu_env_factory(tmp_path):
    """工厂 fixture：可自定义构建多用户环境，测试结束后清理。"""
    created = []

    def factory(**kwargs) -> dict:
        env = build_mu_env(tmp_path, **kwargs)
        created.append(env)
        return env

    yield factory
    db_module.close_db()
    _reset_config_state()


@pytest.fixture
def mu_client(mu_env):
    """带认证路由与中间件的 TestClient。"""
    return TestClient(build_mu_app())


def csrf(client, headers: dict | None = None) -> dict:
    """为变更类请求附加与客户端 CSRF Cookie 值一致的请求头（双提交 CSRF）。"""
    h = dict(headers or {})
    token = client.cookies.get(CSRF_COOKIE, "")
    h[CSRF_HEADER] = token
    return h


# ── v2 P6 邮件测试支持 ─────────────────────────────────────────
class _MailCollector:
    """aiosmtpd DATA 处理器：收集投递的信封（content / mail_from / rcpt_tos）。"""

    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(
            {
                "content": envelope.content.decode("utf-8", errors="replace"),
                "mail_from": envelope.mail_from,
                "rcpt_tos": list(envelope.rcpt_tos),
            }
        )
        return "250 Message accepted"


@pytest.fixture
def smtp_server():
    """本地 mock SMTP server（aiosmtpd Controller，随机空闲端口）。

    yield {"port": int, "messages": [envelope_dict]}；测试经 messages 断言
    投递结果。失败注入用 fail_times：handle_DATA 前 N 次返回临时失败。
    """
    import socket

    from aiosmtpd.controller import Controller

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    collector = _MailCollector()
    controller = Controller(collector, hostname="127.0.0.1", port=port)
    controller.start()
    try:
        yield {"port": port, "messages": collector.messages}
    finally:
        controller.stop()


def smtp_enabled_extra(port: int, **overrides) -> dict:
    """构造指向本地 mock SMTP 的 smtp 节配置（供 build_mu_env(smtp_extra=...)）。"""
    cfg = {
        "enabled": True,
        "host": "127.0.0.1",
        "port": port,
        "encryption": "none",
    }
    cfg.update(overrides)
    return cfg


def last_outbox_row() -> dict | None:
    """读取最近一条 outbox 行（找回令牌测试从 body_text 解析明文令牌）。"""
    from backend.core.multiuser.db import db_query_one

    return db_query_one(
        "SELECT * FROM email_outbox ORDER BY created_at DESC, id DESC LIMIT 1"
    )
