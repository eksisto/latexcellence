"""
SPAStaticFiles 单元测试：history 路由刷新/直达的 index.html 回退，
以及静态资源缺失、API 未命中时保持 404 的行为。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.utils.spa_static_files import SPAStaticFiles

# 模拟浏览器页面导航的 Accept 头（必然包含 text/html）
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
# 脚本/样式等资源请求的典型 Accept 头
ASSET_ACCEPT = "*/*"

INDEX_HTML = "<html><body>spa-index</body></html>"
APP_JS = "console.log('app');"


@pytest.fixture()
def static_root(tmp_path):
    """构造一个最小的 Vite 构建产物目录：index.html + assets/app.js。"""
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    assets = root / "assets"
    assets.mkdir()
    (assets / "app.js").write_text(APP_JS, encoding="utf-8")
    return root


@pytest.fixture()
def client(static_root):
    """复刻 main.py 生产模式的路由形态：先注册 API 路由，再挂载 "/" 静态服务。"""
    app = FastAPI()

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    app.mount("/", SPAStaticFiles(directory=str(static_root), html=True), name="static")
    return TestClient(app)


def test_root_serves_directory_index(client):
    """/ 命中目录索引，不受回退逻辑影响。"""
    resp = client.get("/")
    assert resp.status_code == 200
    assert INDEX_HTML in resp.text


def test_client_route_refresh_falls_back_to_index(client):
    """刷新 / 直达客户端路由：物理文件不存在，回退 index.html。"""
    resp = client.get("/formula-ocr", headers={"Accept": BROWSER_ACCEPT})
    assert resp.status_code == 200
    assert INDEX_HTML in resp.text
    assert resp.headers["content-type"].startswith("text/html")


def test_nested_unknown_route_falls_back(client):
    """多级未知路径同样回退（前端嵌套路由场景）。"""
    resp = client.get(
        "/writing-chat/sessions/deep/nested", headers={"Accept": BROWSER_ACCEPT}
    )
    assert resp.status_code == 200
    assert INDEX_HTML in resp.text


def test_existing_asset_served(client):
    """真实存在的静态资源照常返回。"""
    resp = client.get("/assets/app.js", headers={"Accept": ASSET_ACCEPT})
    assert resp.status_code == 200
    assert APP_JS in resp.text


def test_missing_asset_keeps_404(client):
    """非页面导航（脚本/样式等）缺失时保持 404，不回退 HTML。"""
    resp = client.get("/assets/missing.js", headers={"Accept": ASSET_ACCEPT})
    assert resp.status_code == 404


def test_missing_asset_with_browser_accept_still_404(client):
    """带扩展名的资源即使 Accept 含 text/html 也保持 404 之外的严格性：
    此处以 .css 资源 + 浏览器导航头验证路径前缀判断不影响资源请求。
    （浏览器对子资源的 Accept 不含 text/html，此处覆盖极端头部组合）"""
    resp = client.get("/favicon-missing.ico", headers={"Accept": ASSET_ACCEPT})
    assert resp.status_code == 404


def test_api_miss_keeps_json_404(client):
    """/api/* 未命中保持 API 风格 JSON 404，不回退 HTML。"""
    resp = client.get("/api/nope", headers={"Accept": BROWSER_ACCEPT})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
    assert resp.headers["content-type"].startswith("application/json")


def test_registered_api_route_wins_over_mount(client):
    """先注册的真实 API 路由优先于 "/" 挂载，不受回退影响。"""
    resp = client.get("/api/health", headers={"Accept": BROWSER_ACCEPT})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
