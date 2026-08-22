"""多用户模式 v2 P5：大文件分片上传与断点续传测试（方案 §10）。

覆盖内容：
    - 迁移 v6：upload_sessions 表与 schema 版本
    - init：zip / directory 两 kind 全流程；扩展名白名单、manifest 结构
      （路径穿越/重复/大小写冲突/分块数/md5 格式）、剩余配额预检
    - part：X-Part-MD5 错拒、体积超限拒绝、索引越界拒绝、幂等覆盖重传
    - 并发分片：同文件不同块并发落盘安全，complete 成功
    - status：部分上传后位图正确；断点续传补齐后 complete 成功
    - complete：分块缺失拒绝（会话保留可续传）、全量 MD5 复核损坏拒绝、
      配额二次复核失败整包拒绝且 staging 清干净
    - abort：行 + staging 清理；后续访问 404
    - 归属：他人 sid 一律 404 不泄露存在性
    - TTL：过期会话访问即 410 并清场；维护循环清理过期会话与孤儿目录，
      通用 clean_staging 跳过活跃会话目录

Service 层不发真实网络请求；测试数据不含 LaTeX 内容校验路径。
"""

import hashlib
import io
import json
import os
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.config import get_config
from backend.core.multiuser import auth as auth_core
from backend.core.multiuser import db as db_module
from backend.core.multiuser import quota, upload_sessions, userspace
from backend.core.security_middleware import AccessSecurityMiddleware
from tests.test_multiuser.conftest import (
    _reset_config_state,
    build_mu_env,
    csrf,
)

PASSWORD = "password-123"
CHUNK = 1024 * 1024  # 与 mu_extra 的 chunk_part_max_mb=1 对齐


