"""project_router /undo-status 端点测试。"""

from pathlib import Path

import pytest


class TestUndoStatus:
    """/api/project/undo-status 端点测试。"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from main import app

        return TestClient(app)

    def test_returns_both_flags_for_fresh_file(self, client, tmp_path):
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "undo-status-test"
        (tmp_path / "main.tex").write_text("hello", encoding="utf-8")
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(tmp_path))

        resp = client.get(
            "/api/project/undo-status",
            params={"file_path": "main.tex", "workspace_id": ws_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"can_undo", "can_redo"}
        assert data["can_undo"] is False
        assert data["can_redo"] is False

    def test_returns_false_when_file_missing(self, client, tmp_path):
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "undo-status-test-missing"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(tmp_path))

        resp = client.get(
            "/api/project/undo-status",
            params={"file_path": "no_such.tex", "workspace_id": ws_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data == {"can_undo": False, "can_redo": False}
