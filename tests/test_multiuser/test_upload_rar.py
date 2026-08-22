"""多用户模式 v2 P2：rar 上传解压管线测试。

覆盖（方案 §7 / §14 完成标准）：
    - 正常解压：顶层单目录布局 / 直接内容布局 / 内容与计数
    - 加密包拒绝（文件头加密标志，预扫确定性拦截）
    - 分卷包拒绝（多卷集第一卷形态，预扫确定性拦截）
    - 缺失 unar 二进制时的明确报错（unar_missing，不依赖环境装包）
    - 损坏包 / 空产物（多卷集静默零产物兜底）拒绝
    - GBK 文件名样例（unar 字符集探测与 zip 管线存在已知差异，
      仅断言可解出唯一文件且内容一致）
    - 解压限时接线（超时不启动进程）、监视线程超限击杀（fake 进程注入）
    - API 集成：POST /api/projects 上传 .rar 创建项目

真实 unar 解压用例在无 unar 环境自动 skip；预扫 / 缺二进制 / 监视线程
用例不依赖外部二进制，任何环境都执行。
"""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.multiuser import upload
from backend.core.multiuser import userspace
from tests.test_multiuser.test_projects_api import (
    PASSWORD,
    build_mu_projects_app,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rar"

UNAR_AVAILABLE = shutil.which("unar") is not None

requires_unar = pytest.mark.skipif(not UNAR_AVAILABLE, reason="unar 工具不可用")


def _limits(**overrides) -> upload.UploadLimits:
    base = dict(
        max_archive_bytes=1024 * 1024,
        max_files=100,
        max_file_size_bytes=1024 * 1024,
        max_compression_ratio=1000,
        remaining_quota_bytes=10 * 1024 * 1024,
        timeout_seconds=60,
    )
    base.update(overrides)
    return upload.UploadLimits(**base)


def _rar4(main_flags: int = 0, entries: list[tuple[bytes, bytes]] = ()) -> bytes:
    """纯字节构造最小 RAR4 store 归档（与 tools/rar_fixtures/make_fixtures.py
    同构，供预扫/兜底分支的 hermetic 单测使用，不依赖环境装包）。"""
    import struct as _struct
    import zlib as _zlib

    def _crc16(body: bytes) -> bytes:
        return _struct.pack("<H", _zlib.crc32(body) & 0xFFFF)

    ftime = (2026 - 1980) << 25 | 8 << 21 | 22 << 16
    main_body = _struct.pack("<BHH", 0x73, main_flags, 13) + _struct.pack("<HI", 0, 0)
    out = bytearray(upload._RAR4_MARKER + _crc16(main_body) + main_body)
    for name, content in entries:
        hsize = 32 + len(name)
        body = (
            _struct.pack(
                "<BHHIIBIIBBHI",
                0x74,
                0,
                hsize,
                len(content),
                len(content),
                3,  # HOST_OS Unix
                _zlib.crc32(content) & 0xFFFFFFFF,
                ftime,
                20,  # UNP_VER
                0x30,  # METHOD_STORE
                len(name),
                0o644,
            )
            + name
        )
        out += _crc16(body) + body + content
    end_body = _struct.pack("<BHH", 0x7B, 0x4000, 7)
    out += _crc16(end_body) + end_body
    return bytes(out)


@pytest.fixture
def extract_dir(tmp_path) -> Path:
    d = tmp_path / "extracted"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── 预扫（不依赖 unar，任何环境执行）──────────────────────────────
class TestRar4Prescan:
    def test_encrypted_flag_rejected(self, extract_dir):
        with pytest.raises(upload.ArchiveError) as ei:
            upload._reject_protected_rar(FIXTURES / "encrypted.rar")
        assert ei.value.code == "archive_unsafe"

    def test_volume_first_part_rejected(self, extract_dir):
        with pytest.raises(upload.ArchiveError) as ei:
            upload._reject_protected_rar(FIXTURES / "volume.part1.rar")
        assert ei.value.code == "archive_unsafe"

    def test_normal_archives_pass(self):
        # 正常包不抛错（含子目录与 GBK 名字样例）
        for name in ("layout_flat.rar", "layout_top_dir.rar", "gbk_name.rar"):
            upload._reject_protected_rar(FIXTURES / name)

    def test_main_flag_recovery_record_allowed(self, tmp_path):
        """主头 0x0040 是 MHD_PROTECT（恢复记录），合法包常见，不得误拒。"""
        p = tmp_path / "rr.rar"
        p.write_bytes(_rar4(main_flags=0x0040, entries=[(b"a.tex", b"x")]))
        upload._reject_protected_rar(p)  # 不抛错

    def test_main_flag_header_encrypted_rejected(self, tmp_path):
        """主头 0x0080 是 MHD_PASSWORD（整包头加密），确定性拒绝。"""
        p = tmp_path / "enchdr.rar"
        p.write_bytes(_rar4(main_flags=0x0080, entries=[(b"a.tex", b"x")]))
        with pytest.raises(upload.ArchiveError) as ei:
            upload._reject_protected_rar(p)
        assert ei.value.code == "archive_unsafe"

    def test_main_flag_volume_bit_rejected(self, tmp_path):
        """主头 MHD_VOLUME 位：多卷集合确定性拒绝，不依赖文件级 split 标志。"""
        p = tmp_path / "vol.rar"
        p.write_bytes(_rar4(main_flags=0x0001, entries=[(b"a.tex", b"x")]))
        with pytest.raises(upload.ArchiveError) as ei:
            upload._reject_protected_rar(p)
        assert ei.value.code == "archive_unsafe"

    def test_rar5_marker_skipped(self, tmp_path):
        # RAR5 归档不做 RAR4 预扫（由 unar 输出匹配与零产物兜底）
        p = tmp_path / "r5.rar"
        p.write_bytes(upload._RAR5_MARKER + b"\x00" * 32)
        upload._reject_protected_rar(p)

    def test_garbage_bytes_skipped(self, tmp_path):
        p = tmp_path / "g.rar"
        p.write_bytes(b"Rar!\x1a\x07\x00" + b"\xff" * 64)
        upload._reject_protected_rar(p)  # 结构异常静默返回，交由 unar 判定

    def test_unreadable_file_rejected(self, tmp_path):
        p = tmp_path / "missing.rar"
        with pytest.raises(upload.ArchiveError) as ei:
            upload._reject_protected_rar(p)
        assert ei.value.code == "invalid_archive"

    def test_block_cap_bounds_prescan(self, tmp_path):
        """超小块垃圾包：头链遍历受 max_blocks 限制，快速拒绝而非空转。"""
        import struct as _struct
        import zlib as _zlib

        body = _struct.pack("<BHH", 0x73, 0, 13) + _struct.pack("<HI", 0, 0)
        main = _struct.pack("<H", _zlib.crc32(body) & 0xFFFF) + body
        # 无数个最小非文件块（type 非 FILE/ENDARC、hsize=7），pos 每次仅前进 7
        junk_block = _struct.pack("<HBHH", 0, 0x76, 0, 7)
        p = tmp_path / "junk.rar"
        p.write_bytes(upload._RAR4_MARKER + main + junk_block * 4096)
        with pytest.raises(upload.ArchiveError) as ei:
            upload._reject_protected_rar(p, max_blocks=100)
        assert ei.value.code == "invalid_archive"


# ── 缺二进制与限时接线（不依赖 unar）─────────────────────────────
class TestWiring:
    def test_missing_binary_reports_unar_missing(self, extract_dir, monkeypatch):
        monkeypatch.setattr(upload.shutil, "which", lambda name: None)
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(FIXTURES / "layout_flat.rar", extract_dir, _limits())
        assert ei.value.code == "unar_missing"

    def test_expired_deadline_skips_process_spawn(self, extract_dir, monkeypatch):
        """timeout_seconds=0：deadline 已过，未启动进程即拒绝。"""
        monkeypatch.setattr(upload.shutil, "which", lambda name: "/fake/unar")

        def _fail_popen(*args, **kwargs):
            raise AssertionError("Popen must not be called after deadline")

        monkeypatch.setattr(upload.subprocess, "Popen", _fail_popen)
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(
                FIXTURES / "layout_flat.rar", extract_dir, _limits(timeout_seconds=0)
            )
        assert ei.value.code == "invalid_archive"

    def test_zero_product_guard_hermetic(self, extract_dir, monkeypatch):
        """exit=0 且零产物（多卷集静默形态）：不依赖真实 unar 的兜底用例。"""
        monkeypatch.setattr(upload.shutil, "which", lambda name: "/fake/unar")

        class _FakePopen:
            def __init__(self, *args, **kwargs):
                self.pid = 4242
                self.returncode = 0

            def communicate(self, timeout=None):
                return b"", b""

        monkeypatch.setattr(upload.subprocess, "Popen", _FakePopen)
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(FIXTURES / "layout_flat.rar", extract_dir, _limits())
        assert ei.value.code == "invalid_archive"

    def test_case_conflict_rejected_hermetic(self, extract_dir, monkeypatch):
        """解压产物大小写不敏感重名：后置扫描拒绝（不依赖真实 unar）。"""
        monkeypatch.setattr(upload.shutil, "which", lambda name: "/fake/unar")
        (extract_dir / "README.tex").write_text("A", encoding="utf-8")
        (extract_dir / "readme.tex").write_text("B", encoding="utf-8")

        class _FakePopen:
            def __init__(self, *args, **kwargs):
                self.pid = 4242
                self.returncode = 0

            def communicate(self, timeout=None):
                return b"", b""

        monkeypatch.setattr(upload.subprocess, "Popen", _FakePopen)
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(FIXTURES / "layout_flat.rar", extract_dir, _limits())
        assert ei.value.code == "archive_unsafe"


# ── 监视线程（fake 进程注入，不依赖 unar）────────────────────────
class _FakeProc:
    def __init__(self):
        self.pid = 99999
        self.killed = False


class TestRarWatch:
    def test_quota_breach_kills_process_group(self, tmp_path, monkeypatch):
        import threading

        # 预置已写盘量超过剩余配额的解压目录（模拟解压中途越限）
        (tmp_path / "extracted").mkdir()
        (tmp_path / "extracted" / "bomb.bin").write_bytes(b"\0" * 1024)

        kills: list[int] = []
        monkeypatch.setattr(upload.os, "killpg", lambda pgid, sig: kills.append(pgid))
        monkeypatch.setattr(upload.os, "getpgid", lambda pid: pid)

        limits = _limits(remaining_quota_bytes=512, timeout_seconds=999)
        proc = _FakeProc()
        stop = threading.Event()
        breach: list[str] = []
        watcher = upload._start_rar_watch(
            proc,
            tmp_path / "extracted",
            limits,
            upload._deadline(limits),
            stop,
            breach,
        )
        watcher.join(timeout=5)
        assert not watcher.is_alive()
        assert breach == ["quota_exceeded"]
        assert kills == [proc.pid]
        stop.set()

    def test_deadline_breach_reports_timeout(self, tmp_path, monkeypatch):
        import threading
        import time

        (tmp_path / "extracted").mkdir()
        monkeypatch.setattr(upload.os, "killpg", lambda pgid, sig: None)
        monkeypatch.setattr(upload.os, "getpgid", lambda pid: pid)

        limits = _limits(timeout_seconds=999)
        proc = _FakeProc()
        stop = threading.Event()
        breach: list[str] = []
        watcher = upload._start_rar_watch(
            proc,
            tmp_path / "extracted",
            limits,
            time.monotonic() - 1,  # 已过期
            stop,
            breach,
        )
        watcher.join(timeout=5)
        assert breach == ["invalid_archive"]
        stop.set()

    def test_normal_state_not_interrupted(self, tmp_path, monkeypatch):
        import threading

        (tmp_path / "extracted").mkdir()
        (tmp_path / "extracted" / "a.tex").write_bytes(b"x" * 16)
        kills: list[int] = []
        monkeypatch.setattr(upload.os, "killpg", lambda pgid, sig: kills.append(pgid))
        monkeypatch.setattr(upload.os, "getpgid", lambda pid: pid)

        limits = _limits(remaining_quota_bytes=1 << 20, timeout_seconds=60)
        proc = _FakeProc()
        stop = threading.Event()
        breach: list[str] = []
        watcher = upload._start_rar_watch(
            proc,
            tmp_path / "extracted",
            limits,
            upload._deadline(limits),
            stop,
            breach,
        )
        # 一个采样周期内未越限 → 不击杀、不记 breach
        watcher.join(timeout=2.5)
        assert watcher.is_alive()
        assert kills == []
        assert breach == []
        stop.set()
        watcher.join(timeout=5)


# ── 真实解压（需要 unar）────────────────────────────────────────
@requires_unar
class TestNormalExtract:
    def test_top_dir_layout(self, extract_dir):
        res = upload.extract_archive(
            FIXTURES / "layout_top_dir.rar", extract_dir, _limits()
        )
        # 顶层仅一个目录 → 归一化为项目根
        assert res.root == extract_dir / "proj"
        assert (res.root / "main.tex").read_text(
            encoding="utf-8"
        ) == "\\documentclass{article}\nWorld\n"
        assert (res.root / "refs.bib").read_text(encoding="utf-8") == (
            "@misc{x, title={X}}\n"
        )
        assert res.file_count == 2
        assert res.size > 0

    def test_flat_layout(self, extract_dir):
        res = upload.extract_archive(
            FIXTURES / "layout_flat.rar", extract_dir, _limits()
        )
        # 顶层多个条目（main.tex + notes/）→ 解压根即项目根
        assert res.root == extract_dir
        assert (res.root / "main.tex").read_text(
            encoding="utf-8"
        ) == "\\documentclass{article}\nHello\n"
        assert (res.root / "notes" / "readme.txt").read_text(
            encoding="utf-8"
        ) == "flat layout\n"
        assert res.file_count == 2

    def test_gbk_name_extracts_single_file(self, extract_dir):
        """GBK 文件名：unar 字符集探测与 zip 管线可能不一致（已知差异），
        只断言解出唯一文件且内容一致，不断言文件名逐字还原。"""
        res = upload.extract_archive(FIXTURES / "gbk_name.rar", extract_dir, _limits())
        files = [p for p in extract_dir.rglob("*") if p.is_file()]
        assert len(files) == 1
        assert files[0].name.endswith(".txt")
        assert files[0].read_bytes() == b"gbk name\n"
        assert res.file_count == 1


@requires_unar
class TestProtectedReject:
    def test_encrypted_rejected(self, extract_dir):
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(FIXTURES / "encrypted.rar", extract_dir, _limits())
        assert ei.value.code == "archive_unsafe"

    def test_volume_first_part_rejected(self, extract_dir):
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(
                FIXTURES / "volume.part1.rar", extract_dir, _limits()
            )
        assert ei.value.code == "archive_unsafe"


@requires_unar
class TestCorrupt:
    def test_garbage_bytes_rejected(self, extract_dir, tmp_path):
        p = tmp_path / "upload.rar"
        p.write_bytes(b"definitely not a rar archive")
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(p, extract_dir, _limits())
        assert ei.value.code == "invalid_archive"

    def test_empty_archive_rejected(self, extract_dir, tmp_path):
        """合法但零条目的 rar：多卷集静默零产物兜底同样拒绝。"""
        p = tmp_path / "upload.rar"
        p.write_bytes(_rar4())
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(p, extract_dir, _limits())
        assert ei.value.code == "invalid_archive"

    def test_case_conflict_rejected_end_to_end(self, extract_dir, tmp_path):
        """真实 unar 解出大小写重名对：后置扫描拒绝（Linux 上两者共存）。"""
        p = tmp_path / "upload.rar"
        p.write_bytes(_rar4(entries=[(b"README.tex", b"A"), (b"readme.tex", b"B")]))
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(p, extract_dir, _limits())
        assert ei.value.code == "archive_unsafe"


# ── API 集成（需要 unar）────────────────────────────────────────
@requires_unar
class TestApiUpload:
    @pytest.fixture
    def env(self, tmp_path):
        from backend.core.multiuser import auth as auth_core
        from backend.core.multiuser import db as db_module
        from tests.test_multiuser.conftest import (
            _reset_config_state,
            build_mu_env,
        )

        env = build_mu_env(tmp_path)
        env["alice"] = auth_core.create_user(
            "alice", PASSWORD, role="user", status="active"
        )
        yield env
        db_module.close_db()
        _reset_config_state()

    @pytest.fixture
    def alice_client(self, env) -> TestClient:
        client = TestClient(build_mu_projects_app())
        r = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": PASSWORD},
        )
        assert r.status_code == 200, r.text
        return client

    def test_upload_rar_creates_project(self, env, alice_client):
        from tests.test_multiuser.conftest import csrf

        payload = (FIXTURES / "layout_top_dir.rar").read_bytes()
        r = alice_client.post(
            "/api/projects",
            data={"name": "RarProj"},
            files={
                "archive": (
                    "proj.rar",
                    payload,
                    "application/vnd.rar",
                )
            },
            headers=csrf(alice_client),
        )
        assert r.status_code == 201, r.text
        proj = r.json()["project"]
        assert proj["name"] == "RarProj"
        root = userspace.project_dir(env["alice"]["id"], proj["project_id"])
        assert (root / "main.tex").read_text(
            encoding="utf-8"
        ) == "\\documentclass{article}\nWorld\n"
        assert (root / "refs.bib").exists()

    def test_upload_encrypted_rar_rejected(self, env, alice_client):
        from tests.test_multiuser.conftest import csrf

        payload = (FIXTURES / "encrypted.rar").read_bytes()
        r = alice_client.post(
            "/api/projects",
            files={"archive": ("enc.rar", payload, "application/vnd.rar")},
            headers=csrf(alice_client),
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "archive_unsafe"

    def test_upload_unsupported_suffix_rejected(self, env, alice_client):
        from tests.test_multiuser.conftest import csrf

        r = alice_client.post(
            "/api/projects",
            files={"archive": ("x.tar", b"xx", "application/x-tar")},
            headers=csrf(alice_client),
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_archive"
