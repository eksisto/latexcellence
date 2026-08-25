"""测试 AccessSecurityMiddleware：CORS 白名单与访问令牌校验。"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import config as cfg_module
from backend.core.security_middleware import AccessSecurityMiddleware


def _build_app():
    app = FastAPI()
    app.add_middleware(AccessSecurityMiddleware)

    @app.get("/api/ping")
    async def ping():
        return {"ok": True}

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.post("/api/ping")
    async def ping_post():
        return {"ok": True}

    return app


@pytest.fixture(autouse=True)
def _reset_config():
    """每次测试后重置 config 全局状态，避免污染其他测试。"""
    yield
    cfg_module._config = None
    cfg_module._config_path = None
    cfg_module._secrets_from_env = set()


def _load_test_config(tmp_path, token="", origins=None):
    data = {
        "server": {
            "allowed_origins": (
                list(origins)
                if origins is not None
                else ["http://localhost:5173", "https://latexcellence.example.com"]
            ),
        },
        "security": {"access_token": token},
    }
    config_path = tmp_path / "config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    cfg_module.load_config(str(config_path))


class TestCorsAllowlist:
    def test_allowed_origin_gets_cors_header(self, tmp_path):
        _load_test_config(tmp_path)
        client = TestClient(_build_app())
        res = client.get(
            "/api/ping",
            headers={"Origin": "https://latexcellence.example.com"},
        )
        assert res.status_code == 200
        assert (
            res.headers["access-control-allow-origin"]
            == "https://latexcellence.example.com"
        )

    def test_disallowed_origin_rejected(self, tmp_path):
        _load_test_config(tmp_path)
        client = TestClient(_build_app())
        res = client.get("/api/ping", headers={"Origin": "https://evil.example"})
        assert res.status_code == 403

    def test_preflight_allowed_origin(self, tmp_path):
        _load_test_config(tmp_path)
        client = TestClient(_build_app())
        res = client.options(
            "/api/ping",
            headers={
                "Origin": "https://latexcellence.example.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-latexcellence-token",
            },
        )
        assert res.status_code == 204
        assert res.headers["access-control-allow-methods"] == "*"
        assert res.headers["access-control-allow-headers"] == "*"


class TestSameOrigin:
    """同源生产模式：FastAPI 托管 static/ 时页面源即本服务，必须自动放行。"""

    def test_same_origin_get_allowed(self, tmp_path):
        _load_test_config(tmp_path)
        client = TestClient(_build_app(), base_url="http://localhost:8000")
        res = client.get("/api/ping", headers={"Origin": "http://localhost:8000"})
        assert res.status_code == 200

    def test_same_origin_post_allowed(self, tmp_path):
        """浏览器对同源 POST 也携带 Origin，不应被当作跨域拒绝。"""
        _load_test_config(tmp_path)
        client = TestClient(_build_app(), base_url="http://localhost:8000")
        res = client.post("/api/ping", headers={"Origin": "http://localhost:8000"})
        assert res.status_code == 200

    def test_same_origin_preflight_allowed(self, tmp_path):
        _load_test_config(tmp_path)
        client = TestClient(_build_app(), base_url="http://localhost:8000")
        res = client.options(
            "/api/ping",
            headers={
                "Origin": "http://localhost:8000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert res.status_code == 204
        assert res.headers["access-control-allow-origin"] == "http://localhost:8000"

    def test_different_port_is_not_same_origin(self, tmp_path):
        """端口不同视为跨域，仍按白名单拒绝。"""
        _load_test_config(tmp_path)
        client = TestClient(_build_app(), base_url="http://localhost:8000")
        res = client.get("/api/ping", headers={"Origin": "http://localhost:9000"})
        assert res.status_code == 403

    def test_same_origin_still_requires_token(self, tmp_path):
        """同源放行只针对 CORS 来源检查，不绕过访问令牌校验。"""
        _load_test_config(tmp_path, token="secret-token")
        client = TestClient(_build_app(), base_url="http://localhost:8000")
        res = client.get("/api/ping", headers={"Origin": "http://localhost:8000"})
        assert res.status_code == 401

    def test_same_origin_with_valid_token_allowed(self, tmp_path):
        """同源 + 正确令牌时正常放行。"""
        _load_test_config(tmp_path, token="secret-token")
        client = TestClient(_build_app(), base_url="http://localhost:8000")
        res = client.get(
            "/api/ping",
            headers={
                "Origin": "http://localhost:8000",
                "X-Latexcellence-Token": "secret-token",
            },
        )
        assert res.status_code == 200

    def test_same_origin_host_case_insensitive(self, tmp_path):
        """Origin 的 host 大小写不影响同源判定。"""
        _load_test_config(tmp_path)
        client = TestClient(_build_app(), base_url="http://localhost:8000")
        res = client.get(
            "/api/ping",
            headers={"Origin": "http://LOCALHOST:8000"},
        )
        assert res.status_code == 200

    def test_suffix_host_is_not_same_origin(self, tmp_path):
        """Origin 是 Host 的域名后缀时不得误判为同源。"""
        _load_test_config(tmp_path)
        client = TestClient(_build_app(), base_url="http://localhost:8000")
        res = client.get(
            "/api/ping",
            headers={"Origin": "http://localhost:8000.evil.example"},
        )
        assert res.status_code == 403


class TestFailClosed:
    """空白名单与配置缺失时必须拒绝，不能退化为放行。"""

    def test_empty_allowlist_rejects_cross_origin(self, tmp_path):
        _load_test_config(tmp_path, origins=[])
        client = TestClient(_build_app())
        res = client.get("/api/ping", headers={"Origin": "https://evil.example"})
        assert res.status_code == 403

    def test_empty_allowlist_allows_same_origin(self, tmp_path):
        _load_test_config(tmp_path, origins=[])
        client = TestClient(_build_app(), base_url="http://localhost:8000")
        res = client.get("/api/ping", headers={"Origin": "http://localhost:8000"})
        assert res.status_code == 200

    def test_empty_allowlist_still_requires_token(self, tmp_path):
        """空白名单只放开同源来源检查，访问令牌校验依然生效。"""
        _load_test_config(tmp_path, origins=[], token="secret-token")
        client = TestClient(_build_app(), base_url="http://localhost:8000")
        res = client.get("/api/ping", headers={"Origin": "http://localhost:8000"})
        assert res.status_code == 401

    def test_empty_allowlist_rejects_cross_origin_preflight(self, tmp_path):
        _load_test_config(tmp_path, origins=[])
        client = TestClient(_build_app())
        res = client.options(
            "/api/ping",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert res.status_code == 403

    def test_missing_config_rejected(self):
        """配置未加载时中间件拒绝请求，而不是放行。"""
        cfg_module._config = None
        cfg_module._config_path = None
        client = TestClient(_build_app())
        res = client.get("/api/ping")
        assert res.status_code == 503

    def test_missing_config_rejects_health_too(self):
        """配置未加载时连健康检查也拒绝，保证严格 fail-closed。"""
        cfg_module._config = None
        cfg_module._config_path = None
        client = TestClient(_build_app())
        res = client.get("/api/health")
        assert res.status_code == 503


class TestAccessToken:
    def test_missing_token_rejected(self, tmp_path):
        _load_test_config(tmp_path, token="secret-token")
        client = TestClient(_build_app())
        res = client.get("/api/ping")
        assert res.status_code == 401

    def test_header_token_accepted(self, tmp_path):
        _load_test_config(tmp_path, token="secret-token")
        client = TestClient(_build_app())
        res = client.get(
            "/api/ping",
            headers={"X-Latexcellence-Token": "secret-token"},
        )
        assert res.status_code == 200

    def test_query_token_accepted(self, tmp_path):
        """iframe/img/EventSource 等场景通过 URL 传递令牌。"""
        _load_test_config(tmp_path, token="secret-token")
        client = TestClient(_build_app())
        res = client.get("/api/ping?token=secret-token")
        assert res.status_code == 200

    def test_wrong_token_rejected(self, tmp_path):
        _load_test_config(tmp_path, token="secret-token")
        client = TestClient(_build_app())
        res = client.get(
            "/api/ping",
            headers={"X-Latexcellence-Token": "wrong"},
        )
        assert res.status_code == 401

    def test_health_endpoint_is_public(self, tmp_path):
        """连接测试依赖 /api/health，令牌未配置时也应可访问。"""
        _load_test_config(tmp_path, token="secret-token")
        client = TestClient(_build_app())
        res = client.get("/api/health")
        assert res.status_code == 200


class TestAccessTokenConfigMasking:
    def test_masked_token_keeps_original(self, tmp_path):
        _load_test_config(tmp_path, token="real-secret")
        cfg_module.update_config_from_dict({"security": {"access_token": "***cret"}})
        assert cfg_module.get_config().security.access_token == "real-secret"

    def test_empty_token_clears(self, tmp_path):
        _load_test_config(tmp_path, token="real-secret")
        cfg_module.update_config_from_dict({"security": {"access_token": ""}})
        assert cfg_module.get_config().security.access_token == ""

    def test_partial_update_without_token_preserves_token(self, tmp_path):
        """部分更新不携带 access_token 时，不应意外清除令牌。"""
        _load_test_config(tmp_path, token="real-secret")
        cfg_module.update_config_from_dict({"security": {}})
        assert cfg_module.get_config().security.access_token == "real-secret"

    def test_short_token_masked_keeps_original(self, tmp_path):
        """短令牌掩码（****）与空值区分，保存后保留原值。"""
        _load_test_config(tmp_path, token="abcd")
        cfg_module.update_config_from_dict({"security": {"access_token": "****"}})
        assert cfg_module.get_config().security.access_token == "abcd"

    def test_new_token_updates(self, tmp_path):
        _load_test_config(tmp_path, token="real-secret")
        cfg_module.update_config_from_dict({"security": {"access_token": "new-secret"}})
        assert cfg_module.get_config().security.access_token == "new-secret"
