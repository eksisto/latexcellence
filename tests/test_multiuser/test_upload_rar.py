"""多用户模式 v2 P2 + 回退层 + 部署收口阶段一/二：rar 上传解压管线测试。

覆盖（docs/rar-extractor-fallback.md §六 +
deploy-followups.md §4.1/§4.2 阶段一/二）：
    - 正常解压：顶层单目录布局 / 直接内容布局 / 内容与计数（unar 与
      bsdtar 双工具参数化）
    - 真实压缩样本（rar CLI 生成，压缩流无法伪造）：RAR4 / RAR5 在双
      工具下端到端上传解压，产物与源 .tex 内容逐字节一致（阶段二验收）
    - 真实加密 RAR5（AES-256）拒绝：拒绝发生在解压器失败输出关键词
      匹配，错误码按工具细分（unar→archive_unsafe / bsdtar→invalid_archive）
    - 真实 RAR4 分卷对拒绝：预扫 MHD_VOLUME 确定性拦截，与解压器无关
    - 加密包拒绝（文件头加密标志，预扫确定性拦截）
    - 分卷包拒绝（多卷集第一卷形态，预扫确定性拦截）
    - 服务/子块（恢复记录 RR、NTFS 子块）带数据区的合法包不被预扫
      误拒：数据区按块头声明的长度整段跳过，含固定种子 Monte Carlo
      与「数据区起始即受保护标志模式」的决定性回归
    - 解压器选择层三态：有 unar / 仅 bsdtar / 全缺（rar_extractor_missing，
      仅 local 后端语义）；docker 后端按固定优先序取注册表 builder
    - 解压执行段经 SandboxRunner：ExecSpec 形状断言（命令形状与「不含
      -P」回归、cwd、压缩包只读挂载、timeout 折算、image_kind、无 stdin、
      每用户租约上下文透传），fake runner 不启动任何进程
    - 失败输出关键词映射：encrypt / password / 截断类普通错误分别断言错误码
    - 文件名归一化（bsdtar 分支）：GBK surrogate 名还原 / 改名冲突拒绝 /
      合法 UTF-8 名不动 / 全链解码失败拒绝 / 自底向上改名顺序
    - 缺失二进制时的明确报错（rar_extractor_missing，不依赖环境装包）
    - 损坏包 / 空产物（多卷集静默零产物兜底）拒绝
    - GBK 文件名样例（unar 字符集探测与 zip 管线存在已知差异；bsdtar +
      归一化路径下应精确还原为中文名）
    - 解压限时接线（超时不发起执行）、宿主侧监视任务超配额/超时置位
      cancel_event 的接线（fake runner 模拟取消后非零退出）
    - API 集成：POST /api/projects 上传 .rar 创建项目

真实解压用例按工具参数化（requires_rar_tool），无对应二进制的环境自动
skip；预扫 / 选择层 / 缺二进制 / 监视任务 / 归一化 / ExecSpec 形状用例
不依赖外部二进制与 Docker daemon（fake runner），任何环境都执行；
真容器冒烟见 tests/test_docker/test_rar_sandbox_smoke.py（marker docker）。

入库样本的结构期望由 tools/rar_fixtures/verify_fixtures.py 独立对账；
真实样本再生成步骤见 tools/rar_fixtures/make_fixtures.py 模块 docstring。
"""

