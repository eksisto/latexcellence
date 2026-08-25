"""
多用户反向补丁编辑日志测试（v2 P3，方案 §8）。

覆盖内容（对应方案 §8.6）：
    - 写入记账：apply_change / apply_whole_files 落 op + 快照布局
      （owner 维度 .edit_logs/，不进项目目录）、单用户/无当前用户不记账、
      无变化不记账
    - 撤销/重做语义：LIFO 逆序回退、redo 对称、两用户协作冲突 409、
      配额超限 413 quota_exceeded_recover（op 保持 active）、文件缺失标记 failed
    - op_group：一次 apply_whole_files 事务整体回滚
    - 过大不可撤（non_undoable）：只记索引不写快照，撤销被拒
    - 生命周期：条数上限按组淘汰（连带快照删除）、TTL 整栈清理、
      项目级级联清理
    - 项目转交：ops 按 actor 归属不变，新 owner 无栈可撤
"""

import json
import logging
import uuid
from unittest import mock

import pytest
from fastapi import HTTPException

from backend.core.config import get_config
from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import db as db_module
from backend.core.multiuser import edit_log, userspace
from backend.core.project_manager import LatexProject

PASSWORD = "password-123"

MAIN_TEX = (
    r"\documentclass{article}"
    + "\n"
    + r"\begin{document}"
    + "\n"
    + "First paragraph."
    + "\n"
    + "\n"
    + "Second paragraph."
    + "\n"
    + r"\end{document}"
    + "\n"
)


def _patched_edit_log_config(**mu_updates):
    """构造替换 edit_log 配额字段的配置对象上下文管理器。"""
    real = get_config()
    mu = real.multi_user.model_copy(update=mu_updates)
    cfg = real.model_copy(update={"multi_user": mu})
    return mock.patch.object(edit_log, "get_config", return_value=cfg)


