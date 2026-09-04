"""LLM 每用户治理（v2 P1b §6）：滑动窗口限流 + 并发租约 + 登录限流同表迁移。"""

import asyncio
import json as _json
import os as _os
from unittest import mock as _mock

import pytest

from backend.core.llm_client import LLMClient
from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import db as db_module
from backend.core.multiuser import llm_governance as gov
from backend.core.multiuser import userspace

PAST_ISO = "2000-01-01T00:00:00+00:00"


@pytest.fixture
def mu_env(tmp_path):
    """多用户环境（LLM 阈值经 build_mu_env 参数控制，默认走配置字段）。"""
    from tests.test_multiuser.conftest import build_mu_env, _reset_config_state

    env = build_mu_env(tmp_path)
    yield env
    db_module.close_db()
    _reset_config_state()
    userspace.set_current_user_id(None)


@pytest.fixture
def governed_user():
    """注入请求级当前用户上下文，测试结束清理。"""
    userspace.set_current_user_id("u1")
    yield "u1"
    userspace.set_current_user_id(None)


@pytest.fixture
def mu_env_single_slot(mu_env_factory):
    """并发上限 = 1 的多用户环境（隔离验证每用户租约互斥）。"""
    return mu_env_factory(llm_rate_limit_per_minute=100, llm_max_concurrency_per_user=1)


def _events(kind: str = "llm"):
    return db_module.db_query_all(
        "SELECT * FROM rate_limit_events WHERE kind = ? ORDER BY ts", (kind,)
    )


def _leases():
    return db_module.db_query_all("SELECT * FROM llm_concurrency_leases")