import asyncio
import contextlib
import json
import os
import random
import shutil
import struct
import tempfile
import time
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.multiuser import upload, upload_rar, userspace
from backend.utils.sandbox import ExecResult, ExecSpec, SandboxMount
from tests.test_multiuser.test_projects_api import (
    PASSWORD,
    build_mu_projects_app,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rar"

UNAR_AVAILABLE = shutil.which("unar") is not None
BSDTAR_AVAILABLE = shutil.which("bsdtar") is not None

# 真实解压集成用例的双工具参数化：skipif 条件各自独立（docs/rar-extractor-
# fallback.md §六），无对应二进制的环境自动跳过该工具变体
RAR_TOOL_PARAMS = [
    pytest.param(
        "unar",
        id="unar",
        marks=pytest.mark.skipif(not UNAR_AVAILABLE, reason="unar 工具不可用"),
    ),
    pytest.param(
        "bsdtar",
        id="bsdtar",
        marks=pytest.mark.skipif(not BSDTAR_AVAILABLE, reason="bsdtar 工具不可用"),
    ),
]

# 真实压缩样本（compressed_rar4.rar / compressed_rar5.rar，同源 .tex）的
# 期望内容：与 tools/rar_fixtures/verify_fixtures.py 的 CRC32/sha256 指纹
# 互相钉死——源内容一旦漂移，两边校验都会失败
COMPRESSED_TEX = (
    "\\documentclass{article}\n"
    "\\title{Compression Fixture}\n"
    "\\author{LaTeXcellence Test Suite}\n"
    "\\begin{document}\n"
    "\\maketitle\n"
    "This sample paragraph is generated for the rar fixture suite.\n"
    "TeX typesets the same sentence repeatedly to keep the archive small.\n"
    "\\section{Repeat}\n"
    "TeX typesets the same sentence repeatedly to keep the archive small.\n"
    "\\end{document}\n"
)


@pytest.fixture
def force_rar_tool(request, monkeypatch):
    """强制解压器选择层选中参数指定的工具（indirect 参数化）。

    unar 本就是优先项无需干预；bsdtar 变体通过隐藏 unar 的 which 结果
    走回退分支，其余查找保持真实实现（确保 bsdtar 确实存在才不 skip）。"""
    tool = getattr(request, "param", None)
    if tool == "bsdtar":
        real_which = shutil.which

        def _which(name, *args, **kwargs):
            if name == "unar":
                return None
            return real_which(name, *args, **kwargs)

        monkeypatch.setattr(upload_rar.shutil, "which", _which)
    elif tool != "unar":
        raise ValueError(f"unknown rar tool param: {tool!r}")
    return tool


def _patch_which(monkeypatch, tool: str | None) -> None:
    """让选择层恒选中参数指定的工具（fake 二进制路径，不依赖环境装包）；
    tool=None 表示全部缺失。"""
    if tool is None:
        monkeypatch.setattr(upload_rar.shutil, "which", lambda name, *a, **k: None)
        return
    fake = {"unar": "/fake/unar", "bsdtar": "/fake/bsdtar"}[tool]
    monkeypatch.setattr(
        upload_rar.shutil,
        "which",
        lambda name, *args, **kwargs: fake if name == tool else None,
    )


def _raw_path(extract_dir: Path, raw_name: bytes) -> Path:
    """构造 surrogateescape 字节名条目的落盘路径（模拟 libarchive 产物）。"""
    return extract_dir / os.fsdecode(raw_name)


# RAR4 块类型（与 upload_rar.py 常量同源，测试侧独立声明避免依赖私有拼写）
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
    out = bytearray(upload_rar._RAR4_MARKER + _crc16(main_body) + main_body)
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


@pytest.fixture
def raw_name_dir() -> Path:
    """支持原始字节文件名的临时目录（归一化用例专用）。

    仓库可能检出在 NTFS/exFAT 等挂载点上（如 ntfs3），这类文件系统拒绝
    非 UTF-8 原始字节名（EINVAL）；探测失败即跳过，避免环境性误报。"""
    d = Path(tempfile.mkdtemp(prefix="latexcellence-rarnorm-"))
    probe = os.fsencode(d) + b"/\xd6\xd0"
    try:
        fd = os.open(probe, os.O_CREAT | os.O_WRONLY, 0o644)
        os.close(fd)
        os.unlink(probe)
    except OSError:
        shutil.rmtree(d, ignore_errors=True)
        pytest.skip("当前文件系统不支持原始字节文件名")
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ── 预扫（不依赖 unar，任何环境执行）──────────────────────────────
class TestRar4Prescan:
    def test_encrypted_flag_rejected(self, extract_dir):
        with pytest.raises(upload.ArchiveError) as ei:
            upload_rar.reject_protected_rar(FIXTURES / "encrypted.rar")
        assert ei.value.code == "archive_unsafe"

    def test_volume_first_part_rejected(self, extract_dir):
        with pytest.raises(upload.ArchiveError) as ei:
            upload_rar.reject_protected_rar(FIXTURES / "volume.part1.rar")
        assert ei.value.code == "archive_unsafe"

    def test_normal_archives_pass(self):
        # 正常包不抛错（含子目录与 GBK 名字样例）
        for name in ("layout_flat.rar", "layout_top_dir.rar", "gbk_name.rar"):
            upload_rar.reject_protected_rar(FIXTURES / name)

    def test_main_flag_recovery_record_allowed(self, tmp_path):
        """主头 0x0040 是 MHD_PROTECT（恢复记录），合法包常见，不得误拒。"""
        p = tmp_path / "rr.rar"
        p.write_bytes(_rar4(main_flags=0x0040, entries=[(b"a.tex", b"x")]))
        upload_rar.reject_protected_rar(p)  # 不抛错

    def test_main_flag_header_encrypted_rejected(self, tmp_path):
        """主头 0x0080 是 MHD_PASSWORD（整包头加密），确定性拒绝。"""
        p = tmp_path / "enchdr.rar"
        p.write_bytes(_rar4(main_flags=0x0080, entries=[(b"a.tex", b"x")]))
        with pytest.raises(upload.ArchiveError) as ei:
            upload_rar.reject_protected_rar(p)
        assert ei.value.code == "archive_unsafe"

    def test_main_flag_volume_bit_rejected(self, tmp_path):
        """主头 MHD_VOLUME 位：多卷集合确定性拒绝，不依赖文件级 split 标志。"""
        p = tmp_path / "vol.rar"
        p.write_bytes(_rar4(main_flags=0x0001, entries=[(b"a.tex", b"x")]))
        with pytest.raises(upload.ArchiveError) as ei:
            upload_rar.reject_protected_rar(p)
        assert ei.value.code == "archive_unsafe"

    def test_rar5_marker_skipped(self, tmp_path):
        # RAR5 归档不做 RAR4 预扫（由 unar 输出匹配与零产物兜底）
        p = tmp_path / "r5.rar"
        p.write_bytes(upload_rar._RAR5_MARKER + b"\x00" * 32)
        upload_rar.reject_protected_rar(p)

    def test_garbage_bytes_skipped(self, tmp_path):
        p = tmp_path / "g.rar"
        p.write_bytes(b"Rar!\x1a\x07\x00" + b"\xff" * 64)
        upload_rar.reject_protected_rar(p)  # 结构异常静默返回，交由 unar 判定

    def test_unreadable_file_rejected(self, tmp_path):
        p = tmp_path / "missing.rar"
        with pytest.raises(upload.ArchiveError) as ei:
            upload_rar.reject_protected_rar(p)
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
        upload_rar.reject_protected_rar(p)  # 不抛错

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
        upload_rar.reject_protected_rar(p)  # 不抛错

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
            upload_rar.reject_protected_rar(p)  # 不抛错

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
            upload_rar.reject_protected_rar(p)

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
        upload_rar.reject_protected_rar(p)  # 不抛错；若继续走必命中拒绝分支

    def test_truncated_data_size_safe_return(self, tmp_path):
        """服务头在 DataSize 字段中途截断：长度异常安全返回放行。"""
        partial = struct.pack("<BHH", SERVICE_HEAD, LONG_BLOCK, 11) + b"\x01\x02"
        p = tmp_path / "trunc.rar"
        p.write_bytes(_assemble([])[:-7] + _crc16(partial) + partial)
        upload_rar.reject_protected_rar(p)  # 不抛错

    def test_file_large_flag_safe_return(self, tmp_path):
        """LHD_LARGE（数据区超 u32 模型）：按长度异常安全放行，
        由 unar 实际解压与解压后复核兜底。"""
        p = tmp_path / "large.rar"
        p.write_bytes(_assemble([_file_block(b"big.tex", b"x", flags=LHD_LARGE)]))
        upload_rar.reject_protected_rar(p)  # 不抛错

    def test_fixture_archives_with_service_data_pass(self):
        """入库 fixture：带恢复记录 / NTFS 子块的合法包不被预扫误拒。"""
        for name in ("service_rr.rar", "subhead_ntfs.rar"):
            upload_rar.reject_protected_rar(FIXTURES / name)

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
            upload_rar.reject_protected_rar(p, max_blocks=100)
        assert ei.value.code == "invalid_archive"


# ── 解压器选择层与命令构造（fake 进程，不依赖二进制）────────────
def _fake_result(**kwargs) -> ExecResult:
    kwargs.setdefault("returncode", 0)
    kwargs.setdefault("stdout", "")
    kwargs.setdefault("stderr", "")
    kwargs.setdefault("backend", "local")
    return ExecResult(**kwargs)


class _FakeRunner:
    """记录 ExecSpec / cancel_event / user_id 的 fake SandboxRunner。

    hermetic：不启动任何进程、不探测 Docker daemon。result 为直接返回的
    canned 结果；execute_impl 供自定义异步实现（如等待 cancel_event 置位
    后返回非零退出，模拟后端取消路径）。"""

    def __init__(self, *, backend: str = "local", result=None, execute_impl=None):
        self.backend = backend
        self.result = result
        self.execute_impl = execute_impl
        self.specs: list[ExecSpec] = []
        self.cancel_events: list[object] = []
        self.user_ids: list[str | None] = []
        self.execute_calls = 0

    async def resolve_backend(self, kind: str) -> str:
        assert kind == "convert"
        return self.backend

    async def execute(
        self, spec, *, user_id=None, cancel_event=None, lease_exempt=False
    ):
        self.execute_calls += 1
        self.specs.append(spec)
        self.cancel_events.append(cancel_event)
        self.user_ids.append(user_id)
        if self.execute_impl is not None:
            return await self.execute_impl(
                spec, user_id=user_id, cancel_event=cancel_event
            )
        return self.result


def _install_runner(monkeypatch, runner: _FakeRunner) -> _FakeRunner:
    monkeypatch.setattr(upload_rar, "get_sandbox_runner", lambda: runner)
    return runner


class TestExtractorSelection:
    def test_unar_preferred_when_both_available(self, monkeypatch):
        monkeypatch.setattr(
            upload_rar.shutil,
            "which",
            lambda name, *a, **k: (
                f"/fake/{name}" if name in ("unar", "bsdtar") else None
            ),
        )
        assert upload_rar.resolve_rar_extractor() == ("unar", "/fake/unar")

    def test_bsdtar_fallback_when_only_bsdtar(self, monkeypatch):
        monkeypatch.setattr(
            upload_rar.shutil,
            "which",
            lambda name, *a, **k: "/fake/bsdtar" if name == "bsdtar" else None,
        )
        assert upload_rar.resolve_rar_extractor() == ("bsdtar", "/fake/bsdtar")

    def test_both_missing_reports_rar_extractor_missing(self, extract_dir, monkeypatch):
        _patch_which(monkeypatch, None)
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(FIXTURES / "layout_flat.rar", extract_dir, _limits())
        assert ei.value.code == "rar_extractor_missing"


class TestSandboxExecSpec:
    """解压执行段经 SandboxRunner：ExecSpec 形状断言（阶段一验收）。

    fake runner 记录 ExecSpec，不启动任何进程；工具选择层仍走真实
    实现（which 探测按 _patch_which 注入），保证「选择层 → 命令构造 →
    ExecSpec」全链一致。"""

    def _run(self, monkeypatch, extract_dir: Path, tool: str = "bsdtar") -> ExecSpec:
        _patch_which(monkeypatch, tool)
        runner = _install_runner(monkeypatch, _FakeRunner(result=_fake_result()))
        (extract_dir / "main.tex").write_text("x", encoding="utf-8")  # 免零产物兜底
        upload_rar.extract_rar(FIXTURES / "layout_flat.rar", extract_dir, _limits())
        assert runner.execute_calls == 1
        return runner.specs[0]

    def test_bsdtar_command_shape_and_no_insecure_flags(self, extract_dir, monkeypatch):
        """命令形如 `bsdtar -x -f <abs_archive> -C <abs_dir>`；
        回归项：命令不含 `-P`（保留成员名绝对路径语义的开关）。"""
        spec = self._run(monkeypatch, extract_dir)
        cmd = spec.command
        assert cmd[:3] == ["/fake/bsdtar", "-x", "-f"]
        archive_arg, c_flag, dir_arg = cmd[3:]
        assert c_flag == "-C"
        # 归档与目录都用绝对路径
        assert Path(archive_arg).is_absolute()
        assert Path(archive_arg) == (FIXTURES / "layout_flat.rar").resolve()
        assert Path(dir_arg).is_absolute()
        assert Path(dir_arg) == extract_dir.resolve()
        assert "-P" not in cmd

    def test_unar_command_shape(self, extract_dir, monkeypatch):
        """命令形如 `unar -f -q -D -o <dir> -- <archive>`（-D 根归一化语义）。"""
        spec = self._run(monkeypatch, extract_dir, tool="unar")
        cmd = spec.command
        assert cmd[0] == "/fake/unar"
        assert cmd[1:5] == ["-f", "-q", "-D", "-o"]
        assert Path(cmd[5]) == extract_dir.resolve()
        assert cmd[6] == "--"
        assert Path(cmd[7]) == (FIXTURES / "layout_flat.rar").resolve()

    def test_exec_spec_shape(self, extract_dir, monkeypatch):
        """cwd=解压目录、压缩包绝对路径只读挂载、image_kind=convert、
        既有 deadline 折算为 timeout_seconds、argv 无 shell 字符串。"""
        spec = self._run(monkeypatch, extract_dir)
        assert spec.cwd == str(extract_dir.resolve())
        assert spec.image_kind == "convert"
        assert len(spec.mounts) == 1
        mount = spec.mounts[0]
        assert isinstance(mount, SandboxMount)
        assert mount.host_path == str((FIXTURES / "layout_flat.rar").resolve())
        assert mount.read_only is True
        # 60s deadline 折算（运行期耗时占用极少，容差 2s）
        assert 58.0 < spec.timeout_seconds <= 60.0
        # argv 形式：全为非空字符串（禁 shell 字符串由 ExecSpec 模型强制）
        assert spec.command == [str(a) for a in spec.command]
        assert all(spec.command)

    def test_cancel_event_and_user_lease_context(self, extract_dir, monkeypatch):
        """cancel_event 为当次执行的 asyncio.Event；每用户租约上下文经
        current_sandbox_user_id 透传（Q4 方案 A）。"""
        monkeypatch.setattr(upload_rar, "current_sandbox_user_id", lambda: "u-rar")
        _patch_which(monkeypatch, "unar")
        runner = _install_runner(monkeypatch, _FakeRunner(result=_fake_result()))
        (extract_dir / "main.tex").write_text("x", encoding="utf-8")
        upload_rar.extract_rar(FIXTURES / "layout_flat.rar", extract_dir, _limits())
        assert runner.user_ids == ["u-rar"]
        assert isinstance(runner.cancel_events[0], asyncio.Event)

    def test_docker_backend_skips_host_path_probe(self, extract_dir, monkeypatch):
        """docker 后端不探测宿主 PATH（Q3）：宿主全缺解压器也按注册表
        固定优先序选中 unar（裸二进制名交容器 PATH 解析）。"""
        _patch_which(monkeypatch, None)  # 宿主 unar/bsdtar 全缺
        runner = _install_runner(
            monkeypatch,
            _FakeRunner(backend="docker", result=_fake_result(backend="docker")),
        )
        (extract_dir / "main.tex").write_text("x", encoding="utf-8")
        upload_rar.extract_rar(FIXTURES / "layout_flat.rar", extract_dir, _limits())
        assert runner.specs[0].command[0] == "unar"


# ── 失败输出关键词映射（canned ExecResult，不依赖二进制）─────────
class TestFailureKeywordMapping:
    @pytest.mark.parametrize("rar_tool", ["unar", "bsdtar"])
    def test_encrypted_stderr_maps_archive_unsafe(
        self, extract_dir, monkeypatch, rar_tool
    ):
        _patch_which(monkeypatch, rar_tool)
        stderr = {
            "unar": "unar: Cannot extract encrypted.rar: wrong password\n",
            "bsdtar": (
                "bsdtar: Error opening archive: RAR5 AES-256 encrypted "
                "archive, passphrase required\n"
            ),
        }[rar_tool]
        _install_runner(
            monkeypatch, _FakeRunner(result=_fake_result(returncode=1, stderr=stderr))
        )
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(FIXTURES / "layout_flat.rar", extract_dir, _limits())
        assert ei.value.code == "archive_unsafe"

    @pytest.mark.parametrize("rar_tool", ["unar", "bsdtar"])
    def test_generic_failure_maps_invalid_archive(
        self, extract_dir, monkeypatch, rar_tool
    ):
        """截断类普通错误不含保护词 → invalid_archive（RAR5 分卷缺卷在
        bsdtar 下即落此码；安全性不变，仅错误细分度下降）。"""
        _patch_which(monkeypatch, rar_tool)
        stderr = {
            "unar": "unar: Unexpected end of file\n",
            "bsdtar": "bsdtar: Unexpected EOF in archive\n",
        }[rar_tool]
        _install_runner(
            monkeypatch, _FakeRunner(result=_fake_result(returncode=1, stderr=stderr))
        )
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(FIXTURES / "layout_flat.rar", extract_dir, _limits())
        assert ei.value.code == "invalid_archive"


# ── 文件名归一化（bsdtar 分支专用步骤）──────────────────────────
class TestNormalizeRarNames:
    GBK_BYTES = "中文.txt".encode("gbk")  # \xd6\xd0\xce\xc4.txt

    def test_gbk_surrogate_name_restored(self, raw_name_dir):
        raw = _raw_path(raw_name_dir, self.GBK_BYTES)
        raw.write_bytes(b"x")
        upload_rar.normalize_rar_names(raw_name_dir)
        restored = raw_name_dir / "中文.txt"
        assert restored.read_bytes() == b"x"
        assert not os.path.lexists(os.fsencode(raw))
        # 归一化后的名字必须可 JSON 序列化（文件树接口的前提）
        json.dumps(restored.name)

    def test_nested_gbk_names_renamed_bottom_up(self, raw_name_dir):
        """父子目录同为 GBK 名：自底向上改名顺序回归项。

        目录名用「资料」——其 GBK 字节非合法 UTF-8（「目录」的 GBK 字节
        恰好也是合法 UTF-8，会按 utf-8 优先链保留 mojibake，与 zip 管线
        同源语义，无法触达改名分支）。"""
        gbk_dir = "资料".encode("gbk")
        child = _raw_path(_raw_path(raw_name_dir, gbk_dir), self.GBK_BYTES)
        child.parent.mkdir(parents=True)
        child.write_bytes(b"x")
        upload_rar.normalize_rar_names(raw_name_dir)
        restored = raw_name_dir / "资料" / "中文.txt"
        assert restored.read_bytes() == b"x"
        assert not os.path.lexists(os.fsencode(_raw_path(raw_name_dir, gbk_dir)))

    def test_rename_conflict_rejected(self, raw_name_dir):
        """解码目标已存在 → invalid_archive 拒绝，不做覆盖。"""
        (raw_name_dir / "中文.txt").write_bytes(b"existing")
        _raw_path(raw_name_dir, self.GBK_BYTES).write_bytes(b"incoming")
        with pytest.raises(upload.ArchiveError) as ei:
            upload_rar.normalize_rar_names(raw_name_dir)
        assert ei.value.code == "invalid_archive"
        # 原有内容未被覆盖
        assert (raw_name_dir / "中文.txt").read_bytes() == b"existing"

    def test_valid_utf8_names_untouched(self, extract_dir):
        (extract_dir / "hello.tex").write_bytes(b"x")
        (extract_dir / "sub").mkdir()
        (extract_dir / "sub" / "notes.txt").write_bytes(b"y")
        before = sorted(str(p.relative_to(extract_dir)) for p in extract_dir.rglob("*"))
        upload_rar.normalize_rar_names(extract_dir)
        after = sorted(str(p.relative_to(extract_dir)) for p in extract_dir.rglob("*"))
        assert before == after

    def test_does_not_follow_symlinked_dirs(self, raw_name_dir):
        """符号链接目录不递进：归一化先于 _validate_extracted 的符号链接
        拒绝执行，绝不能沿链接改到解压根之外的文件系统。"""
        victim_dir = Path(tempfile.mkdtemp(prefix="latexcellence-rarnorm-out-"))
        try:
            victim = _raw_path(victim_dir, self.GBK_BYTES)
            victim.write_bytes(b"x")
            link = raw_name_dir / "link"
            try:
                os.symlink(victim_dir, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                pytest.skip("当前环境不允许创建符号链接")
            upload_rar.normalize_rar_names(raw_name_dir)
            # 链接目标内的外部文件未被改名或触碰
            assert os.path.lexists(os.fsencode(victim))
            assert not os.path.lexists(
                os.fsencode(_raw_path(victim_dir, "中文.txt".encode()))
            )
        finally:
            shutil.rmtree(victim_dir, ignore_errors=True)

    def test_decode_chain_exhausted_rejected(self, raw_name_dir, monkeypatch):
        """全链解码失败 → invalid_archive（宁可拒收，不入库坏名）。

        cp437 对全部字节可解、链实际不会耗尽；为触达防御分支，把解码链
        收窄到 utf-8+gbk 并使用两者都非法的字节序列。"""
        monkeypatch.setattr(upload_rar, "RAR_NAME_DECODE_CHAIN", ("utf-8", "gbk"))
        undecodable = b"\xff\xff.bin"  # utf-8/gbk 均非法
        _raw_path(raw_name_dir, undecodable).write_bytes(b"x")
        with pytest.raises(upload.ArchiveError) as ei:
            upload_rar.normalize_rar_names(raw_name_dir)
        assert ei.value.code == "invalid_archive"

    def test_applied_in_bsdtar_branch_before_case_scan(self, raw_name_dir, monkeypatch):
        """端到端接线：bsdtar 分支成功退出后、大小写扫描前执行归一化。

        fake runner 不落盘，surrogate 字节名预先放置在解压目录内；解压目录
        本身也须位于支持原始字节名的文件系统（NTFS 挂载会 EINVAL）。"""
        _patch_which(monkeypatch, "bsdtar")
        _install_runner(monkeypatch, _FakeRunner(result=_fake_result()))
        work = raw_name_dir / "extracted"
        work.mkdir()
        _raw_path(work, self.GBK_BYTES).write_bytes(b"x")
        res = upload.extract_archive(FIXTURES / "layout_flat.rar", work, _limits())
        assert res.file_count >= 1
        assert (work / "中文.txt").exists()


# ── 缺二进制与限时接线（不依赖解压器二进制）─────────────────────
class TestWiring:
    def test_missing_binary_reports_rar_extractor_missing(
        self, extract_dir, monkeypatch
    ):
        _patch_which(monkeypatch, None)
        runner = _install_runner(monkeypatch, _FakeRunner(result=_fake_result()))
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(FIXTURES / "layout_flat.rar", extract_dir, _limits())
        assert ei.value.code == "rar_extractor_missing"
        # 选择层失败：解压命令从未发起
        assert runner.execute_calls == 0

    @pytest.mark.parametrize("rar_tool", ["unar", "bsdtar"])
    def test_expired_deadline_skips_execution(self, extract_dir, monkeypatch, rar_tool):
        """timeout_seconds=0：deadline 已过，未发起执行即拒绝（双工具）。"""
        _patch_which(monkeypatch, rar_tool)
        runner = _install_runner(monkeypatch, _FakeRunner(result=_fake_result()))
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(
                FIXTURES / "layout_flat.rar", extract_dir, _limits(timeout_seconds=0)
            )
        assert ei.value.code == "invalid_archive"
        assert runner.execute_calls == 0

    @pytest.mark.parametrize("rar_tool", ["unar", "bsdtar"])
    def test_backend_timeout_maps_invalid_archive(
        self, extract_dir, monkeypatch, rar_tool
    ):
        """deadline 折算 spec.timeout_seconds：后端 timed_out → 既有超时文案。"""
        _patch_which(monkeypatch, rar_tool)
        _install_runner(
            monkeypatch,
            _FakeRunner(result=_fake_result(returncode=-1, timed_out=True)),
        )
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(FIXTURES / "layout_flat.rar", extract_dir, _limits())
        assert ei.value.code == "invalid_archive"
        assert "timed out" in ei.value.message

    @pytest.mark.parametrize("rar_tool", ["unar", "bsdtar"])
    def test_zero_product_guard_hermetic(self, extract_dir, monkeypatch, rar_tool):
        """exit=0 且零产物（多卷集静默形态）：双工具兜底用例。"""
        _patch_which(monkeypatch, rar_tool)
        _install_runner(monkeypatch, _FakeRunner(result=_fake_result()))
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(FIXTURES / "layout_flat.rar", extract_dir, _limits())
        assert ei.value.code == "invalid_archive"

    @pytest.mark.parametrize("rar_tool", ["unar", "bsdtar"])
    def test_case_conflict_rejected_hermetic(self, extract_dir, monkeypatch, rar_tool):
        """解压产物大小写不敏感重名：后置扫描拒绝（不依赖真实解压器，
        bsdtar 变体同时验证归一化不影响该扫描）。"""
        _patch_which(monkeypatch, rar_tool)
        _install_runner(monkeypatch, _FakeRunner(result=_fake_result()))
        (extract_dir / "README.tex").write_text("A", encoding="utf-8")
        (extract_dir / "readme.tex").write_text("B", encoding="utf-8")
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(FIXTURES / "layout_flat.rar", extract_dir, _limits())
        assert ei.value.code == "archive_unsafe"


# ── 宿主侧监视任务与取消接线（fake 进程注入，不依赖 unar/docker）──
class TestRarWatch:
    """监视任务（沙箱化后为宿主侧 asyncio task）：越限置位 cancel_event。"""

    @pytest.mark.asyncio
    async def test_quota_breach_sets_cancel_event(self, tmp_path, monkeypatch):
        monkeypatch.setattr(upload_rar, "_WATCH_INTERVAL_SECONDS", 0.01)
        extract = tmp_path / "extracted"
        extract.mkdir()
        # 预置已写盘量超过剩余配额的解压目录（模拟解压中途越限）
        (extract / "bomb.bin").write_bytes(b"\0" * 1024)

        limits = _limits(remaining_quota_bytes=512, timeout_seconds=999)
        cancel_event = asyncio.Event()
        breach: list[str] = []
        await asyncio.wait_for(
            upload_rar._rar_watch_task(
                extract, limits, upload._deadline(limits), cancel_event, breach
            ),
            timeout=5,
        )
        assert breach == ["quota_exceeded"]
        assert cancel_event.is_set()

    @pytest.mark.asyncio
    async def test_deadline_breach_sets_cancel_event(self, tmp_path, monkeypatch):
        monkeypatch.setattr(upload_rar, "_WATCH_INTERVAL_SECONDS", 0.01)
        extract = tmp_path / "extracted"
        extract.mkdir()
        limits = _limits(timeout_seconds=999)
        cancel_event = asyncio.Event()
        breach: list[str] = []
        await asyncio.wait_for(
            upload_rar._rar_watch_task(
                extract, limits, time.monotonic() - 1, cancel_event, breach  # 已过期
            ),
            timeout=5,
        )
        assert breach == ["invalid_archive"]
        assert cancel_event.is_set()

    @pytest.mark.asyncio
    async def test_normal_state_not_interrupted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(upload_rar, "_WATCH_INTERVAL_SECONDS", 0.01)
        extract = tmp_path / "extracted"
        extract.mkdir()
        (extract / "a.tex").write_bytes(b"x" * 16)

        limits = _limits(remaining_quota_bytes=1 << 20, timeout_seconds=60)
        cancel_event = asyncio.Event()
        breach: list[str] = []
        task = asyncio.ensure_future(
            upload_rar._rar_watch_task(
                extract, limits, upload._deadline(limits), cancel_event, breach
            )
        )
        # 数个采样周期内未越限 → 不置位 cancel_event、不记 breach
        await asyncio.sleep(0.05)
        assert not cancel_event.is_set()
        assert breach == []
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class TestCancelWiring:
    """监视任务 → cancel_event → 后端取消 的端到端接线（阶段一 Q4 方案 A）。

    fake runner 模拟后端在 cancel_event 置位后以非零退出返回。"""

    def test_quota_breach_cancels_execution(self, extract_dir, monkeypatch):
        monkeypatch.setattr(upload_rar, "_WATCH_INTERVAL_SECONDS", 0.01)
        (extract_dir / "bomb.bin").write_bytes(b"\0" * 1024)

        async def impl(spec, *, user_id=None, cancel_event=None):
            await cancel_event.wait()
            return _fake_result(returncode=137)

        _install_runner(monkeypatch, _FakeRunner(execute_impl=impl))
        _patch_which(monkeypatch, "unar")
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(
                FIXTURES / "layout_flat.rar",
                extract_dir,
                _limits(remaining_quota_bytes=512),
            )
        assert ei.value.code == "quota_exceeded"

    def test_user_lease_context_survives_worker_loop(self, extract_dir, monkeypatch):
        """每用户租约上下文经 worker 循环传入 execute（Q4 每用户并发治理）。

        服务层真实调用形态：请求循环内 ContextVar 已注入当前用户 →
        asyncio.to_thread 调度同步核心 → 核心内 asyncio.run 建独立循环。
        跨线程 + 跨循环的 ContextVar 传递链是本次接入的关键接缝——
        本用例不 Mock current_sandbox_user_id 本身，仅替换 runner，
        保证该接缝不被实现细节变化悄悄打断。"""
        from backend.core.multiuser import userspace

        _patch_which(monkeypatch, "unar")
        runner = _install_runner(monkeypatch, _FakeRunner(result=_fake_result()))
        (extract_dir / "main.tex").write_text("x", encoding="utf-8")

        async def caller():
            userspace.set_current_user_id("u-ctx")
            try:
                await asyncio.to_thread(
                    upload_rar.extract_rar,
                    FIXTURES / "layout_flat.rar",
                    extract_dir,
                    _limits(),
                )
            finally:
                userspace.set_current_user_id(None)

        asyncio.run(caller())
        assert runner.user_ids == ["u-ctx"]


# ── 真实解压（需要解压器；unar / bsdtar 双工具参数化）──────────
# 加密/分卷包在预扫阶段即确定性拒绝，与实际选中的解压器无关
@pytest.mark.parametrize("force_rar_tool", RAR_TOOL_PARAMS, indirect=True)
class TestNormalExtract:
    def test_top_dir_layout(self, extract_dir, force_rar_tool):
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

    def test_flat_layout(self, extract_dir, force_rar_tool):
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

    def test_gbk_name_extracts_single_file(self, raw_name_dir, force_rar_tool):
        """GBK 文件名：unar 走字符集探测，与 zip 管线可能不一致（已知差异），
        只断言唯一文件且内容一致；bsdtar 落盘原始字节名后经归一化步骤解码，
        应精确还原为中文名（与 zip 管线一致，强于 unar 的探测结果）。

        解压目录用 raw_name_dir 而非 extract_dir：会话级 basetemp 位于仓库
        工作区内，可能落在拒绝非 UTF-8 原始字节名的挂载点（如 ntfs3，
        bsdtar 落盘后 stat 直接 EINVAL），须先探测文件系统能力再执行。"""
        res = upload.extract_archive(FIXTURES / "gbk_name.rar", raw_name_dir, _limits())
        files = [p for p in raw_name_dir.rglob("*") if p.is_file()]
        assert len(files) == 1
        assert files[0].read_bytes() == b"gbk name\n"
        if force_rar_tool == "bsdtar":
            # 该断言依赖 bsdtar 分支的 normalize_rar_names 步骤
            assert files[0].name == "中文.txt"
            json.dumps(files[0].name)  # 文件树接口 JSON 序列化安全
        else:
            assert files[0].name.endswith(".txt")
        assert res.file_count == 1

    def test_archive_with_service_data_extracts(self, extract_dir, force_rar_tool):
        """带恢复记录数据区的合法包端到端：预扫放行后解压器忽略服务块并
        正常解出文件（缺陷一的功能性验收）。"""
        res = upload.extract_archive(
            FIXTURES / "service_rr.rar", extract_dir, _limits()
        )
        assert (res.root / "main.tex").read_text(
            encoding="utf-8"
        ) == "\\documentclass{article}\nHello\n"


@pytest.mark.parametrize("force_rar_tool", RAR_TOOL_PARAMS, indirect=True)
class TestProtectedReject:
    def test_encrypted_rejected(self, extract_dir, force_rar_tool):
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(FIXTURES / "encrypted.rar", extract_dir, _limits())
        assert ei.value.code == "archive_unsafe"

    def test_volume_first_part_rejected(self, extract_dir, force_rar_tool):
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(
                FIXTURES / "volume.part1.rar", extract_dir, _limits()
            )
        assert ei.value.code == "archive_unsafe"


# ── 真实压缩 / 加密 / 分卷样本（部署收口阶段二，需要解压器）──────
# 样本由 rarlab rar 6.24 生成入库（压缩流无法伪造），结构期望由
# tools/rar_fixtures/verify_fixtures.py 独立对账；再生成步骤见
# tools/rar_fixtures/make_fixtures.py 模块 docstring。
@pytest.mark.parametrize("force_rar_tool", RAR_TOOL_PARAMS, indirect=True)
class TestRealCompressedExtract:
    """真实压缩 RAR4 / RAR5 端到端解压：预扫放行 → 沙箱解压 →
    产物与源 .tex 内容逐字节一致（阶段二验收口径，unar 同 bsdtar 覆盖）。"""

    @pytest.mark.parametrize(
        "fixture_name", ["compressed_rar4.rar", "compressed_rar5.rar"]
    )
    def test_compressed_archive_roundtrip(
        self, extract_dir, fixture_name, force_rar_tool
    ):
        res = upload.extract_archive(FIXTURES / fixture_name, extract_dir, _limits())
        # 单文件直接内容布局：解压根即项目根
        assert res.root == extract_dir
        assert (res.root / "main.tex").read_text(encoding="utf-8") == COMPRESSED_TEX
        assert res.file_count == 1


@pytest.mark.parametrize("force_rar_tool", RAR_TOOL_PARAMS, indirect=True)
class TestRealProtectedReject:
    """真实受保护样本的确定性拒绝。

    - 真实加密 RAR5（AES-256）：预扫只覆盖 RAR4，拒绝发生在解压器失败
      输出的关键词匹配——unar 报「requires a password」命中 password →
      archive_unsafe；bsdtar 报「Unsupported block header size」不含关键
      词 → invalid_archive（§五：安全性不变，仅错误细分度随工具下降）；
    - 真实 RAR4 分卷对：预扫 MHD_VOLUME 确定性拦截，与解压器无关，
      第一卷 / 后续卷单独上传均拒绝。分卷样本选用 RAR4 而非 RAR5：
      RAR5 分卷第一卷单独上传时 unar 仅零产物退出（拒绝依赖零产物兜底
      的弱信号），RAR4 预扫在解压发起前拦截、不依赖解压器行为——
      见 make_fixtures.py docstring 与 rar-extractor-fallback.md §八。"""

    def test_real_encrypted_rar5_rejected(self, extract_dir, force_rar_tool):
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(
                FIXTURES / "encrypted_rar5.rar", extract_dir, _limits()
            )
        expected = {"unar": "archive_unsafe", "bsdtar": "invalid_archive"}
        assert ei.value.code == expected[force_rar_tool]

    def test_real_volume_parts_rejected(self, extract_dir, force_rar_tool):
        for name in ("volume4.part1.rar", "volume4.part2.rar"):
            with pytest.raises(upload.ArchiveError) as ei:
                upload.extract_archive(FIXTURES / name, extract_dir, _limits())
            assert ei.value.code == "archive_unsafe"


@pytest.mark.parametrize("force_rar_tool", RAR_TOOL_PARAMS, indirect=True)
class TestCorrupt:
    def test_garbage_bytes_rejected(self, extract_dir, tmp_path, force_rar_tool):
        p = tmp_path / "upload.rar"
        p.write_bytes(b"definitely not a rar archive")
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(p, extract_dir, _limits())
        assert ei.value.code == "invalid_archive"

    def test_empty_archive_rejected(self, extract_dir, tmp_path, force_rar_tool):
        """合法但零条目的 rar：多卷集静默零产物兜底同样拒绝。"""
        p = tmp_path / "upload.rar"
        p.write_bytes(_rar4())
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(p, extract_dir, _limits())
        assert ei.value.code == "invalid_archive"

    def test_case_conflict_rejected_end_to_end(
        self, extract_dir, tmp_path, force_rar_tool
    ):
        """真实解压器解出大小写重名对：后置扫描拒绝（Linux 上两者共存）。"""
        p = tmp_path / "upload.rar"
        p.write_bytes(_rar4(entries=[(b"README.tex", b"A"), (b"readme.tex", b"B")]))
        with pytest.raises(upload.ArchiveError) as ei:
            upload.extract_archive(p, extract_dir, _limits())
        assert ei.value.code == "archive_unsafe"


# ── API 集成（需要解压器；unar / bsdtar 双工具参数化）──────────
@pytest.mark.parametrize("force_rar_tool", RAR_TOOL_PARAMS, indirect=True)
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

    def test_upload_rar_creates_project(self, env, alice_client, force_rar_tool):
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

    @pytest.mark.parametrize(
        "fixture_name", ["compressed_rar4.rar", "compressed_rar5.rar"]
    )
    def test_upload_compressed_rar_creates_project(
        self, env, alice_client, fixture_name, force_rar_tool
    ):
        """真实压缩 RAR4/RAR5 经 API 端到端上传创建项目
        （解压 + 内容核验 + 落位用户空间，阶段二验收口径）。"""
        from tests.test_multiuser.conftest import csrf

        payload = (FIXTURES / fixture_name).read_bytes()
        r = alice_client.post(
            "/api/projects",
            data={"name": "RarCompressed"},
            files={"archive": (fixture_name, payload, "application/vnd.rar")},
            headers=csrf(alice_client),
        )
        assert r.status_code == 201, r.text
        proj = r.json()["project"]
        root = userspace.project_dir(env["alice"]["id"], proj["project_id"])
        assert (root / "main.tex").read_text(encoding="utf-8") == COMPRESSED_TEX

    def test_upload_encrypted_rar_rejected(self, env, alice_client, force_rar_tool):
        from tests.test_multiuser.conftest import csrf

        payload = (FIXTURES / "encrypted.rar").read_bytes()
        r = alice_client.post(
            "/api/projects",
            files={"archive": ("enc.rar", payload, "application/vnd.rar")},
            headers=csrf(alice_client),
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "archive_unsafe"

    def test_upload_rar_with_service_data_creates_project(
        self, env, alice_client, force_rar_tool
    ):
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

    def test_upload_unsupported_suffix_rejected(
        self, env, alice_client, force_rar_tool
    ):
        from tests.test_multiuser.conftest import csrf

        r = alice_client.post(
            "/api/projects",
            files={"archive": ("x.tar", b"xx", "application/x-tar")},
            headers=csrf(alice_client),
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_archive"


# ── API 集成：沙箱硬失败语义（hermetic，不依赖解压器二进制）──────
class TestApiSandboxErrorMapping:
    """rar 解压经 SandboxRunner 后，docker 模式硬失败须映射 503 而非 400。

    类级双工具参数化不适用于本组用例：fake runner 在解压器选择前即抛
    沙箱错误，选择层不触达，hermetic 恒执行。"""

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

    def test_upload_docker_unavailable_maps_503(self, env, alice_client, monkeypatch):
        """mode=docker 硬失败（daemon 不可达）：sandbox_unavailable → 503（Q6）。

        fake runner 直接从 resolve_backend 抛沙箱错误；验证 service 层把
        带 sandbox_code 的错误映射为 503，而非落入通用 400 invalid_archive。"""
        from backend.utils.sandbox import sandbox_unavailable
        from tests.test_multiuser.conftest import csrf

        class _UnavailableRunner:
            async def resolve_backend(self, kind: str) -> str:
                assert kind == "convert"
                raise sandbox_unavailable("docker daemon unreachable (test)")

        monkeypatch.setattr(
            upload_rar, "get_sandbox_runner", lambda: _UnavailableRunner()
        )
        payload = (FIXTURES / "layout_flat.rar").read_bytes()
        r = alice_client.post(
            "/api/projects",
            files={"archive": ("proj.rar", payload, "application/vnd.rar")},
            headers=csrf(alice_client),
        )
        assert r.status_code == 503, r.text
        assert r.json()["detail"]["error"] == "sandbox_unavailable"