@pytest.fixture
def stack_env(mu_env):
    """多用户环境 + owner 用户与一个含 main.tex 的活动项目。"""
    owner = auth_core.create_user("owner", PASSWORD, role="user", status="active")
    editor = auth_core.create_user("editor", PASSWORD, role="user", status="active")
    project_id = uuid.uuid4().hex
    root = userspace.project_dir(owner["id"], project_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.tex").write_text(MAIN_TEX, encoding="utf-8")
    now = db_module.utcnow()
    db_module.db_execute(
        "INSERT INTO projects (id, owner_id, name, size_bytes, status, "
        "deleted_at, created_at, updated_at) "
        "VALUES (?, ?, 'P', 0, 'active', NULL, ?, ?)",
        (project_id, owner["id"], now, now),
    )
    # editor 以协作者身份共享项目（v2 P3：共享项目可撤销自己的修改）
    db_module.db_execute(
        "INSERT INTO project_collaborators (project_id, user_id, role, created_at) "
        "VALUES (?, ?, 'editor', ?)",
        (project_id, editor["id"], now),
    )
    env = dict(mu_env)
    env.update(
        {
            "owner": owner,
            "editor": editor,
            "project_id": project_id,
            "root": root,
        }
    )
    yield env


def _open_project(env, user_id: str) -> LatexProject:
    """以指定用户身份打开项目（模拟请求上下文中的当前用户）。"""
    userspace.set_current_user_id(user_id)
    project = LatexProject()
    project.open(str(env["root"]))
    return project


def _op_rows(env, actor_id: str | None = None) -> list[dict]:
    if actor_id is None:
        return db_module.db_query_all("SELECT * FROM workspace_edit_ops ORDER BY seq")
    return db_module.db_query_all(
        "SELECT * FROM workspace_edit_ops WHERE actor_user_id = ? ORDER BY seq",
        (actor_id,),
    )


# ── 写入记账 ──────────────────────────────────────────────────────────────
class TestRecording:
    def test_apply_change_records_op_and_snapshot(self, stack_env):
        project = _open_project(stack_env, stack_env["owner"]["id"])
        project.apply_change("main.tex", 0, "First EDITED.")

        rows = _op_rows(stack_env)
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "active"
        assert row["non_undoable"] == 0
        assert row["file_count"] == 1
        assert row["actor_user_id"] == stack_env["owner"]["id"]
        assert row["owner_id"] == stack_env["owner"]["id"]

        # 快照目录：owner 维度 .edit_logs/<project>/<op>/，不进项目目录
        snap_dir = edit_log.op_snapshot_dir(
            stack_env["owner"]["id"], stack_env["project_id"], row["op_id"]
        )
        assert snap_dir.exists()
        assert not (stack_env["root"] / ".edit_logs").exists()
        names = {p.name for p in snap_dir.iterdir()}
        assert names == {"manifest.json", "0.before", "0.after"}
        manifest = json.loads((snap_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["files"][0]["path"] == "main.tex"
        # before 快照 == 原始全文；after 快照 == 修改后全文
        assert (snap_dir / "0.before").read_text(encoding="utf-8") == MAIN_TEX
        assert "First EDITED." in (snap_dir / "0.after").read_text(encoding="utf-8")

    def test_apply_whole_files_shares_op_group(self, stack_env):
        project = _open_project(stack_env, stack_env["owner"]["id"])
        (stack_env["root"] / "a.tex").write_text("A\n", encoding="utf-8")
        (stack_env["root"] / "b.tex").write_text("B\n", encoding="utf-8")
        with edit_log.op_group_scope():
            project.apply_whole_files({"a.tex": "A2\n", "b.tex": "B2\n"})
        rows = _op_rows(stack_env)
        assert len(rows) == 1
        assert rows[0]["file_count"] == 2

    def test_apply_whole_files_return_edit_op_id(self, stack_env):
        """return_edit_op_id=True：返回 (md5 字典, 本次记账 op_id)。

        供编辑器整文件保存等调用方在配额超限回滚后按 id 撤销对应记账，
        保证撤销栈不被失败写入污染（方案 §8.4）。"""
        project = _open_project(stack_env, stack_env["owner"]["id"])
        result, op_id = project.apply_whole_files(
            {"main.tex": "V1\n"}, return_edit_op_id=True
        )
        assert set(result) == {"main.tex"}
        rows = _op_rows(stack_env)
        assert len(rows) == 1
        assert rows[0]["op_id"] == op_id
        # 该 id 可用于 discard_op 闭环（配额回滚路径依赖）
        assert (
            edit_log.discard_op(
                op_id, stack_env["project_id"], stack_env["owner"]["id"]
            )
            is True
        )
        assert _op_rows(stack_env) == []

    def test_apply_whole_files_default_return_contract_unchanged(self, stack_env):
        """默认（不传 return_edit_op_id）返回值契约保持不变。"""
        project = _open_project(stack_env, stack_env["owner"]["id"])
        result = project.apply_whole_files({"main.tex": "V2\n"})
        assert isinstance(result, dict)
        assert set(result) == {"main.tex"}

    def test_apply_whole_files_unchanged_content_returns_none_op(self, stack_env):
        """无变化写入不产生 op：return_edit_op_id 返回 None。"""
        project = _open_project(stack_env, stack_env["owner"]["id"])
        content = (stack_env["root"] / "main.tex").read_text(encoding="utf-8")
        _, op_id = project.apply_whole_files(
            {"main.tex": content}, return_edit_op_id=True
        )
        assert op_id is None
        assert _op_rows(stack_env) == []

    def test_single_user_mode_records_nothing(self, tmp_path):
        from tests.test_multiuser.conftest import build_mu_env

        env = build_mu_env(tmp_path, enabled=False)
        try:
            userspace.set_current_user_id(None)
            root = tmp_path / "solo-project"
            root.mkdir()
            (root / "main.tex").write_text(MAIN_TEX, encoding="utf-8")
            project = LatexProject()
            project.open(str(root))
            # 单用户模式：DB 未初始化，apply_change 正常完成即证明记账路径
            # 未触碰任何多用户设施
            project.apply_change("main.tex", 0, "Edited.")
            # 不产生 .edit_logs 快照目录，内存撤销栈行为保持现状
            assert not (tmp_path / "users").exists() or not any(
                (tmp_path / "users").rglob(".edit_logs")
            )
            assert project.can_undo("main.tex") is True
        finally:
            userspace.set_current_user_id(None)

    def test_no_current_user_records_nothing(self, stack_env):
        userspace.set_current_user_id(None)
        project = LatexProject()
        project.open(str(stack_env["root"]))
        project.apply_change("main.tex", 0, "Edited.")
        assert _op_rows(stack_env) == []

    def test_unchanged_content_records_no_op(self, stack_env):
        project = _open_project(stack_env, stack_env["owner"]["id"])
        content = (stack_env["root"] / "main.tex").read_text(encoding="utf-8")
        project.apply_whole_files({"main.tex": content})
        assert _op_rows(stack_env) == []

    def test_source_module_detected_from_caller(self, stack_env):
        """source_module 尽力推断自业务调用方模块（诊断字段）。"""
        import sys
        import types

        project = _open_project(stack_env, stack_env["owner"]["id"])
        mod_name = "backend.modules.writing_chat.service.edits"
        mod = types.ModuleType(mod_name)
        mod.__dict__["__name__"] = mod_name

        def fake_apply_edit(p):
            p.apply_change("main.tex", 0, "Edited.")

        # 以该模块命名空间重建函数：调用帧 __name__ 指向目标模块
        fn = types.FunctionType(
            fake_apply_edit.__code__, dict(mod.__dict__), "fake_apply_edit"
        )
        sys.modules[mod_name] = mod
        try:
            fn(project)
        finally:
            sys.modules.pop(mod_name, None)
        rows = _op_rows(stack_env)
        assert len(rows) == 1
        assert rows[0]["source_module"] == "writing_chat"

    def test_explicit_source_scope_overrides_detection(self, stack_env):
        project = _open_project(stack_env, stack_env["owner"]["id"])
        with edit_log.source_scope("custom_origin"):
            project.apply_change("main.tex", 0, "Edited.")
        rows = _op_rows(stack_env)
        assert rows[0]["source_module"] == "custom_origin"

    def test_evict_overflow_failure_does_not_break_apply_change(
        self, stack_env, caplog
    ):
        """回归（缺陷二）：_evict_overflow 是机会主义维护，抛异常时必须
        降级为 warning 日志——绝不能从 record_write / apply_change 逃逸，
        把已成功落盘的写入变成客户端眼中的失败。"""
        project = _open_project(stack_env, stack_env["owner"]["id"])
        with mock.patch.object(
            edit_log,
            "_evict_overflow",
            side_effect=RuntimeError("database is locked"),
        ):
            with caplog.at_level(logging.WARNING, logger="multiuser.edit_log"):
                op_id = project.apply_change("main.tex", 0, "Survives eviction outage.")
        # 写入与记账均不受驱逐故障影响
        assert op_id is not None
        rows = _op_rows(stack_env)
        assert len(rows) == 1
        assert rows[0]["status"] == "active"
        content = (stack_env["root"] / "main.tex").read_text(encoding="utf-8")
        assert "Survives eviction outage." in content
        # 驱逐失败留有 warning 级痕迹
        assert any(
            "eviction failed" in record.getMessage().lower()
            for record in caplog.records
        )

    def test_evict_overflow_failure_does_not_break_apply_whole_files(self, stack_env):
        """同缺陷二的 apply_whole_files 路径：批量事务写回后驱逐异常不逃逸。"""
        project = _open_project(stack_env, stack_env["owner"]["id"])
        (stack_env["root"] / "batch.tex").write_text("B0\n", encoding="utf-8")
        with mock.patch.object(
            edit_log,
            "_evict_overflow",
            side_effect=RuntimeError("database is locked"),
        ):
            project.apply_whole_files({"batch.tex": "B1\n"})
        assert len(_op_rows(stack_env)) == 1
        assert (stack_env["root"] / "batch.tex").read_text(encoding="utf-8") == "B1\n"

    def test_non_undoable_when_snapshot_too_large(self, stack_env):
        project = _open_project(stack_env, stack_env["owner"]["id"])
        big_before = "y" * (700 * 1024)
        big_after = "x" * (700 * 1024)
        big_path = stack_env["root"] / "big.txt"
        big_path.write_text(big_before, encoding="utf-8")
        with _patched_edit_log_config(edit_log_max_op_mb=1):
            # 前后全文合计约 1.4MB，超过 1MB 上限
            project.apply_whole_files({"big.txt": big_after})
        rows = _op_rows(stack_env)
        assert len(rows) == 1
        assert rows[0]["non_undoable"] == 1
        snap_dir = edit_log.op_snapshot_dir(
            stack_env["owner"]["id"], stack_env["project_id"], rows[0]["op_id"]
        )
        # 只留清单，不写全文快照
        assert (snap_dir / "manifest.json").exists()
        assert not (snap_dir / "0.before").exists()

        status = edit_log.compute_status(
            stack_env["project_id"], stack_env["owner"]["id"], str(stack_env["root"])
        )
        assert status["can_undo"] is False
        assert status["undo_block_reason"] == "too_large"

        with pytest.raises(HTTPException) as ei:
            edit_log.perform_undo(
                project,
                project_id=stack_env["project_id"],
                actor_user_id=stack_env["owner"]["id"],
                root_path=str(stack_env["root"]),
            )
        assert ei.value.status_code == 409
        assert ei.value.detail["error"] == "op_not_undoable"


# ── 撤销 / 重做语义 ───────────────────────────────────────────────────────
class TestUndoRedoSemantics:
    def test_lifo_multiple_edits_reversed_then_redo(self, stack_env):
        project = _open_project(stack_env, stack_env["owner"]["id"])
        for i in range(3):
            project.apply_change("main.tex", 0, f"Version {i}.")
        path = stack_env["root"] / "main.tex"

        for i in range(2, -1, -1):
            result = edit_log.perform_undo(
                project,
                project_id=stack_env["project_id"],
                actor_user_id=stack_env["owner"]["id"],
                root_path=str(stack_env["root"]),
            )
            assert result["undone"] is True
            expected_prev = MAIN_TEX if i == 0 else None
            content = path.read_text(encoding="utf-8")
            if i > 0:
                assert f"Version {i - 1}." in content
            else:
                assert expected_prev is not None and content == MAIN_TEX

        # redo 对称重放
        for i in range(3):
            result = edit_log.perform_redo(
                project,
                project_id=stack_env["project_id"],
                actor_user_id=stack_env["owner"]["id"],
                root_path=str(stack_env["root"]),
            )
            assert result["redone"] is True
            assert f"Version {i}." in path.read_text(encoding="utf-8")

        status = edit_log.compute_status(
            stack_env["project_id"], stack_env["owner"]["id"], str(stack_env["root"])
        )
        assert status == {
            "can_undo": True,
            "can_redo": False,
            "undo_block_reason": None,
            "redo_block_reason": None,
            "failed_ops": 0,
        }

    def test_two_users_conflict_then_independent_stacks(self, stack_env):
        """A 改 → B 改 → A undo 被 409 拒绝且 B 未受影响 → B undo 成功。"""
        owner_id = stack_env["owner"]["id"]
        editor_id = stack_env["editor"]["id"]

        project_owner = _open_project(stack_env, owner_id)
        project_owner.apply_change("main.tex", 0, "Owner edit.")
        project_editor = _open_project(stack_env, editor_id)
        project_editor.apply_change("main.tex", 1, "Editor edit.")

        # A 的最新状态已被 B 覆盖：冲突拒绝，绝不静默回退他人修改
        with pytest.raises(HTTPException) as ei:
            edit_log.perform_undo(
                project_owner,
                project_id=stack_env["project_id"],
                actor_user_id=owner_id,
                root_path=str(stack_env["root"]),
            )
        assert ei.value.status_code == 409
        assert ei.value.detail["error"] == "file_conflict"
        content = (stack_env["root"] / "main.tex").read_text(encoding="utf-8")
        assert "Owner edit." in content and "Editor edit." in content
        # A 的 op 保持 active（可待文件回到对应状态后重试）
        assert all(r["status"] == "active" for r in _op_rows(stack_env))

        # B 撤销自己的修改：只回退 B 的段落，A 的修改保留
        result = edit_log.perform_undo(
            project_editor,
            project_id=stack_env["project_id"],
            actor_user_id=editor_id,
            root_path=str(stack_env["root"]),
        )
        assert result["undone"] is True
        content = (stack_env["root"] / "main.tex").read_text(encoding="utf-8")
        assert "Owner edit." in content
        assert "Editor edit." not in content
        assert "Second paragraph." in content

        # 他人 op 不出现在自己的栈中：B 已无可撤；A 的 op 在文件回到其
        # after 状态后恢复可撤（方案 §8.3：MD5 校验失败保留 op 待重试）
        status_b = edit_log.compute_status(
            stack_env["project_id"], editor_id, str(stack_env["root"])
        )
        assert status_b["can_undo"] is False
        status_a = edit_log.compute_status(
            stack_env["project_id"], owner_id, str(stack_env["root"])
        )
        assert status_a["can_undo"] is True
        # A 现在撤销自己的修改：恢复原始内容，B 的段落不受影响
        result = edit_log.perform_undo(
            project_owner,
            project_id=stack_env["project_id"],
            actor_user_id=owner_id,
            root_path=str(stack_env["root"]),
        )
        assert result["undone"] is True
        assert (stack_env["root"] / "main.tex").read_text(encoding="utf-8") == MAIN_TEX

    def test_redo_blocked_by_newer_active_op(self, stack_env):
        project = _open_project(stack_env, stack_env["owner"]["id"])
        project.apply_change("main.tex", 0, "Edit one.")
        project.apply_change("main.tex", 0, "Edit two.")
        # 撤销"Edit two"后再产生新修改 → 重做视为已被取代
        edit_log.perform_undo(
            project,
            project_id=stack_env["project_id"],
            actor_user_id=stack_env["owner"]["id"],
            root_path=str(stack_env["root"]),
        )
        project.apply_change("main.tex", 1, "Fresh third edit.")
        result = edit_log.perform_redo(
            project,
            project_id=stack_env["project_id"],
            actor_user_id=stack_env["owner"]["id"],
            root_path=str(stack_env["root"]),
        )
        assert result["redone"] is False

    def test_redo_not_blocked_by_other_project_activity(self, stack_env):
        """seq 为全库自增：其他项目的活跃 op 不得阻断本项目的重做。"""
        owner_id = stack_env["owner"]["id"]
        other_pid = uuid.uuid4().hex
        now = db_module.utcnow()
        db_module.db_execute(
            "INSERT INTO projects (id, owner_id, name, size_bytes, status, "
            "deleted_at, created_at, updated_at) "
            "VALUES (?, ?, 'Other', 0, 'active', NULL, ?, ?)",
            (other_pid, owner_id, now, now),
        )
        project = _open_project(stack_env, owner_id)
        project.apply_change("main.tex", 0, "Edit one.")
        edit_log.perform_undo(
            project,
            project_id=stack_env["project_id"],
            actor_user_id=owner_id,
            root_path=str(stack_env["root"]),
        )
        # 另一项目产生更新的 op
        other_root = userspace.project_dir(owner_id, other_pid)
        other_root.mkdir(parents=True, exist_ok=True)
        (other_root / "note.tex").write_text("N\n", encoding="utf-8")
        other_project = LatexProject()
        other_project.open(str(other_root))
        other_project.apply_change("note.tex", 0, "N2.")
        rows = db_module.db_query_all(
            "SELECT project_id, status FROM workspace_edit_ops ORDER BY seq"
        )
        assert rows[-1]["project_id"] == other_pid

        status = edit_log.compute_status(
            stack_env["project_id"], owner_id, str(stack_env["root"])
        )
        assert status["can_redo"] is True
        assert status["redo_block_reason"] is None
        result = edit_log.perform_redo(
            project,
            project_id=stack_env["project_id"],
            actor_user_id=owner_id,
            root_path=str(stack_env["root"]),
        )
        assert result["redone"] is True

    def test_redo_chain_survives_unrelated_replayed_op(self, stack_env):
        """回归（缺陷一）：多次撤销 → 异文件新编辑 → 撤销该新编辑 →
        连续 redo 必须放行整条重放链（c → a → b）。

        修复前 _newest_active_blocks_redo 以"存在 seq 更大的 active op"
        判定取代：c 重放回活后（seq3 > seq1/seq2），继续重做 a/b 被误判为
        superseded，合法重放链在第二步永久断裂。收窄后仅同组或共享文件
        路径的更新 active op 才阻断 redo，异文件编辑不终止本组重放链。"""
        owner_id = stack_env["owner"]["id"]
        root = stack_env["root"]
        (root / "extra.tex").write_text("Extra v0\n", encoding="utf-8")
        project = _open_project(stack_env, owner_id)

        def undo_once() -> dict:
            return edit_log.perform_undo(
                project,
                project_id=stack_env["project_id"],
                actor_user_id=owner_id,
                root_path=str(root),
            )

        def redo_once() -> dict:
            return edit_log.perform_redo(
                project,
                project_id=stack_env["project_id"],
                actor_user_id=owner_id,
                root_path=str(root),
            )

        # a(seq1)、b(seq2) 同文件两次编辑 → 全部撤销
        project.apply_change("main.tex", 0, "Alpha paragraph.")
        project.apply_change("main.tex", 1, "Beta paragraph.")
        assert undo_once()["undone"] is True
        assert undo_once()["undone"] is True
        # 异文件新编辑 c(seq3) → 撤销之
        project.apply_whole_files({"extra.tex": "Extra v1\n"})
        assert undo_once()["undone"] is True
        assert (root / "extra.tex").read_text(encoding="utf-8") == "Extra v0\n"

        # 固定 undone_at 时序（b < a < c）确定重放顺序为 c → a → b：
        # utcnow 为秒级精度，连续撤销可能同秒而落入 seq 破平分支
        rows = _op_rows(stack_env, owner_id)
        assert [r["file_count"] for r in rows] == [1, 1, 1]
        stamps = (
            db_module.utcnow_after(-60),  # a：居中
            db_module.utcnow_after(-120),  # b：最早撤销
            db_module.utcnow_after(-30),  # c：最后撤销
        )
        for row, stamp in zip(rows, stamps):
            db_module.db_execute(
                "UPDATE workspace_edit_ops SET undone_at = ? WHERE seq = ?",
                (stamp, row["seq"]),
            )

        # 第一步：重放最后撤销的异文件编辑 c
        first = redo_once()
        assert first == {"redone": True, "files": ["extra.tex"]}
        # 关键回归点：刚重放的 c（seq 更大且 active）不得把后续 a/b 的重做
        # 误判为 superseded——状态接口与执行路径都必须继续放行整链
        status = edit_log.compute_status(stack_env["project_id"], owner_id, str(root))
        assert status["can_redo"] is True
        assert status["redo_block_reason"] is None
        second = redo_once()
        third = redo_once()
        assert second["redone"] is True
        assert second["files"] == ["main.tex"]
        assert third["redone"] is True
        assert third["files"] == ["main.tex"]

        # 终态对称：整组全部回活、内容完整重放
        assert all(r["status"] == "active" for r in _op_rows(stack_env, owner_id))
        assert (root / "extra.tex").read_text(encoding="utf-8") == "Extra v1\n"
        content = (root / "main.tex").read_text(encoding="utf-8")
        assert "Alpha paragraph." in content
        assert "Beta paragraph." in content

    def test_quota_exceeded_recover_keeps_op_active(self, stack_env):
        owner_id = stack_env["owner"]["id"]
        # 原始文件很大，随后把段落改小：撤销该修改会让文件重新变大
        big_paragraph = "y" * 60000
        big_content = big_paragraph + "\n\n" + MAIN_TEX
        (stack_env["root"] / "main.tex").write_text(big_content, encoding="utf-8")
        project = _open_project(stack_env, owner_id)
        project.apply_change("main.tex", 0, "tiny.")

        db_module.db_execute(
            "UPDATE users SET project_quota_bytes = ? WHERE id = ?",
            (1024, owner_id),  # 远小于恢复后的体积增量
        )
        with pytest.raises(HTTPException) as ei:
            edit_log.perform_undo(
                project,
                project_id=stack_env["project_id"],
                actor_user_id=owner_id,
                root_path=str(stack_env["root"]),
            )
        assert ei.value.status_code == 413
        assert ei.value.detail["error"] == "quota_exceeded_recover"
        # 文件未被改动、op 保持 active 可重试
        assert (
            (stack_env["root"] / "main.tex")
            .read_text(encoding="utf-8")
            .startswith("tiny.")
        )
        rows = _op_rows(stack_env, owner_id)
        assert [r["status"] for r in rows] == ["active"]

        # 解除配额限制后可成功撤销
        db_module.db_execute(
            "UPDATE users SET project_quota_bytes = NULL WHERE id = ?", (owner_id,)
        )
        result = edit_log.perform_undo(
            project,
            project_id=stack_env["project_id"],
            actor_user_id=owner_id,
            root_path=str(stack_env["root"]),
        )
        assert result["undone"] is True
        assert (stack_env["root"] / "main.tex").read_text(
            encoding="utf-8"
        ) == big_content

    def test_missing_file_marks_op_failed(self, stack_env):
        project = _open_project(stack_env, stack_env["owner"]["id"])
        project.apply_change("main.tex", 0, "Edited.")
        (stack_env["root"] / "main.tex").unlink()  # 防御性场景：文件消失

        with pytest.raises(HTTPException) as ei:
            edit_log.perform_undo(
                project,
                project_id=stack_env["project_id"],
                actor_user_id=stack_env["owner"]["id"],
                root_path=str(stack_env["root"]),
            )
        assert ei.value.status_code == 409
        rows = _op_rows(stack_env)
        assert rows[0]["status"] == "failed"
        # 状态接口提示 failed op
        status = edit_log.compute_status(
            stack_env["project_id"], stack_env["owner"]["id"], str(stack_env["root"])
        )
        assert status["failed_ops"] == 1

    def test_group_undo_rolls_back_whole_transaction(self, stack_env):
        project = _open_project(stack_env, stack_env["owner"]["id"])
        (stack_env["root"] / "a.tex").write_text("A\n", encoding="utf-8")
        (stack_env["root"] / "b.tex").write_text("B\n", encoding="utf-8")
        group_id: str | None = None
        with edit_log.op_group_scope() as gid:
            group_id = gid
            project.apply_whole_files({"a.tex": "A2\n"})
            project.apply_whole_files({"b.tex": "B2\n"})
        rows = _op_rows(stack_env)
        assert len(rows) == 2
        assert {r["op_group_id"] for r in rows} == {group_id}

        result = edit_log.perform_undo(
            project,
            project_id=stack_env["project_id"],
            actor_user_id=stack_env["owner"]["id"],
            root_path=str(stack_env["root"]),
        )
        assert result["undone"] is True
        assert sorted(result["files"]) == ["a.tex", "b.tex"]
        assert (stack_env["root"] / "a.tex").read_text(encoding="utf-8") == "A\n"
        assert (stack_env["root"] / "b.tex").read_text(encoding="utf-8") == "B\n"
        assert all(r["status"] == "undone" for r in _op_rows(stack_env))

    def test_group_with_repeated_file_undoes_to_first_before(self, stack_env):
        """回归（M1）：同一 op_group 内对同一文件链式写多次（v0→v1→v2），
        整组撤销应回到进组前状态（最小 seq 的 before），而非中间态；
        重做对称地一次回到最终态。"""
        project = _open_project(stack_env, stack_env["owner"]["id"])
        path = stack_env["root"] / "main.tex"
        with edit_log.op_group_scope():
            project.apply_whole_files({"main.tex": "V1\n"})
            project.apply_whole_files({"main.tex": "V2\n"})
        rows = _op_rows(stack_env)
        assert len(rows) == 2

        result = edit_log.perform_undo(
            project,
            project_id=stack_env["project_id"],
            actor_user_id=stack_env["owner"]["id"],
            root_path=str(stack_env["root"]),
        )
        assert result["undone"] is True
        assert path.read_text(encoding="utf-8") == MAIN_TEX
        assert all(r["status"] == "undone" for r in _op_rows(stack_env))

        # 重做对称：校验当前 == 最小 seq 的 before，重放最大 seq 的 after
        result = edit_log.perform_redo(
            project,
            project_id=stack_env["project_id"],
            actor_user_id=stack_env["owner"]["id"],
            root_path=str(stack_env["root"]),
        )
        assert result["redone"] is True
        assert path.read_text(encoding="utf-8") == "V2\n"
        assert all(r["status"] == "active" for r in _op_rows(stack_env))


# ── 生命周期 ──────────────────────────────────────────────────────────────
class TestLifecycle:
    def test_cap_eviction_removes_oldest_groups_with_snapshots(self, stack_env):
        project = _open_project(stack_env, stack_env["owner"]["id"])
        with _patched_edit_log_config(edit_log_max_ops=3):
            for i in range(4):
                project.apply_change("main.tex", 0, f"V{i}.")
        rows = _op_rows(stack_env)
        assert len(rows) == 3
        # 最旧的 V0 快照目录已删除，其余保留
        remaining_dirs = {
            p.name
            for p in (
                userspace.user_root(stack_env["owner"]["id"])
                / ".edit_logs"
                / stack_env["project_id"]
            ).iterdir()
        }
        assert remaining_dirs == {r["op_id"] for r in rows}
        # 保留栈中最旧的一条（V1）的 before 即 V0 状态——淘汰边界之上的
        # 撤销链内容保持自洽
        oldest_before = self._snapshot_before(stack_env, rows[0])
        assert "V0." in oldest_before
        # 连续撤销 3 次回退到 V0 状态后无可再撤（V0 已被淘汰）
        for i in (2, 1, 0):
            result = edit_log.perform_undo(
                project,
                project_id=stack_env["project_id"],
                actor_user_id=stack_env["owner"]["id"],
                root_path=str(stack_env["root"]),
            )
            assert result["undone"] is True
        status = edit_log.compute_status(
            stack_env["project_id"], stack_env["owner"]["id"], str(stack_env["root"])
        )
        assert status["can_undo"] is False
        content = (stack_env["root"] / "main.tex").read_text(encoding="utf-8")
        assert "V0." in content

    @staticmethod
    def _snapshot_before(env, row) -> str:
        snap = edit_log.op_snapshot_dir(
            env["owner"]["id"], env["project_id"], row["op_id"]
        )
        return (snap / "0.before").read_text(encoding="utf-8")

    def test_ttl_purge_removes_idle_stack(self, stack_env):
        from datetime import datetime, timedelta, timezone

        project = _open_project(stack_env, stack_env["owner"]["id"])
        project.apply_change("main.tex", 0, "Edited.")
        assert len(_op_rows(stack_env)) == 1
        # 把 created_at 拨到 25 小时前（TTL 默认 24h）
        old = (
            datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=25)
        ).isoformat()
        db_module.db_execute("UPDATE workspace_edit_ops SET created_at = ?", (old,))
        removed = edit_log.purge_expired_edit_logs()
        assert removed == 1
        assert _op_rows(stack_env) == []
        proj_logs = (
            userspace.user_root(stack_env["owner"]["id"])
            / ".edit_logs"
            / stack_env["project_id"]
        )
        assert not proj_logs.exists()

    def test_purging_one_actor_keeps_collaborator_snapshots(self, stack_env):
        """回归（C1）：清理单个 actor 的栈只逐 op 删快照并回收空目录，
        绝不整目录 rmtree 波及共享项目其他成员的快照。"""
        owner_id = stack_env["owner"]["id"]
        editor_id = stack_env["editor"]["id"]
        project_editor = _open_project(stack_env, editor_id)
        project_editor.apply_change("main.tex", 1, "Editor edit.")
        project_owner = _open_project(stack_env, owner_id)
        project_owner.apply_change("main.tex", 0, "Owner edit.")

        # 仅清理 editor 的栈（如 TTL 过期）
        assert edit_log.purge_stack(stack_env["project_id"], editor_id) == 1
        assert _op_rows(stack_env, editor_id) == []
        assert len(_op_rows(stack_env, owner_id)) == 1

        # owner 的快照必须完好：仍可正常撤销自己的修改
        # （修复前：整目录被 rmtree，此处 409 snapshot missing 且永久卡死）
        result = edit_log.perform_undo(
            project_owner,
            project_id=stack_env["project_id"],
            actor_user_id=owner_id,
            root_path=str(stack_env["root"]),
        )
        assert result["undone"] is True

    def test_purge_project_ops_cascades(self, stack_env):
        project = _open_project(stack_env, stack_env["owner"]["id"])
        project.apply_change("main.tex", 0, "Edited.")
        assert len(_op_rows(stack_env)) == 1
        assert edit_log.purge_project_ops(stack_env["project_id"]) == 1
        assert _op_rows(stack_env) == []

    def test_transfer_keeps_actor_attribution(self, stack_env):
        """转交后 ops 按 actor 归属不变：原 editor 仍可撤自己的 op，
        新 owner 没有自己的 op。"""
        editor_id = stack_env["editor"]["id"]
        other = auth_core.create_user(
            "receiver", PASSWORD, role="user", status="active"
        )
        project_editor = _open_project(stack_env, editor_id)
        project_editor.apply_change("main.tex", 0, "Editor edit.")

        # 模拟转交：DB 归属变更 + 目录迁移（transfer_project 的核心效果）
        new_root = userspace.project_dir(other["id"], stack_env["project_id"])
        new_root.parent.mkdir(parents=True, exist_ok=True)
        import shutil as _shutil

        _shutil.move(str(stack_env["root"]), str(new_root))
        db_module.db_execute(
            "UPDATE projects SET owner_id = ? WHERE id = ?",
            (other["id"], stack_env["project_id"]),
        )
        stack_env["root"] = new_root

        # 新 owner 无自己的 op 可撤
        project_new_owner = _open_project(stack_env, other["id"])
        result = edit_log.perform_undo(
            project_new_owner,
            project_id=stack_env["project_id"],
            actor_user_id=other["id"],
            root_path=str(new_root),
        )
        assert result["undone"] is False

        # 原 editor 的 op 归属不变；工作区绑定刷新后（重开到新根目录）
        # 仍可撤销自己的 op（对应 transfer_project 的 _refresh_workspace_bindings）
        userspace.set_current_user_id(editor_id)
        project_editor = LatexProject()
        project_editor.open(str(new_root))
        result = edit_log.perform_undo(
            project_editor,
            project_id=stack_env["project_id"],
            actor_user_id=editor_id,
            root_path=str(new_root),
        )
        assert result["undone"] is True
        assert (new_root / "main.tex").read_text(encoding="utf-8") == MAIN_TEX


# ── 配额回滚版本守卫 ────────────────────────────────────────────────────
class TestQuotaRollbackGuard:
    def test_rollback_aborts_when_file_changed_during_quota_check(self, stack_env):
        """回归（M4）：配额检查窗口期他人落盘新修改时，回滚必须放弃覆盖
        （乐观锁 expected_md5s 冲突）、仅撤销本次记账并如实报错 500，
        绝不静默回退他人的内容。"""
        import asyncio

        from backend.modules.projects import service as projects_service

        owner_id = stack_env["owner"]["id"]
        userspace.set_current_user_id(owner_id)
        project = LatexProject()
        project.open(str(stack_env["root"]))
        before = (stack_env["root"] / "main.tex").read_text(encoding="utf-8")
        op_id = project.apply_change("main.tex", 0, "Oversized edit.")
        assert op_id is not None

        # 模拟配额检查期间另一用户写入
        concurrent = before + "\n% another user's concurrent write\n"
        (stack_env["root"] / "main.tex").write_text(concurrent, encoding="utf-8")

        binding = {
            "multi_user": True,
            "project_id": stack_env["project_id"],
            "owner_id": owner_id,
        }
        with mock.patch.object(
            projects_service, "refresh_owner_usage_after_write", return_value=0
        ), mock.patch.object(
            projects_service, "project_usage_for_user", return_value=10**9
        ), mock.patch.object(
            projects_service, "effective_project_quota", return_value=1
        ):
            with pytest.raises(HTTPException) as ei:
                asyncio.run(
                    projects_service.enforce_write_quota(
                        binding,
                        project,
                        "main.tex",
                        before_content=before,
                        edit_op_id=op_id,
                        actor_user_id=owner_id,
                    )
                )
        # 如实上报（超限写入仍在磁盘上，未回滚也未谎称已回滚）
        assert ei.value.status_code == 500
        assert ei.value.detail["error"] == "quota_exceeded"
        # 他人的并发修改原样保留，未被回滚覆盖
        assert (stack_env["root"] / "main.tex").read_text(
            encoding="utf-8"
        ) == concurrent
        # 本次失败写入的 op 已从撤销栈移除
        assert _op_rows(stack_env) == []


# ── API 集成（TestClient + 中间件） ───────────────────────────────────────
class TestUndoRedoApi:
    @pytest.fixture
    def api_env(self, stack_env):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.core.security_middleware import AccessSecurityMiddleware
        from backend.modules.auth.router import admin_router, auth_router
        from backend.modules.project_router import router as project_router
        from tests.test_multiuser.conftest import csrf

        app = FastAPI()
        app.add_middleware(AccessSecurityMiddleware)

        @app.get("/api/health")
        async def health():
            return {"status": "ok"}

        app.include_router(project_router)
        app.include_router(auth_router)
        app.include_router(admin_router)

        env = dict(stack_env)
        env["client_factory"] = lambda: TestClient(app)
        env["csrf"] = csrf

        def login(client, username):
            r = client.post(
                "/api/auth/login",
                json={"username": username, "password": PASSWORD},
            )
            assert r.status_code == 200, r.text

        env["login"] = login
        yield env
        from backend.core.workspace_manager import get_workspace_manager

        wm = get_workspace_manager()
        for ws in list(wm.list_workspaces()):
            wm.remove(ws["workspace_id"])

    def _open(self, client, env, user_key, ws_id):
        r = client.post(
            "/api/project/open",
            params={"workspace_id": ws_id},
            json={"project_id": env["project_id"]},
            headers=env["csrf"](client),
        )
        assert r.status_code == 200, r.text

    def _apply(self, client, env, ws_id, new_text, para=0):
        return client.post(
            "/api/project/apply-change",
            params={"workspace_id": ws_id},
            json={
                "file_path": "main.tex",
                "paragraph_index": para,
                "new_text": new_text,
            },
            headers=env["csrf"](client),
        )

    def _undo(self, client, env, ws_id):
        return client.post(
            "/api/project/undo",
            params={"workspace_id": ws_id},
            json={"file_path": "main.tex"},
            headers=env["csrf"](client),
        )

    def test_two_users_collaboration_over_api(self, api_env):
        owner_client = api_env["client_factory"]()
        api_env["login"](owner_client, "owner")
        self._open(owner_client, api_env, "owner", "ws-a")
        editor_client = api_env["client_factory"]()
        api_env["login"](editor_client, "editor")
        self._open(editor_client, api_env, "editor", "ws-b")

        # A 改 → B 改
        r = self._apply(owner_client, api_env, "ws-a", "Owner paragraph.")
        assert r.status_code == 200, r.text
        r = self._apply(editor_client, api_env, "ws-b", "Editor paragraph.", para=1)
        assert r.status_code == 200, r.text

        # A undo 被 409 拒绝（B 的修改在后面），B 未受影响
        r = self._undo(owner_client, api_env, "ws-a")
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "file_conflict"

        # B undo 成功：只回退 B 自己的段落
        r = self._undo(editor_client, api_env, "ws-b")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["undone"] is True
        content = (api_env["root"] / "main.tex").read_text(encoding="utf-8")
        assert "Owner paragraph." in content
        assert "Editor paragraph." not in content
        assert "Second paragraph." in content

    def test_viewer_cannot_undo(self, api_env):
        auth_core.create_user("viewer", PASSWORD, role="user", status="active")
        now = db_module.utcnow()
        db_module.db_execute(
            "INSERT INTO project_collaborators (project_id, user_id, role, created_at) "
            "VALUES (?, (SELECT id FROM users WHERE username = 'viewer'), 'viewer', ?)",
            (api_env["project_id"], now),
        )
        client = api_env["client_factory"]()
        api_env["login"](client, "viewer")
        self._open(client, api_env, "viewer", "ws-v")
        r = self._undo(client, api_env, "ws-v")
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "no_permission"

    def test_quota_rollback_leaves_clean_edit_log(self, api_env):
        """写回超限回滚后：文件恢复、编辑日志无残留 op（栈不被污染）。"""
        owner_id = api_env["owner"]["id"]
        db_module.db_execute(
            "UPDATE users SET project_quota_bytes = ? WHERE id = ?",
            (10, owner_id),  # 任何增长都会超限
        )
        client = api_env["client_factory"]()
        api_env["login"](client, "owner")
        self._open(client, api_env, "owner", "ws-a")

        before = (api_env["root"] / "main.tex").read_text(encoding="utf-8")
        r = self._apply(client, api_env, "ws-a", "This grows the file.")
        assert r.status_code == 413
        assert r.json()["detail"]["error"] == "quota_exceeded"
        # 文件已直接恢复
        assert (api_env["root"] / "main.tex").read_text(encoding="utf-8") == before
        # 编辑日志没有失败写入的残留 op
        rows = db_module.db_query_all("SELECT * FROM workspace_edit_ops")
        assert rows == []
        status_r = client.get(
            "/api/project/undo-status",
            params={"file_path": "main.tex", "workspace_id": "ws-a"},
        )
        assert status_r.status_code == 200
        data = status_r.json()
        assert data["can_undo"] is False
        assert data["can_redo"] is False