# ── schema 迁移 ──────────────────────────────────────────────────
class TestMigration:
    def test_governance_tables_exist(self, mu_env):
        names = {
            r["name"]
            for r in db_module.db_query_all(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "rate_limit_events" in names
        assert "llm_concurrency_leases" in names

    def test_legacy_v2_db_upgrades_to_v3(self, tmp_path):
        """存量 v2 库（user_version=2）升级到 v3：新表就位、既有数据保留。"""
        import os as _os
        import sqlite3
        from unittest import mock as _mock

        from backend.core import config as cfg_module
        from tests.test_multiuser.conftest import MINIMAL_CONFIG, _reset_config_state

        db_path = tmp_path / "legacy_v2.db"
        conn = sqlite3.connect(str(db_path))
        try:
            # 复刻一个"停在 P1a 的库"：v1 基线 + 沙箱租约表，版本号 2
            db_module._migration_v1_baseline(conn)
            db_module._migration_v2_sandbox_slots(conn)
            conn.execute("PRAGMA user_version = 2")
            conn.execute(
                "INSERT INTO sandbox_slots (slot_id, user_id, acquired_at, "
                "expires_at) VALUES ('s1', 'u1', '2026-01-01T00:00:00+00:00', "
                "'2099-01-01T00:00:00+00:00')"
            )
            conn.commit()
        finally:
            conn.close()

        data = dict(MINIMAL_CONFIG)
        data["multi_user"] = {
            "enabled": True,
            "db_path": str(db_path),
            "users_root": str(tmp_path / "users") + "/",
            "staging_root": str(tmp_path / "staging") + "/",
        }
        config_path = tmp_path / "config.json"
        config_path.write_text(_json.dumps(data), encoding="utf-8")
        with _mock.patch.dict(_os.environ, {}, clear=True):
            cfg_module.load_config(str(config_path))
        try:
            db_module.init_db()
            assert db_module.schema_status()["user_version"] == (
                db_module.SCHEMA_VERSION
            )
            names = {
                r["name"]
                for r in db_module.db_query_all(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            assert {"rate_limit_events", "llm_concurrency_leases"} <= names
            # v2 既有数据完好
            slot = db_module.db_query_one(
                "SELECT * FROM sandbox_slots WHERE slot_id = 's1'"
            )
            assert slot is not None and slot["user_id"] == "u1"
        finally:
            db_module.close_db()
            _reset_config_state()


# ── acquire / release：速率窗口 ───────────────────────────────────
class TestRateWindow:
    def test_admit_records_event_and_lease(self, mu_env, governed_user):
        task_id = gov.acquire("u1")
        assert task_id
        assert len(_events()) == 1
        assert _events()[0]["identity"] == "u1"
        assert len(_leases()) == 1
        gov.release_lease(task_id)
        assert _leases() == []

    def test_over_limit_rejected_with_retry_after(self, mu_env):
        userspace.set_current_user_id("u1")
        # 默认阈值 llm_rate_limit_per_minute=20：造满窗口后拒绝
        # （每次获取后立即释放租约，避免并发上限先行拦截）
        for _ in range(20):
            gov.release_lease(gov.acquire("u1"))
        assert len(_leases()) == 0
        with pytest.raises(RuntimeError) as exc_info:
            gov.acquire("u1")
        err = exc_info.value
        assert gov.governance_code_of(err) == gov.LLM_RATE_LIMITED
        assert 1 <= getattr(err, "retry_after") <= 61
        # 被拒绝的调用不落行：窗口只统计已准入调用
        assert len(_events()) == 20
        # 其他用户不受影响
        gov.acquire("u2")

    def test_window_slide_frees_budget(self, mu_env):
        userspace.set_current_user_id("u1")
        for _ in range(20):
            gov.release_lease(gov.acquire("u1"))
        with pytest.raises(RuntimeError) as exc_info:
            gov.acquire("u1")
        assert gov.governance_code_of(exc_info.value) == gov.LLM_RATE_LIMITED
        # 窗口整体滑出（事件过期）后预算恢复
        db_module.db_execute("UPDATE rate_limit_events SET ts = ?", (PAST_ISO,))
        assert gov.acquire("u1")


# ── acquire / release：并发租约 ───────────────────────────────────
class TestConcurrencyLease:
    def test_per_user_limit_no_queueing(self, mu_env_single_slot):
        t1 = gov.acquire("u1")
        with pytest.raises(RuntimeError) as exc_info:
            gov.acquire("u1")
        err = exc_info.value
        assert gov.governance_code_of(err) == gov.LLM_CONCURRENCY_LIMITED
        assert getattr(err, "retry_after") >= 1
        # 其他用户不受该用户的并发限制影响
        assert gov.acquire("u2")
        gov.release_lease(t1)

    def test_release_frees_slot(self, mu_env_single_slot):
        t1 = gov.acquire("u1")
        gov.release_lease(t1)
        assert gov.acquire("u1")

    def test_release_idempotent(self, mu_env):
        t1 = gov.acquire("u1")
        gov.release_lease(t1)
        gov.release_lease(t1)

    def test_expired_lease_does_not_block(self, mu_env_single_slot):
        gov.acquire("u1")
        db_module.db_execute(
            "UPDATE llm_concurrency_leases SET expires_at = ?", (PAST_ISO,)
        )
        assert gov.acquire("u1")

    def test_rate_quota_not_consumed_by_concurrency_reject(self, mu_env_single_slot):
        """并发超限被拒时回滚整个事务：不落速率事件。"""
        t1 = gov.acquire("u1")
        with pytest.raises(RuntimeError):
            gov.acquire("u1")
        assert len(_events()) == 1
        gov.release_lease(t1)


# ── 维护清理 ─────────────────────────────────────────────────────
class TestPurge:
    def test_purge_expired_llm_leases(self, mu_env):
        live = gov.acquire("u1")
        dead = gov.acquire("u2")
        db_module.db_execute(
            "UPDATE llm_concurrency_leases SET expires_at = ? WHERE task_id = ?",
            (PAST_ISO, dead),
        )
        removed = gov.purge_expired_llm_leases()
        assert removed == 1
        assert [r["task_id"] for r in _leases()] == [live]

    def test_purge_old_rate_limit_events_keeps_recent(self, mu_env):
        gov.acquire("u1")
        db_module.db_execute(
            "INSERT INTO rate_limit_events (kind, identity, ts) "
            "VALUES ('login', 'pair:a|1.2.3.4', ?)",
            (PAST_ISO,),
        )
        removed = gov.purge_old_rate_limit_events()
        assert removed == 1
        assert len(_events("llm")) == 1
        assert _events("login") == []


# ── admission_gate：注入点行为 ────────────────────────────────────
class TestAdmissionGate:
    @pytest.mark.asyncio
    async def test_skips_in_single_user_mode_without_db(self, tmp_path):
        """单用户模式直通：不依赖数据库初始化（未 init_db 也必须可用）。"""
        from tests.test_multiuser.conftest import build_mu_env, _reset_config_state

        try:
            build_mu_env(tmp_path, enabled=False)
            userspace.set_current_user_id(None)
            async with gov.admission_gate() as task_id:
                assert task_id is None
        finally:
            _reset_config_state()

    @pytest.mark.asyncio
    async def test_skips_without_user_context(self, mu_env):
        """多用户模式但无请求级用户上下文（后台任务等）→ 直通且不落行。"""
        userspace.set_current_user_id(None)
        async with gov.admission_gate() as task_id:
            assert task_id is None
        assert _events() == []
        assert _leases() == []

    @pytest.mark.asyncio
    async def test_acquires_and_releases(self, mu_env, governed_user):
        async with gov.admission_gate() as task_id:
            assert task_id
            assert len(_leases()) == 1
        assert _leases() == []

    @pytest.mark.asyncio
    async def test_releases_on_error(self, mu_env, governed_user):
        with pytest.raises(ValueError):
            async with gov.admission_gate():
                raise ValueError("boom")
        assert _leases() == []

    @pytest.mark.asyncio
    async def test_stream_close_releases(self, mu_env, governed_user):
        """消费方提前关闭（GeneratorExit 路径）时 finally 仍释放租约。"""
        cm = gov.admission_gate()
        task_id = await cm.__aenter__()
        assert task_id
        assert len(_leases()) == 1
        # 模拟异步生成器被 aclose：以 GeneratorExit 退出上下文
        await cm.__aexit__(GeneratorExit, None, None)
        assert _leases() == []


# ── admission_exempt_scope：管理面诊断豁免（部署收口阶段四）──────────
class TestAdmissionExemptScope:
    """POST /api/llm/test 的治理豁免门（deploy-followups-plan §4.4）。

    锁定三个契约：作用域内挂起请求级用户上下文、异常路径恢复、
    ContextVar 按异步任务隔离（并发任务不受豁免影响）。
    """

    @pytest.mark.asyncio
    async def test_suspends_and_restores_user_context(self):
        """作用域内当前用户挂起为 None，退出后恢复原值。"""
        userspace.set_current_user_id("u1")
        try:
            async with gov.admission_exempt_scope():
                assert userspace.get_current_user_id() is None
            assert userspace.get_current_user_id() == "u1"
        finally:
            userspace.set_current_user_id(None)

    @pytest.mark.asyncio
    async def test_restores_on_exception(self):
        """作用域内抛异常：异常向上传播，用户上下文仍被 finally 恢复。"""
        userspace.set_current_user_id("u1")
        try:
            with pytest.raises(RuntimeError, match="boom"):
                async with gov.admission_exempt_scope():
                    raise RuntimeError("boom")
            assert userspace.get_current_user_id() == "u1"
        finally:
            userspace.set_current_user_id(None)

    @pytest.mark.asyncio
    async def test_concurrent_task_isolation(self):
        """豁免按任务隔离：并发任务在豁免窗口内的上下文不受影响。"""
        userspace.set_current_user_id("uA")
        entered = asyncio.Event()
        seen: dict[str, str | None] = {}

        async def exempt_task():
            async with gov.admission_exempt_scope():
                entered.set()  # 通知观察者：已进入豁免窗口
                await asyncio.sleep(0.05)
                seen["inside_exempt"] = userspace.get_current_user_id()
            seen["after_exempt"] = userspace.get_current_user_id()

        async def observer_task():
            await entered.wait()
            seen["other_during_exempt"] = userspace.get_current_user_id()

        try:
            await asyncio.gather(exempt_task(), observer_task())
        finally:
            userspace.set_current_user_id(None)

        assert seen["inside_exempt"] is None
        assert seen["after_exempt"] == "uA"
        # 观察者任务在豁免窗口内读取：仍是自己的 uA，未被豁免波及
        assert seen["other_during_exempt"] == "uA"

    @pytest.mark.asyncio
    async def test_gate_governed_only_outside_scope(self, mu_env, governed_user):
        """admission_gate 仅在豁免作用域内直通，作用域外照常治理。"""
        async with gov.admission_exempt_scope():
            async with gov.admission_gate() as task_id:
                assert task_id is None
        assert _events() == []
        assert _leases() == []
        # 作用域外：治理恢复生效（限流事件落账，租约随调用结束释放）
        async with gov.admission_gate() as task_id:
            assert task_id is not None
        assert len(_events()) == 1
        assert _events()[0]["identity"] == "u1"
        assert _leases() == []


# ── llm_client 注入集成 ──────────────────────────────────────────
def _stub_impl(client, impl):
    """替换 LLMClient 内部实现桩（治理门在桩外层，保持生效）。"""
    client._chat_with_usage_impl = impl  # noqa: SLF001 测试桩


class TestLLMClientIntegration:
    @pytest.mark.asyncio
    async def test_chat_passes_gate_when_governed(self, mu_env, governed_user):
        """治理生效时每次 chat 落一条限流事件，租约随调用结束释放。"""
        client = LLMClient()

        async def fake_impl(**kwargs):
            counts = gov.active_counts("u1")
            assert counts["leases"] == 1  # 租约在调用期间持有
            return "ok", None

        _stub_impl(client, fake_impl)
        assert await client.chat([{"role": "user", "content": "hi"}]) == "ok"
        assert len(_events()) == 1
        assert _leases() == []

    @pytest.mark.asyncio
    async def test_chat_bypasses_gate_when_single_user(self, tmp_path):
        """单用户模式：无库初始化也照常工作（治理零介入）。"""
        from backend.core import config as cfg_module
        from tests.test_multiuser.conftest import MINIMAL_CONFIG, _reset_config_state

        data = dict(MINIMAL_CONFIG)
        data["multi_user"] = {"enabled": False}
        config_path = tmp_path / "config.json"
        config_path.write_text(_json.dumps(data), encoding="utf-8")
        with _mock.patch.dict(_os.environ, {}, clear=True):
            cfg_module.load_config(str(config_path))
        try:
            client = LLMClient()

            async def fake_impl(**kwargs):
                return "ok", None

            _stub_impl(client, fake_impl)
            assert await client.chat([{"role": "user", "content": "hi"}]) == "ok"
        finally:
            _reset_config_state()

    @pytest.mark.asyncio
    async def test_rate_limit_surfaces_from_chat(self, tmp_path):
        """达到每分钟上限后，chat 抛出携带 gov_code 的 429 语义错误。"""
        from tests.test_multiuser.conftest import build_mu_env, _reset_config_state

        try:
            build_mu_env(
                tmp_path, llm_rate_limit_per_minute=1, llm_max_concurrency_per_user=4
            )
            client = LLMClient()

            async def fake_impl(**kwargs):
                return "ok", None

            _stub_impl(client, fake_impl)
            userspace.set_current_user_id("u1")
            assert await client.chat([{"role": "user", "content": "hi"}]) == "ok"
            with pytest.raises(RuntimeError) as exc_info:
                await client.chat([{"role": "user", "content": "hi"}])
            assert gov.governance_code_of(exc_info.value) == gov.LLM_RATE_LIMITED
            # 失败路径不残留租约
            assert _leases() == []
        finally:
            db_module.close_db()
            _reset_config_state()
            userspace.set_current_user_id(None)

    @pytest.mark.asyncio
    async def test_concurrency_limit_surfaces_from_parallel_chats(self, tmp_path):
        """并发超限不排队：并行调用中恰有一个成功、一个被拒且不残留租约。"""
        from tests.test_multiuser.conftest import build_mu_env, _reset_config_state

        try:
            build_mu_env(
                tmp_path, llm_rate_limit_per_minute=100, llm_max_concurrency_per_user=1
            )
            client = LLMClient()

            async def slow_impl(**kwargs):
                await asyncio.sleep(0.05)
                return "ok", None

            _stub_impl(client, slow_impl)
            userspace.set_current_user_id("u1")
            results = await asyncio.gather(
                client.chat([{"role": "user", "content": "a"}]),
                client.chat([{"role": "user", "content": "b"}]),
                return_exceptions=True,
            )
            ok = [r for r in results if not isinstance(r, BaseException)]
            failed = [r for r in results if isinstance(r, BaseException)]
            assert len(ok) == 1
            assert len(failed) == 1
            assert gov.governance_code_of(failed[0]) == gov.LLM_CONCURRENCY_LIMITED
            assert _leases() == []
        finally:
            db_module.close_db()
            _reset_config_state()
            userspace.set_current_user_id(None)

    @pytest.mark.asyncio
    async def test_stream_release_on_early_close(self, mu_env, governed_user):
        """流式入口：提前关闭生成器（GeneratorExit 路径）后租约必须释放。"""
        client = LLMClient()

        async def fake_stream_impl(**kwargs):
            for i in range(5):
                yield f"chunk-{i}"

        client._chat_stream_impl = fake_stream_impl  # noqa: SLF001 测试桩
        gen = client.chat_stream([{"role": "user", "content": "hi"}])
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)
            if chunk == "chunk-1":
                break
        await gen.aclose()
        assert chunks == ["chunk-0", "chunk-1"]
        assert _leases() == []


# ── 错误翻译契约 ─────────────────────────────────────────────────
class TestGovernanceHttpResponse:
    def test_maps_to_429_with_retry_after_header(self):
        err = gov.make_governance_error(
            gov.LLM_RATE_LIMITED, "LLM rate limit exceeded; retry after 42s", 42
        )
        resp = gov.governance_http_response(err)
        assert resp is not None
        assert resp.status_code == 429
        assert resp.headers["retry-after"] == "42"
        # 应用既有的结构化错误约定：payload 包在 detail 里（前端 request()
        # 只读 err.detail，并取 detail.error 挂到 error.code 供按码分支）
        payload = _json.loads(bytes(resp.body))
        detail = payload["detail"]
        assert detail["error"] == gov.LLM_RATE_LIMITED
        assert detail["retry_after"] == 42

    def test_non_governance_error_returns_none(self):
        assert gov.governance_http_response(RuntimeError("plain")) is None
        assert gov.governance_http_response(ValueError("x")) is None

    def test_retry_after_floor_is_one(self):
        err = gov.make_governance_error(gov.LLM_RATE_LIMITED, "msg", 0)
        assert getattr(err, "retry_after") == 1


# ── 路由级集成：真实注册路径（main.py 与测试共用 register_governance_handler）──
class TestGovernanceHttpIntegration:
    @staticmethod
    def _build_app():
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/api/governance-limited")
        async def limited():
            raise gov.make_governance_error(
                gov.LLM_RATE_LIMITED,
                "LLM rate limit exceeded (21/20 per 60s); retry after 42s",
                42,
            )

        @app.get("/api/governance-busy")
        async def busy():
            raise gov.make_governance_error(
                gov.LLM_CONCURRENCY_LIMITED,
                "LLM concurrency limit reached (2/2); retry after 900s",
                900,
            )

        @app.get("/api/plain-error")
        async def plain():
            raise RuntimeError("plain boom")

        gov.register_governance_handler(app)
        return app

    def test_limited_route_returns_429_detail_and_header(self):
        from fastapi.testclient import TestClient

        client = TestClient(self._build_app(), raise_server_exceptions=False)
        r = client.get("/api/governance-limited")
        assert r.status_code == 429
        assert r.headers["retry-after"] == "42"
        body = r.json()["detail"]
        assert body["error"] == gov.LLM_RATE_LIMITED
        assert body["retry_after"] == 42

    def test_busy_route_reports_concurrency_code(self):
        from fastapi.testclient import TestClient

        client = TestClient(self._build_app(), raise_server_exceptions=False)
        r = client.get("/api/governance-busy")
        assert r.status_code == 429
        assert r.json()["detail"]["error"] == gov.LLM_CONCURRENCY_LIMITED

    def test_plain_runtime_error_keeps_default_500_path(self):
        """不带 gov_code 的普通 RuntimeError 维持默认 ServerErrorMiddleware 路径。"""
        from fastapi.testclient import TestClient

        client = TestClient(self._build_app(), raise_server_exceptions=False)
        r = client.get("/api/plain-error")
        assert r.status_code == 500


# ── 中间件全局上下文注入（C-1 回归）────────────────────────────────
class TestMiddlewareContextInjection:
    """
    会话认证成功后由 AccessSecurityMiddleware 全局注入请求级用户上下文，
    治理因此覆盖全部 /api 端点（而非仅声明 current_user_context 的路由）。
    """

    @staticmethod
    def _build_probe_app():
        from fastapi import FastAPI

        from backend.core.security_middleware import AccessSecurityMiddleware
        from backend.modules.auth.router import admin_router, auth_router

        app = FastAPI()
        app.add_middleware(AccessSecurityMiddleware)

        @app.get("/api/governance-probe")
        async def probe():
            async with gov.admission_gate() as task_id:
                return {"governed": task_id is not None}

        app.include_router(auth_router)
        app.include_router(admin_router)
        return app

    def test_authenticated_request_is_governed(self, mu_env):
        from fastapi.testclient import TestClient

        client = TestClient(self._build_probe_app())
        # 直接建 active 用户（绕开审批流），走真实 /api/auth/login 获取会话
        user = auth_core.create_user(
            "reviewer", "review-pass-123", role="user", status="active"
        )
        r = client.post(
            "/api/auth/login",
            json={"username": "reviewer", "password": "review-pass-123"},
        )
        assert r.status_code == 200
        # 登录后 GET 探针：中间件已注入上下文 → 治理生效（task_id 非空）
        r = client.get("/api/governance-probe")
        assert r.status_code == 200
        assert r.json()["governed"] is True
        # 且限流事件以该用户身份落账
        events = _events("llm")
        assert len(events) == 1
        assert events[0]["identity"] == user["id"]

    def test_unauthenticated_request_skips_governance(self, mu_env):
        from fastapi.testclient import TestClient

        client = TestClient(self._build_probe_app())
        r = client.get("/api/governance-probe")
        # 未登录会被中间件 401 拦截（探针不会执行），此处验证拦截本身
        assert r.status_code == 401
        assert _events("llm") == []


# ── 登录限流同表迁移（kind='login'）───────────────────────────────
class TestLoginLimiterMigration:
    def test_records_pair_and_user_identities(self, mu_env):
        assert auth_core.login_rate_limited("Alice", "1.2.3.4") is False
        rows = _events("login")
        identities = sorted(r["identity"] for r in rows)
        assert identities == ["pair:alice|1.2.3.4", "user:alice"]

    def test_username_global_dimension_blocks_other_ip(self, mu_env):
        # login_limit=5：同一用户名跨 IP 计数共享全局维度
        for i in range(5):
            assert auth_core.login_rate_limited("alice", f"10.0.0.{i + 1}") is False
        assert auth_core.login_rate_limited("alice", "10.9.9.9") is True
        # 其他用户名不受影响
        assert auth_core.login_rate_limited("bob", "10.0.0.1") is False

    def test_window_expiry_restores_budget(self, mu_env):
        assert auth_core.login_rate_limited("alice", "1.2.3.4") is False
        db_module.db_execute("UPDATE rate_limit_events SET ts = ?", (PAST_ISO,))
        assert auth_core.login_rate_limited("alice", "1.2.3.4") is False

    def test_reset_clears_rows(self, mu_env):
        auth_core.login_rate_limited("alice", "1.2.3.4")
        auth_core.reset_login_rate_limits()
        assert _events("login") == []

    def test_reset_safe_without_db(self):
        """库未初始化（单用户模式进程内调用）时不抛错。"""
        assert db_module.is_initialized() is False
        auth_core.reset_login_rate_limits()

    def test_llm_and_login_events_isolated(self, mu_env, governed_user):
        """同表共存：kind 维度互不干扰。"""
        gov.acquire("u1")
        auth_core.login_rate_limited("alice", "1.2.3.4")
        assert len(_events("llm")) == 1
        assert len(_events("login")) == 2
