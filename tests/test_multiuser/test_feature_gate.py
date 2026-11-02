"""功能开关服务端强制依赖测试（require_feature_enabled 工厂）。

多用户模式下 `features.<x>.enabled=False` 时模块端点整体 404
（口径同模板库，不泄露存在性）；单用户模式不设门，开关关闭仅由
前端隐藏入口。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.config import get_config
from backend.modules.bib_manager.router import router as bib_manager_router


@pytest.fixture
def bib_app():
    app = FastAPI()
    app.include_router(bib_manager_router)
    return TestClient(app)


def test_multiuser_disabled_feature_returns_404(mu_env, bib_app, monkeypatch):
    """多用户模式：开关关闭时端点整体 404，先于端点级认证依赖短路。"""
    monkeypatch.setattr(get_config().features.bib_manager, "enabled", False)
    r = bib_app.get("/api/bib-manager/list-bib-files", params={"workspace_id": "ws"})
    assert r.status_code == 404
    assert r.json()["detail"] == "Not found"


def test_multiuser_enabled_feature_reaches_endpoint_auth(mu_env, bib_app):
    """多用户模式：开关开启时请求穿过门禁，到达端点级认证（未登录 401）。"""
    assert get_config().features.bib_manager.enabled is True
    r = bib_app.get("/api/bib-manager/list-bib-files", params={"workspace_id": "ws"})
    assert r.status_code == 401


def test_single_user_feature_gate_not_enforced(bib_app, monkeypatch):
    """单用户模式：开关关闭不设门（保持既有行为，仅前端隐藏入口）。"""
    monkeypatch.setattr(get_config().features.bib_manager, "enabled", False)
    r = bib_app.get("/api/bib-manager/list-bib-files", params={"workspace_id": "ws"})
    assert r.status_code != 404
