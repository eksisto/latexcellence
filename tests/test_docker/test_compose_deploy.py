"""Compose 编排冒烟（marker: docker，无 daemon 自动 skip）。

验收口径对应 v2 方案 §12.2：
- docker-compose.yml 语法合法（`docker compose config` 通过）；
- app 镜像可构建、启动；replicas=2 时两副本各自 /api/health 可达；
- 跨副本会话：在副本 A 注册的用户，经共享 SQLite 由副本 B 审批后可在
  副本 B 登录（会话与用户数据外置，无粘性会话）。

完整业务链路（注册审批 → 分片上传 → undo → 下载）由 tools/compose-smoke.sh
覆盖；本用例只做编排层最小验证，避免测试套件耗时过长。

运行方式：pytest -m docker（无 daemon / 构建失败自动 skip 或如实报告）。
"""

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
PROJECT_NAME = f"lxc-pytest-{uuid.uuid4().hex[:8]}"


def _daemon_available() -> bool:
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def compose_stack():
    """构建并启动 replicas=2 的隔离栈，yield (base_urls, cleanup)。

    临时目录放在仓库内（``.tmp-compose-test/``）：bind mount 的源路径必须
    对 Docker daemon 可见，平台临时目录（/tmp）在沙箱环境下不满足。
    """
    if not _daemon_available():
        pytest.skip("Docker daemon 不可用")
    if not COMPOSE_FILE.exists():
        pytest.skip("docker-compose.yml 不存在")

    tmp = COMPOSE_FILE.parent / ".tmp-compose-test" / uuid.uuid4().hex[:8]
    data_dir = tmp / "data"
    config_path = tmp / "config.json"
    data_dir.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "server": {"host": "0.0.0.0", "port": 8000, "dev_mode": False},
                "llm": {
                    "api_key": "",
                    "base_url": "https://smoke.invalid/v1",
                    "model": "smoke",
                },
                "logging": {"level": "INFO", "dir": "logs/"},
                "multi_user": {
                    "enabled": True,
                    "db_path": "/app/data/latexcellence.db",
                    "users_root": "/app/data/users/",
                    "staging_root": "/app/data/staging/",
                    "registration_enabled": True,
                },
                "smtp": {"enabled": False, "host": ""},
                "sandbox": {"mode": "local"},
            }
        ),
        encoding="utf-8",
    )

    def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", "compose", "-p", PROJECT_NAME, "-f", str(COMPOSE_FILE), *args],
            env={
                **os.environ,
                "LATEXCELLENCE_DATA_DIR": str(data_dir),
                "LATEXCELLENCE_CONFIG_PATH": str(config_path),
                "LATEXCELLENCE_ADMIN_USERNAME": "pytest-admin",
                "LATEXCELLENCE_ADMIN_PASSWORD": "PytestAdmin!2026",
            },
            check=check,
            capture_output=True,
            text=True,
            timeout=900,
        )

    # 配置合法性（不启动）
    compose("config", "-q")

    try:
        try:
            compose("build", "app")
        except subprocess.CalledProcessError as e:
            if "read-only file system" in (e.stderr or ""):
                pytest.skip("buildx 状态目录只读（沙箱环境限制，见 DOCKER_CONFIG）")
            raise
        compose("up", "-d", "--wait", "--scale", "app=2", "app")
        # 直接按容器 IP 访问各副本（绕过端口发布，天然覆盖双副本）
        ps_out = compose("ps", "-q", "app").stdout.split()
        assert len(ps_out) >= 2, f"期望 2 个副本，实际 {len(ps_out)}"
        urls = []
        for cid in ps_out[:2]:
            ip = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                    cid,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.strip()
            urls.append(f"http://{ip}:8000")
        yield urls
    finally:
        try:
            compose("down", "--remove-orphans", "--volumes", check=False)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(tmp, ignore_errors=True)


def _http_get_json(url: str, timeout: float = 5.0) -> dict | None:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001
        return None


def test_compose_config_valid() -> None:
    """docker-compose.yml 语法与 schema 合法。"""
    if not _daemon_available():
        pytest.skip("Docker daemon 不可用")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "-p",
            PROJECT_NAME,
            "config",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "LATEXCELLENCE_DATA_DIR": "/tmp/lxc-data",
            "LATEXCELLENCE_CONFIG_PATH": "/tmp/lxc-config.json",
        },
    )
    assert result.returncode == 0, result.stderr


class TestReplicaStack:
    """replicas=2 栈级验证（module 级 fixture 启动，构建耗时较长）。"""

    def test_both_replicas_healthy(self, compose_stack):
        deadline = time.monotonic() + 120
        pending = list(compose_stack)
        while pending and time.monotonic() < deadline:
            for url in list(pending):
                health = _http_get_json(f"{url}/api/health")
                if health and health.get("status") == "ok":
                    pending.remove(url)
            time.sleep(2)
        assert not pending, f"健康检查未通过：{pending}"

    def test_cross_replica_register_approve_login(self, compose_stack):
        """A 副本注册 → B 副本审批（管理员）→ B 副本登录：状态跨副本可续。"""
        import urllib.request

        replica_a, replica_b = compose_stack[0], compose_stack[1]

        def post(url: str, path: str, payload: dict, cookie: str | None = None):
            req = urllib.request.Request(
                f"{url}{path}",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=json.dumps(payload).encode(),
            )
            if cookie:
                req.add_header("Cookie", cookie)
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    set_cookie = resp.headers.get_all("Set-Cookie") or []
                    return resp.status, json.loads(resp.read().decode()), set_cookie
            except Exception as e:  # noqa: BLE001
                return getattr(e, "code", None), str(e), []

        username = f"user-{uuid.uuid4().hex[:8]}"
        status, _, _ = post(
            replica_a,
            "/api/auth/register",
            {"username": username, "password": "Cross!Replica1"},
        )
        assert status in (200, 201)

        status, body, admin_cookies = post(
            replica_b,
            "/api/auth/login",
            {"username": "pytest-admin", "password": "PytestAdmin!2026"},
        )
        assert status == 200, body
        session_cookie = next(
            (
                c.split(";")[0]
                for c in admin_cookies
                if c.startswith("latexcellence_session")
            ),
            None,
        )
        csrf_cookie = next(
            (
                c.split(";")[0]
                for c in admin_cookies
                if c.startswith("latexcellence_csrf")
            ),
            None,
        )

        # 管理员列表查新用户 id（GET，携带 B 副本签发的会话 Cookie）
        req = urllib.request.Request(f"{replica_b}/api/admin/users")
        req.add_header("Cookie", session_cookie)
        with urllib.request.urlopen(req, timeout=10) as resp:
            users = json.loads(resp.read().decode())["users"]
        user_id = next(u["id"] for u in users if u["username"] == username)

        # CSRF 双提交
        approve_req = urllib.request.Request(
            f"{replica_b}/api/admin/users/{user_id}/approve",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Cookie": f"{session_cookie}; {csrf_cookie}",
                "X-Latexcellence-CSRF": csrf_cookie.split("=", 1)[1],
            },
            data=b"{}",
        )
        with urllib.request.urlopen(approve_req, timeout=10) as resp:
            assert resp.status == 200

        # 新用户登录走 A 副本：会话表共享，任一副本均可认证
        status, body, _ = post(
            replica_a,
            "/api/auth/login",
            {"username": username, "password": "Cross!Replica1"},
        )
        assert status == 200, body
