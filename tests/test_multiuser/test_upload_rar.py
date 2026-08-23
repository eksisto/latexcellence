"""多用户模式 v2 P2：rar 上传解压管线测试。

覆盖（方案 §7 / §14 完成标准）：
    - 正常解压：顶层单目录布局 / 直接内容布局 / 内容与计数
    - 加密包拒绝（文件头加密标志，预扫确定性拦截）
    - 分卷包拒绝（多卷集第一卷形态，预扫确定性拦截）
    - 服务/子块（恢复记录 RR、NTFS 子块）带数据区的合法包不被预扫
      误拒：数据区按块头声明的长度整段跳过，含固定种子 Monte Carlo
      与「数据区起始即受保护标志模式」的决定性回归
    - 缺失 unar 二进制时的明确报错（unar_missing，不依赖环境装包）
    - 损坏包 / 空产物（多卷集静默零产物兜底）拒绝
    - GBK 文件名样例（unar 字符集探测与 zip 管线存在已知差异，
      仅断言可解出唯一文件且内容一致）
    - 解压限时接线（超时不启动进程）、监视线程超限击杀（fake 进程注入）
    - API 集成：POST /api/projects 上传 .rar 创建项目

真实 unar 解压用例在无 unar 环境自动 skip；预扫 / 缺二进制 / 监视线程
用例不依赖外部二进制，任何环境都执行。
"""

import random
import shutil
import struct
import zlib
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

# RAR4 块类型（与 upload.py 常量同源，测试侧独立声明避免依赖私有拼写）
FILE_HEAD = 0x74
SUB_HEAD = 0x78
SERVICE_HEAD = 0x7A
ENDARC_HEAD = 0x7B
LONG_BLOCK = 0x8000  # SUB/SERVICE 块携带 ADD_SIZE(DataSize) 字段的标志
LHD_PASSWORD = 0x0004
LHD_LARGE = 0x0100


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


def _crc16(body: bytes) -> bytes:
    """RAR 头部 CRC：从 TYPE 字节到头部末尾的 CRC32 低 16 位。"""
    return struct.pack("<H", zlib.crc32(body) & 0xFFFF)


_FTIME = (2026 - 1980) << 25 | 8 << 21 | 22 << 16


def _file_block(
    name: bytes,
    content: bytes,
    *,
    flags: int = 0,
    btype: int = FILE_HEAD,
) -> bytes:
    """构造文件头块 + 数据段；btype 可换为 SERVICE_HEAD(0x7A)——
    RAR4 服务块与文件头共用同一布局（unrar 即如此解析），名字如 b"RR"。"""
    hsize = 32 + len(name)
    body = (
        struct.pack(
            "<BHHIIBIIBBHI",
            btype,
            flags,
            hsize,
            len(content),
            len(content),
            3,  # HOST_OS Unix
            zlib.crc32(content) & 0xFFFFFFFF,
            _FTIME,
            20,  # UNP_VER
            0x30,  # METHOD_STORE
            len(name),
            0o644,
        )
        + name
    )
    return _crc16(body) + body + content


def _sub_head_block(data: bytes, tag: bytes = b"") -> bytes:
    """构造 SUB_HEAD(0x78) 子块（NTFS 流等）：块头固定段之后的第一个
    u32 为数据区长度（DataSize），随后是类型相关附加段与数据区本身。"""
    body = struct.pack("<BHH", SUB_HEAD, LONG_BLOCK, 11 + len(tag))
    body += struct.pack("<I", len(data)) + tag
    return _crc16(body) + body + data


def _assemble(blocks: list[bytes], main_flags: int = 0) -> bytes:
    """主头 + 若干已拼装块 + ENDARC 组装为完整归档字节流。"""
    main_body = struct.pack("<BHH", 0x73, main_flags, 13) + struct.pack("<HI", 0, 0)
    out = bytearray(upload._RAR4_MARKER + _crc16(main_body) + main_body)
    out += b"".join(blocks)
    end_body = struct.pack("<BHH", ENDARC_HEAD, 0x4000, 7)
    return bytes(out + _crc16(end_body) + end_body)


