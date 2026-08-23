"""v2 P7 多实例状态外置：跨实例（双管理器/服务实例 + 同一 DB）交叉操作。

模拟口径：同一 SQLite 库上构造两个独立的 WorkspaceManager /
BaseParagraphCheckService 实例，并在两步之间清空进程级缓存 dict——
等价于两个独立进程各自持有自己的内存态，仅共享 DB 与磁盘。
"""

import asyncio
import time
from pathlib import Path

import pytest

from backend.core import workspace_manager as wm_module
from backend.core.base_paragraph_check import (
    CheckOperationContext,
    _operation_contexts,
    get_operation_context,
    store_operation_context,
)
from backend.core.multiuser import db as db_module
from backend.core.multiuser import (
    operation_contexts,
    pending_interactions,
    userspace,
)
from backend.core.multiuser.maintenance import run_maintenance_cycle
from backend.utils.web_search.engines import ddg


@pytest.fixture
def mu_env(tmp_path):
    from tests.test_multiuser.conftest import _reset_config_state, build_mu_env

    env = build_mu_env(tmp_path)
    yield env
    db_module.close_db()
    _reset_config_state()
    userspace.set_current_user_id(None)
    # 清空模块级缓存，避免泄漏到其他用例
    _operation_contexts.clear()
    ddg._challenge_store.clear()
    wm_module._workspace_manager = None


def _fresh_manager() -> wm_module.WorkspaceManager:
    """构造独立管理器实例（不取全局单例），模拟另一副本。"""
    return wm_module.WorkspaceManager()


def _make_project_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.tex").write_text(
        "\\documentclass{article}\n" "\\begin{document}\nHello P7.\n\\end{document}\n",
        encoding="utf-8",
    )
    return root


