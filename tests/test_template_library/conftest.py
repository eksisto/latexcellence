"""模板库测试共享 fixtures（多用户 / 单用户双模式）。"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.config import get_config
from backend.core.multiuser import auth as auth_module
from backend.core.multiuser import db as db_module
from backend.core.multiuser import userspace
from backend.core.security_middleware import AccessSecurityMiddleware

PASSWORD = "password-123"


def build_tl_app() -> FastAPI:
    """多用户测试应用：中间件 + 认证 + 项目 + 模板库路由。"""
    from backend.modules.auth.router import admin_router, auth_router
    from backend.modules.project_router import router as project_router
    from backend.modules.template_library.router import router as tl_router

    app = FastAPI()
    app.add_middleware(AccessSecurityMiddleware)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/config/status")
    async def config_status():
        return {"multi_user": True}

    app.include_router(project_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(tl_router)
    return app


def build_single_app() -> FastAPI:
    """单用户测试应用：仅模板库路由（无认证/中间件，保持存量行为）。"""
    from backend.modules.template_library.router import router as tl_router

    app = FastAPI()
    app.include_router(tl_router)
    return app


@pytest.fixture
def env(tmp_path):
    """多用户环境（复用 multiuser conftest 构建）+ 两个活跃用户。"""
    from tests.test_multiuser.conftest import _reset_config_state, build_mu_env

    env = build_mu_env(tmp_path)
    env["alice"] = auth_module.create_user(
        "alice", PASSWORD, role="user", status="active"
    )
    env["bob"] = auth_module.create_user("bob", PASSWORD, role="user", status="active")
    # 关闭 Git 自动初始化后台任务，避免与目录断言竞态
    get_config().features.git_manage.enabled = False
    yield env
    db_module.close_db()
    _reset_config_state()


@pytest.fixture
def client(env) -> TestClient:
    return TestClient(build_tl_app())


@pytest.fixture
def alice_client(env, client) -> TestClient:
    r = client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture
def su_env(tmp_path):
    """单用户环境：把模板库目录指向临时路径（测试后还原，避免污染全局配置）。"""
    cfg = get_config()
    old_dir = cfg.features.template_library.user_library_dir
    old_git = cfg.features.git_manage.enabled
    cfg.features.template_library.user_library_dir = str(tmp_path / "library")
    cfg.features.git_manage.enabled = False
    yield tmp_path
    cfg.features.template_library.user_library_dir = old_dir
    cfg.features.git_manage.enabled = old_git


@pytest.fixture
def su_client(su_env) -> TestClient:
    return TestClient(build_single_app())


def csrf(client: TestClient, headers: dict | None = None) -> dict:
    """为变更类请求附加与客户端 CSRF Cookie 值一致的请求头（双提交 CSRF）。"""
    from tests.test_multiuser.conftest import CSRF_COOKIE, CSRF_HEADER

    h = dict(headers or {})
    h[CSRF_HEADER] = client.cookies.get(CSRF_COOKIE, "")
    return h


def make_zip(files: dict[str, str]) -> bytes:
    """内存构造 zip 压缩包（上传测试用）。"""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


def make_manifest(**overrides) -> dict:
    """构造合法 manifest 字典（可覆盖字段）。"""
    data = {
        "id": "my-template",
        "name": "我的测试模板",
        "name_en": "My Test Template",
        "description": "用于测试的模板",
        "category": "paper",
        "main_file": "main.tex",
        "engine": "xelatex",
        "author": "tester",
        "license": "MIT",
        "source": "upload",
        "created_at": "2026-01-01",
    }
    data.update(overrides)
    return data


def make_user_template(user_id: str, files: dict[str, str], **meta) -> dict:
    """直接在指定用户库落一个模板（绕过上传），返回 manifest dict。"""
    from backend.modules.template_library import user_store
    from backend.modules.template_library.models import TemplateManifest

    staging = user_store.library_root(user_id).parent / "_staging_tpl"
    staging.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = staging / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    manifest = TemplateManifest.model_validate(make_manifest(**meta))
    summary = user_store.create_template(user_id, staging, manifest)
    import shutil

    shutil.rmtree(staging, ignore_errors=True)
    return summary.model_dump()


def make_project_row(env, user_id: str, name: str, files: dict[str, str]) -> str:
    """直接落地一个活动项目（不走上传），返回 project_id。"""
    project_id = uuid.uuid4().hex
    root = userspace.project_dir(user_id, project_id)
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    size = sum((root / r).stat().st_size for r in files)
    now = db_module.utcnow()
    db_module.db_execute(
        "INSERT INTO projects (id, owner_id, name, size_bytes, status, deleted_at, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, 'active', NULL, ?, ?)",
        (project_id, user_id, name, size, now, now),
    )
    return project_id