def build_mu_projects_app() -> FastAPI:
    """多用户测试应用：中间件 + 认证 + 项目路由。"""
    from backend.modules.auth.router import admin_router, auth_router
    from backend.modules.projects.router import router as projects_router

    app = FastAPI()
    app.add_middleware(AccessSecurityMiddleware)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/config/status")
    async def config_status():
        return {"multi_user": True}

    app.include_router(projects_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    return app


@pytest.fixture
def env(tmp_path):
    """多用户环境（chunk_part_max_mb=1 便于小体量多分块）+ 两个活跃用户。"""
    env = build_mu_env(tmp_path, mu_extra={"chunk_part_max_mb": 1})
    env["alice"] = auth_core.create_user(
        "alice", PASSWORD, role="user", status="active"
    )
    env["bob"] = auth_core.create_user("bob", PASSWORD, role="user", status="active")
    yield env
    db_module.close_db()
    _reset_config_state()


@pytest.fixture
def client(env) -> TestClient:
    return TestClient(build_mu_projects_app())


@pytest.fixture
def alice_client(env, client) -> TestClient:
    _login(client, "alice")
    return client


@pytest.fixture
def bob_client(env) -> TestClient:
    c = TestClient(build_mu_projects_app())
    _login(c, "bob")
    return c


def _login(client: TestClient, username: str) -> None:
    r = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert r.status_code == 200, r.text


# ── 构造工具 ─────────────────────────────────────────────────────
def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _entry(path: str, data: bytes, chunk_size: int = CHUNK) -> dict:
    return {
        "path": path,
        "size": len(data),
        "md5": _md5(data),
        "chunks": (len(data) + chunk_size - 1) // chunk_size,
    }


def _zip_bytes(items: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in items.items():
            z.writestr(name, content)
    return buf.getvalue()


def _init_zip(client: TestClient, payload: bytes, name="mydoc"):
    """初始化一个 zip kind 会话（单条目），返回 (response, sid)。"""
    body = {
        "name": name,
        "kind": "zip",
        "total_bytes": len(payload),
        "files": [_entry("mydoc.zip", payload)],
    }
    r = client.post("/api/projects/upload-sessions", json=body, headers=csrf(client))
    return r, (r.json() or {}).get("session_id") if r.status_code == 201 else None


def _init_dir(client: TestClient, files: dict[str, bytes], name="mydir"):
    """初始化一个 directory kind 会话，返回 (response, sid)。"""
    entries = [_entry(p, d) for p, d in files.items()]
    total = sum(len(d) for d in files.values())
    body = {"name": name, "kind": "directory", "total_bytes": total, "files": entries}
    r = client.post("/api/projects/upload-sessions", json=body, headers=csrf(client))
    return r, (r.json() or {}).get("session_id") if r.status_code == 201 else None


def _put_part(client: TestClient, sid: str, fi: int, ci: int, data: bytes):
    headers = csrf(client)
    headers["X-Part-MD5"] = _md5(data)
    return client.put(
        f"/api/projects/upload-sessions/{sid}/parts/{fi}/{ci}",
        content=data,
        headers=headers,
    )


def _upload_dir_chunks(client: TestClient, sid: str, files: dict[str, bytes]) -> None:
    for fi, (_, data) in enumerate(files.items()):
        if not data:
            continue
        n = (len(data) + CHUNK - 1) // CHUNK
        for ci in range(n):
            piece = data[ci * CHUNK : (ci + 1) * CHUNK]
            r = _put_part(client, sid, fi, ci, piece)
            assert r.status_code == 200, r.text


def _seed_project_row(user_id: str, name: str, size_bytes: int) -> str:
    """直接落地一条活动项目行（配额记账用，不写磁盘）。"""
    pid = uuid.uuid4().hex
    now = db_module.utcnow()
    db_module.db_execute(
        "INSERT INTO projects (id, owner_id, name, size_bytes, status, deleted_at, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, 'active', NULL, ?, ?)",
        (pid, user_id, name, size_bytes, now, now),
    )
    return pid


def _session_dir(sid: str):
    return userspace.get_staging_root() / sid


# ── 迁移 ─────────────────────────────────────────────────────────
class TestMigration:
    def test_schema_version_and_table(self, env):
        # v6 为 upload_sessions 引入版本；当前最新版本可能更高（后续阶段追加）
        assert db_module.SCHEMA_VERSION >= 6
        status = db_module.schema_status()
        assert status["user_version"] == db_module.SCHEMA_VERSION
        assert status["up_to_date"]
        row = db_module.db_query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='upload_sessions'"
        )
        assert row is not None

    def test_migration_idempotent(self, env):
        applied = db_module.apply_migrations(db_module._require_conn())
        assert applied == []


# ── init ─────────────────────────────────────────────────────────
class TestInit:
    def test_init_zip_ok(self, env, alice_client):
        payload = _zip_bytes({"mydoc/main.tex": "hello"})
        r, sid = _init_zip(alice_client, payload)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["session_id"] == sid
        assert body["chunk_size"] == CHUNK
        assert body["status"] == "uploading"
        # 会话行与 staging 目录已建立
        row = db_module.db_query_one(
            "SELECT * FROM upload_sessions WHERE id = ?", (sid,)
        )
        assert row is not None and row["user_id"] == env["alice"]["id"]
        manifest = json.loads(row["manifest"])
        assert manifest["chunk_size"] == CHUNK
        assert _session_dir(sid).is_dir()
        assert upload_sessions.is_session_staging_dir(_session_dir(sid))

    def test_init_extension_whitelist(self, env, alice_client):
        body = {
            "name": "x",
            "kind": "zip",
            "total_bytes": 3,
            "files": [_entry("archive.7z", b"abc")],
        }
        r = alice_client.post(
            "/api/projects/upload-sessions", json=body, headers=csrf(alice_client)
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_archive"

    def test_init_rejects_path_traversal(self, env, alice_client):
        body = {
            "name": "x",
            "kind": "directory",
            "total_bytes": 3,
            "files": [
                {"path": "../evil.tex", "size": 3, "md5": _md5(b"abc"), "chunks": 1}
            ],
        }
        r = alice_client.post(
            "/api/projects/upload-sessions", json=body, headers=csrf(alice_client)
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "archive_unsafe"

    def test_init_rejects_case_conflict(self, env, alice_client):
        data = b"x"
        body = {
            "name": "x",
            "kind": "directory",
            "total_bytes": 1,
            "files": [
                {"path": "A.tex", "size": 1, "md5": _md5(data), "chunks": 1},
                {"path": "a.tex", "size": 1, "md5": _md5(data), "chunks": 1},
            ],
        }
        r = alice_client.post(
            "/api/projects/upload-sessions", json=body, headers=csrf(alice_client)
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "archive_unsafe"

    def test_init_rejects_total_mismatch(self, env, alice_client):
        entry = _entry("main.tex", b"abc")
        body = {
            "name": "x",
            "kind": "directory",
            "total_bytes": entry["size"] + 100,
            "files": [entry],
        }
        r = alice_client.post(
            "/api/projects/upload-sessions", json=body, headers=csrf(alice_client)
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_request"

    def test_init_rejects_chunk_count_mismatch(self, env, alice_client):
        entry = _entry("main.tex", b"abc")
        entry["chunks"] = 5
        body = {
            "name": "x",
            "kind": "directory",
            "total_bytes": entry["size"],
            "files": [entry],
        }
        r = alice_client.post(
            "/api/projects/upload-sessions", json=body, headers=csrf(alice_client)
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_request"

    def test_init_quota_precheck(self, tmp_path):
        env = build_mu_env(
            tmp_path, mu_extra={"chunk_part_max_mb": 1, "default_project_quota_mb": 1}
        )
        auth_core.create_user("alice", PASSWORD, role="user", status="active")
        try:
            client = TestClient(build_mu_projects_app())
            _login(client, "alice")
            data = b"x" * (2 * CHUNK)  # 2MB > 1MB 配额
            body = {
                "name": "big",
                "kind": "directory",
                "total_bytes": len(data),
                "files": [_entry("big.bin", data)],
            }
            r = client.post(
                "/api/projects/upload-sessions", json=body, headers=csrf(client)
            )
            assert r.status_code == 413
            assert r.json()["detail"]["error"] == "quota_exceeded"
        finally:
            db_module.close_db()
            _reset_config_state()

    def test_config_status_exposes_chunk_size(self, env):
        """main.config_status 只读透出 chunk_part_max_mb 供前端切片对齐。"""
        import asyncio

        from main import config_status

        payload = asyncio.run(config_status())
        assert payload["chunk_part_max_mb"] == get_config().multi_user.chunk_part_max_mb


# ── part ─────────────────────────────────────────────────────────
class TestParts:
    def test_put_part_ok_and_status_bitmap(self, env, alice_client):
        files = {"main.tex": b"hello world", "sub/a.tex": b"nested"}
        _, sid = _init_dir(alice_client, files)
        _upload_dir_chunks(alice_client, sid, files)
        r = alice_client.get(f"/api/projects/upload-sessions/{sid}")
        assert r.status_code == 200
        st = r.json()
        assert st["received_bytes"] == sum(len(v) for v in files.values())
        assert all(f["bitmap"] == [True] for f in st["files"])

    def test_put_part_wrong_md5_rejected(self, env, alice_client):
        files = {"main.tex": b"abcdef"}
        _, sid = _init_dir(alice_client, files)
        headers = csrf(alice_client)
        headers["X-Part-MD5"] = "0" * 32
        r = alice_client.put(
            f"/api/projects/upload-sessions/{sid}/parts/0/0",
            content=b"abcdef",
            headers=headers,
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "part_md5_mismatch"
        # 拒绝的分片不落盘
        parts = _session_dir(sid) / "parts"
        assert not any(parts.iterdir())

    def test_put_part_missing_header_rejected(self, env, alice_client):
        _, sid = _init_dir(alice_client, {"a.txt": b"data"})
        r = alice_client.put(
            f"/api/projects/upload-sessions/{sid}/parts/0/0",
            content=b"data",
            headers=csrf(alice_client),
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "part_md5_mismatch"

    def test_put_part_index_out_of_range(self, env, alice_client):
        _, sid = _init_dir(alice_client, {"a.txt": b"data"})
        r = _put_part(alice_client, sid, 7, 0, b"data")
        assert r.status_code == 400
        r = _put_part(alice_client, sid, 0, 3, b"data")
        assert r.status_code == 400

    def test_put_part_oversize_body(self, env, alice_client):
        _, sid = _init_dir(alice_client, {"big.bin": b"x"})
        big = b"y" * (CHUNK + 1)
        headers = csrf(alice_client)
        headers["X-Part-MD5"] = _md5(big)
        r = alice_client.put(
            f"/api/projects/upload-sessions/{sid}/parts/0/0",
            content=big,
            headers=headers,
        )
        assert r.status_code == 413
        assert r.json()["detail"]["error"] == "upload_too_large"

    def test_put_part_idempotent_overwrite(self, env, alice_client):
        data = b"stable content"
        _, sid = _init_dir(alice_client, {"a.txt": data})
        for _ in range(2):  # 同块重传两次均成功（幂等）
            r = _put_part(alice_client, sid, 0, 0, data)
            assert r.status_code == 200
        r = alice_client.post(
            f"/api/projects/upload-sessions/{sid}/complete", headers=csrf(alice_client)
        )
        assert r.status_code == 201, r.text

    def test_concurrent_parts_same_file(self, env, alice_client):
        """并发分片同文件不同块安全落盘（核心层并发 + API 复核）。"""
        payload = os.urandom(3 * CHUNK + 4096)
        files = {"blob.bin": payload}
        _, sid = _init_dir(alice_client, files)
        chunks = [
            payload[i * CHUNK : (i + 1) * CHUNK]
            for i in range((len(payload) + CHUNK - 1) // CHUNK)
        ]
        uid = env["alice"]["id"]
        with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
            results = list(
                pool.map(
                    lambda args: upload_sessions.store_part(
                        uid, sid, 0, args[0], args[1], _md5(args[1])
                    ),
                    enumerate(chunks),
                )
            )
        assert all(
            r == {"received": True, "file_index": 0, "chunk_index": i}
            for i, r in enumerate(results)
        )
        r = alice_client.get(f"/api/projects/upload-sessions/{sid}")
        assert r.json()["files"][0]["bitmap"] == [True] * len(chunks)
        r = alice_client.post(
            f"/api/projects/upload-sessions/{sid}/complete", headers=csrf(alice_client)
        )
        assert r.status_code == 201, r.text

    def test_concurrent_same_chunk_writes_are_atomic(self, env, alice_client):
        """并发重传同一 (file, chunk)：落盘内容必为某一完整分片（无撕裂），
        且不留 .tmp 残留（审查修复回归：原子替换写入）。"""
        payloads = [os.urandom(CHUNK) for _ in range(8)]
        _, sid = _init_dir(alice_client, {"blob.bin": b"x" * CHUNK})
        uid = env["alice"]["id"]
        with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
            list(
                pool.map(
                    lambda data: upload_sessions.store_part(
                        uid, sid, 0, 0, data, _md5(data)
                    ),
                    payloads,
                )
            )
        part_file = _session_dir(sid) / "parts" / "0-0.part"
        content = part_file.read_bytes()
        assert content in payloads  # 撕裂内容不可能恰好等于任一原始分片
        assert not any(
            p.name.endswith(".tmp") for p in (_session_dir(sid) / "parts").iterdir()
        )


# ── 断点续传 ─────────────────────────────────────────────────────
class TestResume:
    def test_partial_status_then_resume_complete(self, env, alice_client):
        """多文件部分上传 → status 位图正确 → 只补缺块 → complete 成功。"""
        payload = os.urandom(2 * CHUNK + 12345)
        files = {
            "big.bin": payload,
            "docs/readme.tex": b"readme",
            "empty.lock": b"",  # 空文件：0 分块，无需上传
        }
        _, sid = _init_dir(alice_client, files)
        # 只传第一个文件的第一个块和第二个文件
        r = _put_part(alice_client, sid, 0, 0, payload[:CHUNK])
        assert r.status_code == 200
        r = _put_part(alice_client, sid, 1, 0, b"readme")
        assert r.status_code == 200

        st = alice_client.get(f"/api/projects/upload-sessions/{sid}").json()
        by_index = {f["index"]: f for f in st["files"]}
        assert by_index[0]["bitmap"] == [True, False, False]
        assert by_index[0]["received_count"] == 1
        assert by_index[1]["bitmap"] == [True]
        assert by_index[2]["bitmap"] == [] and by_index[2]["chunks_total"] == 0

        # 补缺：仅剩 file0 的第 1、2 块
        for ci in (1, 2):
            piece = payload[ci * CHUNK : (ci + 1) * CHUNK]
            r = _put_part(alice_client, sid, 0, ci, piece)
            assert r.status_code == 200

        r = alice_client.post(
            f"/api/projects/upload-sessions/{sid}/complete", headers=csrf(alice_client)
        )
        assert r.status_code == 201, r.text
        project = r.json()["project"]
        root = userspace.project_dir(env["alice"]["id"], project["project_id"])
        assert (root / "big.bin").read_bytes() == payload
        assert (root / "docs" / "readme.tex").read_text(encoding="utf-8") == "readme"
        assert (root / "empty.lock").read_bytes() == b""
        # 成功后 staging 与会话行清理
        assert not _session_dir(sid).exists()
        assert (
            db_module.db_query_one("SELECT 1 FROM upload_sessions WHERE id = ?", (sid,))
            is None
        )

    def test_multi_chunk_single_file_zip_flow(self, env, alice_client):
        """zip kind 大包（多块）拼装后走解压链路创建项目。"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as z:
            z.writestr("pkg/main.tex", r"\documentclass{article}")
            z.writestr("pkg/data.bin", os.urandom(2 * CHUNK + 4096))
        payload = buf.getvalue()
        assert len(payload) > 2 * CHUNK  # 确认多分块
        body = {
            "name": "bigzip",
            "kind": "zip",
            "total_bytes": len(payload),
            "files": [_entry("big.zip", payload)],
        }
        r = alice_client.post(
            "/api/projects/upload-sessions", json=body, headers=csrf(alice_client)
        )
        assert r.status_code == 201
        sid = r.json()["session_id"]
        n = (len(payload) + CHUNK - 1) // CHUNK
        for ci in range(n):
            piece = payload[ci * CHUNK : (ci + 1) * CHUNK]
            assert _put_part(alice_client, sid, 0, ci, piece).status_code == 200
        # 中途断点查询一次（位图）
        st = alice_client.get(f"/api/projects/upload-sessions/{sid}").json()
        assert st["files"][0]["received_count"] == n
        r = alice_client.post(
            f"/api/projects/upload-sessions/{sid}/complete",
            headers=csrf(alice_client),
        )
        assert r.status_code == 201, r.text
        proj = r.json()["project"]
        root = userspace.project_dir(env["alice"]["id"], proj["project_id"])
        # 顶层仅一个目录 pkg → 归一化后项目根即 pkg（main.tex 位于项目根）
        assert (root / "main.tex").read_text(
            encoding="utf-8"
        ) == r"\documentclass{article}"
        assert (root / "data.bin").stat().st_size == 2 * CHUNK + 4096


# ── complete 校验失败 ────────────────────────────────────────────
class TestCompleteFailures:
    def test_complete_with_missing_chunk_keeps_session(self, env, alice_client):
        payload = os.urandom(3 * CHUNK)
        _, sid = _init_dir(alice_client, {"blob.bin": payload})
        for ci in (0, 2):  # 缺第 1 块
            piece = payload[ci * CHUNK : (ci + 1) * CHUNK]
            assert _put_part(alice_client, sid, 0, ci, piece).status_code == 200
        r = alice_client.post(
            f"/api/projects/upload-sessions/{sid}/complete", headers=csrf(alice_client)
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "part_missing"
        # 会话保留可续传：状态回到 uploading，补齐后成功
        st = alice_client.get(f"/api/projects/upload-sessions/{sid}").json()
        assert st["status"] == "uploading"
        assert (
            _put_part(alice_client, sid, 0, 1, payload[CHUNK : 2 * CHUNK]).status_code
            == 200
        )
        r = alice_client.post(
            f"/api/projects/upload-sessions/{sid}/complete", headers=csrf(alice_client)
        )
        assert r.status_code == 201, r.text

    def test_complete_md5_full_recheck_rejects_corruption(self, env, alice_client):
        """绕过 API 直接篡改已落盘分片 → complete 全量 MD5 复核拒绝且保留现场。"""
        data = b"original content here"
        _, sid = _init_dir(alice_client, {"a.txt": data})
        assert _put_part(alice_client, sid, 0, 0, data).status_code == 200
        part_file = _session_dir(sid) / "parts" / "0-0.part"
        part_file.write_bytes(b"tampered content!!")
        r = alice_client.post(
            f"/api/projects/upload-sessions/{sid}/complete", headers=csrf(alice_client)
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "part_md5_mismatch"
        assert _session_dir(sid).exists()

    def test_overwrite_chunk_with_wrong_content_fails_complete(self, env, alice_client):
        data = b"final answer"
        _, sid = _init_dir(alice_client, {"a.txt": data})
        assert _put_part(alice_client, sid, 0, 0, data).status_code == 200
        # 幂等覆盖允许重传，但内容若与清单 md5 不符，complete 兜底拦截
        other = b"wrong overwrite"
        r = _put_part(alice_client, sid, 0, 0, other)
        assert r.status_code == 200
        r = alice_client.post(
            f"/api/projects/upload-sessions/{sid}/complete", headers=csrf(alice_client)
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "part_md5_mismatch"

    def test_complete_quota_second_check_rejects_whole_package(self, env, alice_client):
        """init 后占用配额（竞争窗口）→ complete 二次复核 413 且 staging 清干净。"""
        payload = _zip_bytes({"p/main.tex": "x" * (64 * 1024)})
        _, sid = _init_zip(alice_client, payload)
        assert _put_part(alice_client, sid, 0, 0, payload).status_code == 200
        # 竞争：init 与 complete 之间用掉几乎全部配额，仅留 32KB（< 解压产物）
        _seed_project_row(env["alice"]["id"], "hog", 500 * 1024 * 1024 - 32 * 1024)
        projects_before = set(
            r["id"]
            for r in db_module.db_query_all(
                "SELECT id FROM projects WHERE owner_id = ?", (env["alice"]["id"],)
            )
        )
        r = alice_client.post(
            f"/api/projects/upload-sessions/{sid}/complete", headers=csrf(alice_client)
        )
        assert r.status_code == 413
        assert r.json()["detail"]["error"] == "quota_exceeded"
        # 整包拒绝：staging 目录与会话行清干净，无新项目目录产生
        assert not _session_dir(sid).exists()
        assert (
            db_module.db_query_one("SELECT 1 FROM upload_sessions WHERE id = ?", (sid,))
            is None
        )
        projects_after = set(
            r["id"]
            for r in db_module.db_query_all(
                "SELECT id FROM projects WHERE owner_id = ?", (env["alice"]["id"],)
            )
        )
        assert projects_after == projects_before

    def test_complete_twice_returns_not_found_after_success(self, env, alice_client):
        payload = _zip_bytes({"p/main.tex": "x"})
        _, sid = _init_zip(alice_client, payload)
        _put_part(alice_client, sid, 0, 0, payload)
        first = alice_client.post(
            f"/api/projects/upload-sessions/{sid}/complete", headers=csrf(alice_client)
        )
        assert first.status_code == 201
        second = alice_client.post(
            f"/api/projects/upload-sessions/{sid}/complete", headers=csrf(alice_client)
        )
        assert second.status_code == 404

    def test_part_rejected_while_assembling(self, env, alice_client):
        payload = _zip_bytes({"p/main.tex": "x"})
        _, sid = _init_zip(alice_client, payload)
        _put_part(alice_client, sid, 0, 0, payload)
        db_module.db_execute(
            "UPDATE upload_sessions SET status = 'assembling' WHERE id = ?", (sid,)
        )
        r = _put_part(alice_client, sid, 0, 0, payload)
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_state"

    def test_complete_intercepted_while_assembling(self, env, alice_client):
        """并发重复提交拦截：assembling 态的 complete 被条件 UPDATE 拒绝。"""
        payload = _zip_bytes({"p/main.tex": "x"})
        _, sid = _init_zip(alice_client, payload)
        _put_part(alice_client, sid, 0, 0, payload)
        # 模拟另一 complete 已抢先进 assembling
        db_module.db_execute(
            "UPDATE upload_sessions SET status = 'assembling' WHERE id = ?", (sid,)
        )
        r = alice_client.post(
            f"/api/projects/upload-sessions/{sid}/complete", headers=csrf(alice_client)
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_state"

    def test_complete_part_vanished_maps_part_missing(self, env, alice_client):
        """分片在校验中途消失（并发 abort / TTL 清场竞态）→ 400 part_missing
        且会话保留可续传，而非裸 OSError 逃逸成 500（审查修复回归）。"""
        payload = os.urandom(2 * CHUNK)
        _, sid = _init_dir(alice_client, {"blob.bin": payload})
        for ci in (0, 1):
            piece = payload[ci * CHUNK : (ci + 1) * CHUNK]
            assert _put_part(alice_client, sid, 0, ci, piece).status_code == 200
        (_session_dir(sid) / "parts" / "0-1.part").unlink()
        r = alice_client.post(
            f"/api/projects/upload-sessions/{sid}/complete", headers=csrf(alice_client)
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "part_missing"
        st = alice_client.get(f"/api/projects/upload-sessions/{sid}").json()
        assert st["status"] == "uploading"  # 现场保留可续传
        # 补回缺失分片后正常完成
        assert (
            _put_part(alice_client, sid, 0, 1, payload[CHUNK : 2 * CHUNK]).status_code
            == 200
        )
        r = alice_client.post(
            f"/api/projects/upload-sessions/{sid}/complete", headers=csrf(alice_client)
        )
        assert r.status_code == 201, r.text


# ── abort / 归属 ─────────────────────────────────────────────────
class TestAbortAndOwnership:
    def test_abort_cleans_row_and_staging(self, env, alice_client):
        _, sid = _init_dir(alice_client, {"a.txt": b"data"})
        assert _session_dir(sid).exists()
        r = alice_client.delete(
            f"/api/projects/upload-sessions/{sid}", headers=csrf(alice_client)
        )
        assert r.status_code == 200
        assert not _session_dir(sid).exists()
        assert (
            db_module.db_query_one("SELECT 1 FROM upload_sessions WHERE id = ?", (sid,))
            is None
        )
        # 中止后访问 404
        r = alice_client.get(f"/api/projects/upload-sessions/{sid}")
        assert r.status_code == 404
        assert r.json()["detail"]["error"] == "upload_session_not_found"

    def test_other_user_session_is_404(self, env, alice_client, bob_client):
        """bob 操作 alice 的 sid 一律 404（不泄露存在性）。"""
        payload = _zip_bytes({"p/main.tex": "x"})
        _, sid = _init_zip(alice_client, payload)
        assert _put_part(bob_client, sid, 0, 0, payload).status_code == 404
        assert bob_client.get(f"/api/projects/upload-sessions/{sid}").status_code == 404
        assert (
            bob_client.post(
                f"/api/projects/upload-sessions/{sid}/complete",
                headers=csrf(bob_client),
            ).status_code
            == 404
        )
        assert (
            bob_client.delete(
                f"/api/projects/upload-sessions/{sid}", headers=csrf(bob_client)
            ).status_code
            == 404
        )
        # 会话本身不受影响
        assert (
            alice_client.get(f"/api/projects/upload-sessions/{sid}").status_code == 200
        )


# ── TTL 与维护循环 ───────────────────────────────────────────────
def _expire_session(sid: str, hours: int = 48) -> None:
    from datetime import datetime, timedelta, timezone

    old = (
        datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=hours)
    ).isoformat()
    db_module.db_execute(
        "UPDATE upload_sessions SET last_activity_at = ? WHERE id = ?", (old, sid)
    )


class TestTtlAndMaintenance:
    def test_expired_session_access_returns_410_and_purges(self, env, alice_client):
        _, sid = _init_dir(alice_client, {"a.txt": b"data"})
        _put_part(alice_client, sid, 0, 0, b"data")
        _expire_session(sid)
        r = alice_client.get(f"/api/projects/upload-sessions/{sid}")
        assert r.status_code == 410
        assert r.json()["detail"]["error"] == "upload_session_expired"
        assert not _session_dir(sid).exists()
        assert (
            db_module.db_query_one("SELECT 1 FROM upload_sessions WHERE id = ?", (sid,))
            is None
        )

    def test_purge_expired_keeps_fresh(self, env, alice_client):
        _, expired_sid = _init_dir(alice_client, {"a.txt": b"a"})
        _, fresh_sid = _init_dir(alice_client, {"b.txt": b"b"})
        _expire_session(expired_sid)
        removed = upload_sessions.purge_expired_upload_sessions()
        assert removed >= 1
        assert not _session_dir(expired_sid).exists()
        assert _session_dir(fresh_sid).exists()
        assert (
            db_module.db_query_one(
                "SELECT 1 FROM upload_sessions WHERE id = ?", (fresh_sid,)
            )
            is not None
        )

    def test_orphan_session_dir_swept(self, env):
        """带标记但无数据行的孤儿目录由维护清扫回收。"""
        orphan = userspace.get_staging_root() / uuid.uuid4().hex
        orphan.mkdir(parents=True)
        (orphan / upload_sessions.SESSION_MARKER_FILENAME).write_text(
            "{}", encoding="utf-8"
        )
        removed = upload_sessions.purge_expired_upload_sessions()
        assert removed >= 1
        assert not orphan.exists()

    def test_clean_staging_skips_active_session_dirs(self, env, alice_client):
        """通用 mtime 兜底清理跳过会话目录（其 TTL 可能长于暂存保留期）。"""
        _, sid = _init_dir(alice_client, {"a.txt": b"data"})
        stale_plain = userspace.get_staging_root() / "plain-stale"
        stale_plain.mkdir(parents=True)
        old_ts = 0
        os.utime(_session_dir(sid), (old_ts, old_ts))
        os.utime(stale_plain, (old_ts, old_ts))
        cleaned = quota.clean_staging()
        assert cleaned >= 1
        assert not stale_plain.exists()
        assert _session_dir(sid).exists()  # 活跃会话目录不被误删

    def test_touch_renews_last_activity(self, env, alice_client):
        _, sid = _init_dir(alice_client, {"a.txt": b"data"})
        before = db_module.db_query_one(
            "SELECT last_activity_at FROM upload_sessions WHERE id = ?", (sid,)
        )["last_activity_at"]
        alice_client.get(f"/api/projects/upload-sessions/{sid}")
        after = db_module.db_query_one(
            "SELECT last_activity_at FROM upload_sessions WHERE id = ?", (sid,)
        )["last_activity_at"]
        assert after >= before