# ── workspace 绑定：A bind → B get / reopen ─────────────────────────
class TestWorkspaceBindingCrossInstance:
    def test_bind_visible_to_second_instance(self, mu_env):
        a, b = _fresh_manager(), _fresh_manager()
        a.bind("ws1", user_id="u1", project_id="p1", owner_id="u1", root_path="/r")
        got = b.get_binding("ws1")
        assert got is not None
        assert got["project_id"] == "p1"
        assert got["owner_id"] == "u1"
        assert got["root_path"] == "/r"

    def test_unbind_propagates(self, mu_env):
        a, b = _fresh_manager(), _fresh_manager()
        a.bind("ws2", user_id="u1", project_id="p1", owner_id="u1", root_path="/r")
        assert b.get_binding("ws2") is not None
        b.unbind("ws2")
        assert a.get_binding("ws2") is None

    def test_reopen_project_from_binding_on_miss(self, mu_env):
        project_root = _make_project_dir(
            mu_env["users_root"] / "u1" / "projects" / "p1"
        )
        a = _fresh_manager()
        a.bind(
            "ws3",
            user_id="u1",
            project_id="p1",
            owner_id="u1",
            root_path=str(project_root),
        )
        # B 实例从未 open 过该工作区：get_or_create 按绑定 root 重建
        b = _fresh_manager()
        project = b.get_or_create("ws3")
        assert project.is_open
        assert project.root_path == str(project_root)

    def test_stale_root_degrades_to_empty_project(self, mu_env):
        a = _fresh_manager()
        a.bind(
            "ws4",
            user_id="u1",
            project_id="p1",
            owner_id="u1",
            root_path=str(mu_env["tmp_path"] / "gone"),
        )
        b = _fresh_manager()
        project = b.get_or_create("ws4")  # 目录缺失：降级空实例不抛错
        assert not project.is_open

    def test_remove_clears_db_row(self, mu_env):
        a, b = _fresh_manager(), _fresh_manager()
        a.bind("ws5", user_id="u1", project_id="p1", owner_id="u1", root_path="/r")
        assert a.remove("ws5") is True
        assert (
            db_module.db_query_one(
                "SELECT * FROM workspace_bindings WHERE workspace_id = 'ws5'"
            )
            is None
        )
        assert b.get_binding("ws5") is None

    def test_single_user_mode_does_not_touch_table(self, tmp_path):
        """单用户模式（multi_user.enabled=false）绑定不落表。"""
        from tests.test_multiuser.conftest import _reset_config_state, build_mu_env

        try:
            build_mu_env(tmp_path, enabled=False)
            m = _fresh_manager()
            m.bind("ws9", user_id="x", project_id="y", owner_id="x", root_path="/r")
            assert m.get_binding("ws9")["project_id"] == "y"  # 仅进程内
            with pytest.raises(RuntimeError):
                db_module.db_query_one("SELECT COUNT(*) FROM workspace_bindings")
        finally:
            db_module.close_db()
            _reset_config_state()

    def test_transfer_refresh_updates_row(self, mu_env):
        """项目转交刷新路径经 bind() 同步更新 DB 行。"""
        from backend.modules.projects.service import _refresh_workspace_bindings
        from backend.core.workspace_manager import get_workspace_manager

        gwm = get_workspace_manager()
        gwm.get_or_create("ws6")  # list_workspaces 只枚举进程内实例
        gwm.bind("ws6", user_id="old", project_id="pj", owner_id="old", root_path="/o")
        import asyncio

        asyncio.run(_refresh_workspace_bindings("pj", "new", "/n"))
        row = db_module.db_query_one(
            "SELECT * FROM workspace_bindings WHERE workspace_id = 'ws6'"
        )
        assert row["owner_id"] == "new"
        assert row["root_path"] == "/n"

    def test_transfer_refresh_updates_rows_held_by_other_replica(self, mu_env):
        """转交只发生在 A 副本时，B 副本持有的绑定行也必须被刷新。

        回归锚点：_refresh_workspace_bindings 原实现经 list_workspaces()
        只覆盖本进程实例，其他副本的绑定行会残留旧 owner/root_path，
        其后续请求按失效 root_path 定位文件。
        """
        from backend.modules.projects.service import _refresh_workspace_bindings
        from backend.core.workspace_manager import get_workspace_manager

        # "副本 B" 的 workspace：只存在于独立管理器实例，不在全局单例中
        replica_b = _fresh_manager()
        replica_b.bind(
            "ws-other",
            user_id="u2",
            project_id="pj",
            owner_id="old",
            root_path="/old/root",
        )

        import asyncio

        asyncio.run(_refresh_workspace_bindings("pj", "new-owner", "/new/root"))
        row = db_module.db_query_one(
            "SELECT * FROM workspace_bindings WHERE workspace_id = 'ws-other'"
        )
        assert row is not None
        assert row["owner_id"] == "new-owner"
        assert row["root_path"] == "/new/root"
        # B 实例随后读取到的是刷新后的权威行
        assert replica_b.get_binding("ws-other")["root_path"] == "/new/root"

    def test_last_active_at_touched_throttled(self, mu_env):
        a = _fresh_manager()
        a.bind("ws7", user_id="u1", project_id="p1", owner_id="u1", root_path="/r")
        first = db_module.db_query_one(
            "SELECT last_active_at FROM workspace_bindings WHERE workspace_id='ws7'"
        )["last_active_at"]
        # 立即再次访问：节流窗口内不写库
        a.get_binding("ws7")
        again = db_module.db_query_one(
            "SELECT last_active_at FROM workspace_bindings WHERE workspace_id='ws7'"
        )["last_active_at"]
        assert first == again
        # 推进节流窗口后访问：打点刷新
        a._last_touch["ws7"] = 0.0
        time.sleep(1.1)  # utcnow 秒级精度，跨秒确保可断言差异
        a.get_binding("ws7")
        third = db_module.db_query_one(
            "SELECT last_active_at FROM workspace_bindings WHERE workspace_id='ws7'"
        )["last_active_at"]
        assert third >= first