def _rar4(main_flags: int = 0, entries: list[tuple[bytes, bytes]] = ()) -> bytes:
    """纯字节构造最小 RAR4 store 归档（与 tools/rar_fixtures/make_fixtures.py
    同构，供预扫/兜底分支的 hermetic 单测使用，不依赖环境装包）。"""
    return _assemble(
        [_file_block(name, content) for name, content in entries],
        main_flags=main_flags,
    )


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

    # ── 服务/子块数据区误拒回归（Required 缺陷）──────────────────
    # 老预扫只对 FILE_HEAD 跳过数据区，SUB/SERVICE 带数据区时 pos 落进
    # 高熵恢复数据随机游走，撞上「btype=0x74 且 flags&0x0007≠0」的随机
    # 字节即误报加密（60KB RR 实测约 2.5% 误拒率）。

    def test_service_data_area_not_misrejected(self, tmp_path):
        """SERVICE(0x7A) 恢复记录带高熵数据区：预扫必须整段跳过不误拒。"""
        payload = random.Random(318).randbytes(60_000)  # 318 号种子老实现必误拒
        p = tmp_path / "rr.rar"
        p.write_bytes(
            _assemble(
                [
                    _file_block(b"RR", payload, btype=SERVICE_HEAD),
                    _file_block(b"a.tex", b"x"),
                ]
            )
        )
        upload._reject_protected_rar(p)  # 不抛错

    def test_sub_head_data_area_not_misrejected(self, tmp_path):
        """SUB_HEAD(0x78)（NTFS 流等）带数据区：同样整段跳过不误拒。"""
        p = tmp_path / "ntfs.rar"
        p.write_bytes(
            _assemble(
                [
                    _sub_head_block(random.Random(1).randbytes(4096)),
                    _file_block(b"a.tex", b"x"),
                ]
            )
        )
        upload._reject_protected_rar(p)  # 不抛错

    def test_service_data_starting_with_protected_pattern_passes(self, tmp_path):
        """决定性回归：数据区起始即放置会被误读为「加密 FILE_HEAD」的字节
        序列——老实现从数据区起点盲走必然误拒，新语义下数据区内容对预扫
        不可见，任何内容都不得触发拒绝。"""
        mine = struct.pack("<HBHH", 0, FILE_HEAD, LHD_PASSWORD, 7)
        for btype, make_block in (
            (SERVICE_HEAD, lambda data: _file_block(b"RR", data, btype=btype)),
            (SUB_HEAD, _sub_head_block),
        ):
            p = tmp_path / f"mine_{btype:#04x}.rar"
            p.write_bytes(
                _assemble([make_block(mine * 64), _file_block(b"a.tex", b"x")])
            )
            upload._reject_protected_rar(p)  # 不抛错

    def test_random_rr_payloads_never_rejected(self, tmp_path):
        """Monte Carlo 回归（固定种子可重复）：多组 60KB 随机恢复记录 +
        正常文件，全部放行。"""
        p = tmp_path / "mc.rar"
        for seed in range(24):
            rng = random.Random(seed)
            p.write_bytes(
                _assemble(
                    [
                        _file_block(b"RR", rng.randbytes(60_000), btype=SERVICE_HEAD),
                        _file_block(b"a.tex", b"x"),
                    ]
                )
            )
            upload._reject_protected_rar(p)

    def test_unknown_block_type_stops_prescan_safely(self, tmp_path):
        """未知块类型（如 COMM_HEAD 0x75）：直接安全返回放行；其后即使
        紧跟受保护标志块也不得再被扫描到（不在字节流上盲走）。"""
        unknown_body = struct.pack("<BHH", 0x75, 0, 7)
        p = tmp_path / "comm.rar"
        p.write_bytes(
            _assemble(
                [
                    _sub_head_block(b"\x00" * 16),
                    _crc16(unknown_body) + unknown_body,
                    _file_block(b"secret.tex", b"x", flags=LHD_PASSWORD),
                ]
            )
        )
        upload._reject_protected_rar(p)  # 不抛错；若继续走必命中拒绝分支

    def test_truncated_data_size_safe_return(self, tmp_path):
        """服务头在 DataSize 字段中途截断：长度异常安全返回放行。"""
        partial = struct.pack("<BHH", SERVICE_HEAD, LONG_BLOCK, 11) + b"\x01\x02"
        p = tmp_path / "trunc.rar"
        p.write_bytes(_assemble([])[:-7] + _crc16(partial) + partial)
        upload._reject_protected_rar(p)  # 不抛错

    def test_file_large_flag_safe_return(self, tmp_path):
        """LHD_LARGE（数据区超 u32 模型）：按长度异常安全放行，
        由 unar 实际解压与解压后复核兜底。"""
        p = tmp_path / "large.rar"
        p.write_bytes(_assemble([_file_block(b"big.tex", b"x", flags=LHD_LARGE)]))
        upload._reject_protected_rar(p)  # 不抛错

    def test_fixture_archives_with_service_data_pass(self):
        """入库 fixture：带恢复记录 / NTFS 子块的合法包不被预扫误拒。"""
        for name in ("service_rr.rar", "subhead_ntfs.rar"):
            upload._reject_protected_rar(FIXTURES / name)

    def test_block_cap_bounds_prescan(self, tmp_path):
        """超小块垃圾包：头链遍历受 max_blocks 限制，快速拒绝而非空转。

        用零数据 SUB 块拉长头链（未知块类型按新语义直接安全返回，
        不再参与遍历计数，无法用于撑爆上限）。"""

        def _zero_sub() -> bytes:
            body = struct.pack("<BHH", SUB_HEAD, LONG_BLOCK, 11)
            body += struct.pack("<I", 0)
            return _crc16(body) + body

        p = tmp_path / "junk.rar"
        p.write_bytes(_assemble([])[:-7] + _zero_sub() * 4096)
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

    def test_archive_with_service_data_extracts(self, extract_dir):
        """带恢复记录 / NTFS 子块数据区的合法包端到端：预扫放行后
        unar 忽略服务块并正常解出文件（缺陷一的功能性验收）。"""
        res = upload.extract_archive(
            FIXTURES / "service_rr.rar", extract_dir, _limits()
        )
        assert (res.root / "main.tex").read_text(
            encoding="utf-8"
        ) == "\\documentclass{article}\nHello\n"


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

    def test_upload_rar_with_service_data_creates_project(self, env, alice_client):
        """带恢复记录的合法 rar 经 API 上传：不再被预扫功能性误拒。"""
        from tests.test_multiuser.conftest import csrf

        payload = (FIXTURES / "service_rr.rar").read_bytes()
        r = alice_client.post(
            "/api/projects",
            data={"name": "RarRR"},
            files={"archive": ("rr.rar", payload, "application/vnd.rar")},
            headers=csrf(alice_client),
        )
        assert r.status_code == 201, r.text
        proj = r.json()["project"]
        root = userspace.project_dir(env["alice"]["id"], proj["project_id"])
        assert (root / "main.tex").exists()

    def test_upload_unsupported_suffix_rejected(self, env, alice_client):
        from tests.test_multiuser.conftest import csrf

        r = alice_client.post(
            "/api/projects",
            files={"archive": ("x.tar", b"xx", "application/x-tar")},
            headers=csrf(alice_client),
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_archive"
