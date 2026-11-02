"""沙箱会话 TTL 清理模块测试（plot_gen / template_gen / style_modify）。

三个 cleanup.py 含按 mtime 批量 rmtree 用户会话目录的破坏性逻辑，
此前零覆盖：retention 换算或 mtime 比较回归（如 cutoff 落在未来）会
演变为一次全量误删，现套件无法拦截。覆盖：
    - retention 边界（早于 cutoff 删除、未过期保留）
    - cleanup_enabled=False 早退
    - 锁文件随会话目录一并清理
    - style_modify 的 running/applying 会话保护与 operations 子目录清理
    - API 测试触达 main.py lifespan（清理循环启动/取消全链路）

注意：过期判定取目录树内所有文件的最大 mtime（含 meta.json），
构造过期会话时目录与其中的文件必须一并回拨。
"""

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import get_config
from backend.modules.plot_gen.cleanup import (
    cleanup_expired_sandboxes as cleanup_plot_sandboxes,
)
from backend.modules.style_modify.cleanup import (
    cleanup_expired_sessions as cleanup_style_modify_sessions,
)
from backend.modules.template_gen.cleanup import (
    cleanup_expired_sandboxes as cleanup_template_sandboxes,
)
from backend.utils.session_mutex import lock_file_for

DAY = 86400


def _backdate_tree(path: Path, mtime: float) -> None:
    """把路径与其下全部条目的 mtime 回拨到指定时刻。"""
    os.utime(path, (mtime, mtime))
    for child in path.rglob("*"):
        os.utime(child, (mtime, mtime))


def _make_session(root: Path, name: str, mtime: float) -> Path:
    """创建整树 mtime 已回拨的会话目录。"""
    child = root / name
    child.mkdir(parents=True)
    (child / "artifact.tex").write_text("x", encoding="utf-8")
    _backdate_tree(child, mtime)
    return child


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """单用户模式：把三个模块的会话根目录都指到隔离临时目录。"""
    root = tmp_path / "sessions"
    root.mkdir()
    monkeypatch.setattr(get_config().features.plot_gen, "output_dir", str(root))
    monkeypatch.setattr(get_config().features.template_gen, "output_dir", str(root))
    monkeypatch.setattr(
        get_config().features.style_modify.agent, "sessions_dir", str(root)
    )
    return root


class TestPlotSandboxCleanup:
    def test_disabled_early_return(self, tmp_root, monkeypatch):
        monkeypatch.setattr(get_config().features.plot_gen, "cleanup_enabled", False)
        expired = _make_session(tmp_root, "old", time.time() - 30 * DAY)
        assert cleanup_plot_sandboxes() == 0
        assert expired.is_dir()

    def test_retention_boundary(self, tmp_root, monkeypatch):
        """未过保留期的会话保留，早于 cutoff 的删除。"""
        monkeypatch.setattr(get_config().features.plot_gen, "cleanup_retention_days", 7)
        now = time.time()
        # 留 1 小时余量吸收测试执行耗时：cutoff + 1h 保守未过期
        fresh = _make_session(tmp_root, "fresh", now - 7 * DAY + 3600)
        expired = _make_session(tmp_root, "expired", now - 8 * DAY)
        removed = cleanup_plot_sandboxes()
        assert removed == 1
        assert fresh.is_dir()
        assert not expired.exists()

    def test_lock_file_removed_with_session(self, tmp_root, monkeypatch):
        monkeypatch.setattr(get_config().features.plot_gen, "cleanup_retention_days", 7)
        expired = _make_session(tmp_root, "sess", time.time() - 8 * DAY)
        lock = lock_file_for(expired)
        lock.write_text("", encoding="utf-8")
        assert cleanup_plot_sandboxes() == 1
        assert not expired.exists()
        assert not lock.exists()


class TestTemplateSandboxCleanup:
    def test_disabled_early_return(self, tmp_root, monkeypatch):
        monkeypatch.setattr(
            get_config().features.template_gen, "cleanup_enabled", False
        )
        expired = _make_session(tmp_root, "old", time.time() - 30 * DAY)
        assert cleanup_template_sandboxes() == 0
        assert expired.is_dir()

    def test_expired_removed_fresh_kept(self, tmp_root, monkeypatch):
        monkeypatch.setattr(
            get_config().features.template_gen, "cleanup_retention_days", 7
        )
        now = time.time()
        fresh = _make_session(tmp_root, "fresh", now - DAY)
        expired = _make_session(tmp_root, "expired", now - 8 * DAY)
        assert cleanup_template_sandboxes() == 1
        assert fresh.is_dir()
        assert not expired.exists()


class TestStyleModifySessionCleanup:
    def test_running_session_protected_even_if_expired(self, tmp_root, monkeypatch):
        """meta.json status=running 的会话即使过期也不得删除。"""
        monkeypatch.setattr(
            get_config().features.style_modify.agent, "cleanup_retention_days", 7
        )
        old_ts = time.time() - 30 * DAY
        running = _make_session(tmp_root, "running", old_ts)
        (running / "meta.json").write_text(
            json.dumps({"status": "running"}), encoding="utf-8"
        )
        _backdate_tree(running, old_ts)
        expired = _make_session(tmp_root, "done", old_ts)
        (expired / "meta.json").write_text(
            json.dumps({"status": "finished"}), encoding="utf-8"
        )
        _backdate_tree(expired, old_ts)
        assert cleanup_style_modify_sessions() == 1
        assert running.is_dir()
        assert not expired.exists()

    def test_operations_dir_cleaned(self, tmp_root, monkeypatch):
        """sessions_dir 顶层 operations/ 目录按同样 TTL 清理。"""
        monkeypatch.setattr(
            get_config().features.style_modify.agent, "cleanup_retention_days", 7
        )
        ops = tmp_root / "operations"
        old_op = ops / "op-1"
        old_op.mkdir(parents=True)
        (old_op / "patch.json").write_text("{}", encoding="utf-8")
        _backdate_tree(ops, time.time() - 30 * DAY)
        assert cleanup_style_modify_sessions() == 1
        assert not old_op.exists()
        assert ops.is_dir()


class TestAppLifespan:
    def test_lifespan_boot_and_shutdown(self, tmp_root, monkeypatch):
        """API 测试此前从不进入 lifespan：本用例完整走一遍启动/关闭，
        覆盖清理循环创建与取消、HTTP 客户端初始化与释放的全链路。"""
        from main import app

        with TestClient(app) as client:
            resp = client.get("/api/config/status")
            assert resp.status_code in (200, 401, 404)