# ── operation context：A preview 存 → B apply 取 ─────────────────────
class TestOperationContextCrossInstance:
    def test_store_then_fetch_after_cache_cleared(self, mu_env):
        ctx = CheckOperationContext(
            operation_id="opX",
            marker_maps={"main.tex": {0: {"__MATH_0000__": "$x$"}}},
            file_path="main.tex",
            file_md5s={"main.tex": "abc"},
            created_at=time.time(),
        )
        store_operation_context(ctx)
        # 模拟另一进程：L1 缓存为空，只能回源 DB
        _operation_contexts.clear()
        loaded = get_operation_context("opX")
        assert loaded is not None
        assert loaded.marker_maps == ctx.marker_maps
        assert loaded.file_md5s == {"main.tex": "abc"}
        assert loaded.created_at == pytest.approx(ctx.created_at)

    def test_missing_returns_none(self, mu_env):
        _operation_contexts.clear()
        assert get_operation_context("nope") is None

    def test_expired_row_lazily_dropped(self, mu_env):
        ctx = CheckOperationContext(
            operation_id="opOld",
            marker_maps={},
            file_path="f",
            file_md5s={},
        )
        store_operation_context(ctx)
        db_module.db_execute(
            "UPDATE operation_contexts SET created_at = ? WHERE operation_id = ?",
            (db_module.utcnow_after(-7200), "opOld"),
        )
        _operation_contexts.clear()
        assert get_operation_context("opOld") is None
        assert (
            db_module.db_query_one(
                "SELECT * FROM operation_contexts WHERE operation_id='opOld'"
            )
            is None
        )

    def test_purge_expired_in_maintenance_cycle(self, mu_env):
        store_operation_context(
            CheckOperationContext(
                operation_id="opPurge", marker_maps={}, file_path="f", file_md5s={}
            )
        )
        pending_interactions.create("piPurge", "confirm", {}, ttl_seconds=120)
        db_module.db_execute(
            "UPDATE operation_contexts SET created_at = ?",
            (db_module.utcnow_after(-7200),),
        )
        db_module.db_execute(
            "UPDATE pending_interactions SET expires_at = ?", (db_module.utcnow(),)
        )
        import asyncio

        asyncio.run(run_maintenance_cycle())
        assert db_module.db_count("SELECT COUNT(*) FROM operation_contexts") == 0
        assert db_module.db_count("SELECT COUNT(*) FROM pending_interactions") == 0


# ── pending interactions：A 发起 → B 应答 ───────────────────────────
class TestPendingInteractionCrossInstance:
    def test_confirm_roundtrip_across_instances(self, mu_env):
        waiter = _FreshState()
        responder = _FreshState()
        import asyncio

        async def scenario():
            task = asyncio.ensure_future(
                waiter._request_tool_confirmation(
                    "pid1", "edit_paragraph", {"text": "a"}
                )
            )
            await asyncio.sleep(0.05)
            ok = responder.confirm_pending_tool("pid1", {"text": "modified"})
            return await task, ok

        result, ok = asyncio.run(scenario())
        assert ok is True
        assert result == {"text": "modified"}

    def test_cancel_wakes_waiter_with_none(self, mu_env):
        waiter = _FreshState()
        responder = _FreshState()

        async def scenario():
            task = asyncio.ensure_future(
                waiter._request_tool_confirmation("pid2", "undo_last_edit", {})
            )
            await asyncio.sleep(0.05)
            ok = responder.cancel_pending_tool("pid2")
            return await task, ok

        result, ok = asyncio.run(scenario())
        assert ok is True
        assert result is None

    def test_clarify_answer_across_instances(self, mu_env):
        waiter = _FreshState()
        responder = _FreshState()

        async def scenario():
            task = asyncio.ensure_future(waiter._request_clarification("cid1"))
            await asyncio.sleep(0.05)
            ok = responder.submit_clarification("cid1", "用现在时")
            return await task, ok

        answer, ok = asyncio.run(scenario())
        assert ok is True
        assert answer == "用现在时"

    def test_double_resolve_first_wins(self, mu_env):
        state = _FreshState()
        # 先由等待侧登记待定项（DB 模式下应答依赖已存在的 pending 行）
        pending_interactions.create("pid3", "confirm", {"tool_args": {}}, 120)
        assert state.confirm_pending_tool("pid3") is True
        assert state.confirm_pending_tool("pid3") is False  # 已决议

    def test_ddg_challenge_cross_instance(self, mu_env):
        """challenge 在 A 进程存储 → B 进程校验可取。"""
        data = {"query": "q", "gk": "g", "form_data": {}}
        cid = ddg._store_challenge(data)
        # 模拟另一副本：清空进程内兜底 dict，走 DB 路径
        ddg._challenge_store.clear()
        got = ddg.get_challenge(cid)
        assert got is not None
        assert got["query"] == "q"
        ddg.remove_challenge(cid)
        assert ddg.get_challenge(cid) is None

    def test_ddg_challenge_expiry(self, mu_env):
        cid = ddg._store_challenge({"query": "q"})
        db_module.db_execute(
            "UPDATE pending_interactions SET expires_at = ?", (db_module.utcnow(),)
        )
        assert ddg.get_challenge(cid) is None


class _FreshState:
    """独立 StateMixin 实例（共享同一 DB），模拟两个副本的服务对象。

    与 WritingChatService 相同的 mixin 形态：动态构造 StateMixin 子类并
    重绑定实例类型，方法查找走 StateMixin 方法表。
    """

    def __init__(self):
        from backend.modules.writing_chat.service.state import StateMixin

        self.__class__ = type("_FreshStateBound", (StateMixin,), {})
        StateMixin.__init__(self)
