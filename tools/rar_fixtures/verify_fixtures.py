#!/usr/bin/env python3
"""入库 fixtures 结构校验：防坏样本静默进入 tests/fixtures/rar/。

对 tests/fixtures/rar/ 下**全部**入库样本逐一核对结构特征（RAR4/RAR5 版本头、
压缩方法与实际压缩比、加密 / 分卷标志、服务块与子块、成员名清单），并与上传
管线的 RAR4 预扫行为对账（预期放行的不得误拒、预期拒绝的必须命中
`archive_unsafe`）。任一项不符即失败，退出码 1；全部通过退出码 0。

纯标准库解析，不依赖解压器二进制——任何环境都能完成结构校验。环境存在
unar / bsdtar 时**额外**做真实解压对账：
    - 压缩样本解出的成员内容 sha256 必须等于内置期望值（RAR4 与 RAR5 两个
      样本同源，解出内容一致）；
    - 真实加密 RAR5 样本交由解压器必须非零退出（解压失败=拒绝）；
    - 其余合法 store 样本至少解出一个文件；
    - 预扫已确定性拒绝的分卷 / 加密样例不再调用解压器（与生产管线一致，
      工具永远看不到这些包）。

用法（仓库根目录或任意位置）::

    python tools/rar_fixtures/verify_fixtures.py

样本再生成步骤见 tools/rar_fixtures/make_fixtures.py 模块 docstring（真实
压缩样本由 rarlab rar CLI 生成，压缩流无法用本仓代码伪造）。

RAR5 头部为 vint（7 位小端变长整数）编码，块布局按 unrar technote：
    头 = CRC32(u32) + HeaderSize(vint) + Type(vint) + Flags(vint)
         + [ExtraAreaSize] + [DataSize] + 类型相关字段 + [extra 区] + [数据区]
    extra 记录 = Size(vint) + Type(vint) + 数据（经 rar 6.24 产物实测：
    Type 紧跟 Size——CRYPT=0x01 / UOWNER=0x06 / SUBDATA=0x07）。
"""

from __future__ import annotations

import hashlib
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "rar"

RAR4_MARKER = b"Rar!\x1a\x07\x00"
RAR5_MARKER = b"Rar!\x1a\x07\x01\x00"

# RAR4 标志位（与 backend/core/multiuser/upload_rar.py 同源）
MHD_VOLUME = 0x0001
MHD_FIRSTVOLUME = 0x0100
LHD_PASSWORD = 0x0004
LHD_SPLIT_AFTER = 0x0002
MAIN_HEAD = 0x73
FILE_HEAD = 0x74
SUB_HEAD = 0x78
SERVICE_HEAD = 0x7A
ENDARC_HEAD = 0x7B
METHOD_STORE = 0x30  # '0'；'1'..'5' = 0x31..0x35 为真实压缩

# RAR5 头类型 / extra 记录类型（unrar rarv5.hpp 命名）
RAR5_MAIN_HEAD = 1
RAR5_FILE_HEAD = 2
RAR5_SERVICE_HEAD = 3
RAR5_ENDARC_HEAD = 5
RAR5_EXTRA_CRYPT = 0x01
RAR5_HFLAGS_EXTRA = 0x0001
RAR5_HFLAGS_DATA = 0x0002
RAR5_AFLAGS_VOLUME = 0x0001

# 压缩样本的源内容（346 字节自产 .tex 文本，RAR4 与 RAR5 同源）：
# 头内 CRC32 与解压结果 sha256 双重绑定，防止「结构对但内容错」的坏样本。
SOURCE_TEX_CRC32 = 0x75EB3C4F
SOURCE_TEX_SHA256 = "fbb50bfc247c1d3fc29fd3e665a98e7600dd148fd1321c52ca52e92461d84c12"

# 单样本体积上限：入库口径为 KB 级小样本，防大文件静默混入
MAX_FIXTURE_BYTES = 8192


@dataclass
class Rar4File:
    """RAR4 文件 / 服务块解析结果。"""

    btype: int
    flags: int
    method: int  # '0'..'5' 的 ASCII 码
    pack_size: int
    unp_size: int
    file_crc: int
    name: bytes


@dataclass
class Rar4Archive:
    main_flags: int
    files: list[Rar4File] = field(default_factory=list)
    has_sub_head: bool = False  # 0x78 子块（NTFS 流等）
    walk_complete: bool = False  # 走到 ENDARC（False=未知块截断，仅可做弱断言）


@dataclass
class Rar5File:
    name: str
    method: int  # 0=store，1..5=压缩
    unp_size: int
    data_size: int | None  # 打包后数据区长度（None=无数据区）
    file_crc32: int | None
    split: bool  # 头 flags 带 SPLIT_BEFORE/AFTER
    extra_types: list[int] = field(default_factory=list)


@dataclass
class Rar5Archive:
    archive_flags: int
    files: list[Rar5File] = field(default_factory=list)


@dataclass
class Expectation:
    """单个入库样本的结构期望（verify 断言的声明式描述）。"""

    fmt: str  # "rar4" | "rar5"
    prescan: str  # "pass" | "reject"：生产管线 reject_protected_rar 的预期
    check: str  # 结构特征检查方法名（_check_*）
    description: str


# ── RAR4 解析（布局与 upload_rar.reject_protected_rar 同源）─────────


def parse_rar4(data: bytes) -> Rar4Archive:
    """解析 RAR4 头链；未知块类型处截断（与预扫「不在字节流上盲走」一致）。"""
    arc = Rar4Archive(
        main_flags=struct.unpack_from("<H", data, len(RAR4_MARKER) + 3)[0]
    )
    pos = len(RAR4_MARKER) + 13  # 主头固定 13 字节
    while pos + 7 <= len(data):
        btype, flags, hsize = struct.unpack_from("<BHH", data, pos + 2)
        if btype == ENDARC_HEAD:
            arc.walk_complete = True
            break
        if hsize < 7:
            break
        if btype in (FILE_HEAD, SERVICE_HEAD):
            pack_size, unp_size = struct.unpack_from("<II", data, pos + 7)
            # 固定段：PACK(4) UNP(4) HOST_OS(1) FILE_CRC(4) FTIME(4)
            # UNP_VER(1) METHOD(1) NAME_SIZE(2) ATTR(4)
            file_crc = struct.unpack_from("<I", data, pos + 16)[0]
            namesize = struct.unpack_from("<H", data, pos + 26)[0]
            name = data[pos + hsize - namesize : pos + hsize]
            arc.files.append(
                Rar4File(
                    btype=btype,
                    flags=flags,
                    method=data[pos + 25],
                    pack_size=pack_size,
                    unp_size=unp_size,
                    file_crc=file_crc,
                    name=name,
                )
            )
        elif btype == SUB_HEAD:
            arc.has_sub_head = True
        else:
            break  # 未知块类型：无法定位下一块，安全截断
        pos += hsize + struct.unpack_from("<I", data, pos + 7)[0]
    return arc


# ── RAR5 解析（vint 变长整数）───────────────────────────────────────


def _read_vint(data: bytes, pos: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while True:
        val |= (data[pos] & 0x7F) << shift
        if not data[pos] & 0x80:
            return val, pos + 1
        shift += 7
        pos += 1


def parse_rar5(data: bytes) -> Rar5Archive:
    """解析 RAR5 头链（main / file / service / endarc）。"""
    arc = Rar5Archive(archive_flags=0)
    pos = len(RAR5_MARKER)
    while pos + 4 <= len(data):
        p = pos + 4  # 跳过头部 CRC32
        hsize, p = _read_vint(data, p)
        header_end = p + hsize
        htype, p = _read_vint(data, p)
        hflags, p = _read_vint(data, p)
        extra_size = data_size = None
        if hflags & RAR5_HFLAGS_EXTRA:
            extra_size, p = _read_vint(data, p)
        if hflags & RAR5_HFLAGS_DATA:
            data_size, p = _read_vint(data, p)
        if htype == RAR5_MAIN_HEAD:
            arc.archive_flags, _ = _read_vint(data, p)
        elif htype in (RAR5_FILE_HEAD, RAR5_SERVICE_HEAD):
            fflags, q = _read_vint(data, p)
            unp_size, q = _read_vint(data, q)
            _attr, q = _read_vint(data, q)
            if fflags & 0x0002:  # mtime 存在
                q += 4
            file_crc = None
            if fflags & 0x0004:  # CRC32 存在
                file_crc = struct.unpack_from("<I", data, q)[0]
                q += 4
            comp_info, q = _read_vint(data, q)
            q += 1  # host os
            name_len, q = _read_vint(data, q)
            name = data[q : q + name_len].decode("utf-8", "replace")
            q += name_len
            extra_types: list[int] = []
            if extra_size is not None:
                end = q + extra_size
                while q < end:
                    rec_size, q = _read_vint(data, q)
                    rec_end = q + rec_size
                    rec_type, _ = _read_vint(data, q)
                    extra_types.append(rec_type)
                    q = rec_end
            arc.files.append(
                Rar5File(
                    name=name,
                    method=(comp_info >> 7) & 0x7,
                    unp_size=unp_size,
                    data_size=data_size,
                    file_crc32=file_crc,
                    split=bool(hflags & 0x0018),  # SPLIT_BEFORE | SPLIT_AFTER
                    extra_types=extra_types,
                )
            )
        elif htype == RAR5_ENDARC_HEAD:
            break
        pos = header_end + (data_size or 0 if hflags & RAR5_HFLAGS_DATA else 0)
    return arc


# ── 结构特征检查（每个入库样本一项）─────────────────────────────────

_STORE_MEMBERS_FLAT = [b"main.tex", b"notes\\readme.txt"]
_STORE_MEMBERS_TOP = [b"proj\\main.tex", b"proj\\refs.bib"]


def _store_member_map(arc: Rar4Archive) -> dict[bytes, Rar4File]:
    return {f.name: f for f in arc.files if f.btype == FILE_HEAD}


def _check_flat(arc: Rar4Archive, arc5: Rar5Archive | None, fails: list[str]) -> None:
    members = _store_member_map(arc)
    for name in _STORE_MEMBERS_FLAT:
        f = members.get(name)
        if f is None or f.method != METHOD_STORE:
            fails.append(f"成员 {name!r} 缺失或非 store 方法")


def _check_top_dir(
    arc: Rar4Archive, arc5: Rar5Archive | None, fails: list[str]
) -> None:
    members = _store_member_map(arc)
    for name in _STORE_MEMBERS_TOP:
        f = members.get(name)
        if f is None or f.method != METHOD_STORE:
            fails.append(f"成员 {name!r} 缺失或非 store 方法")


def _check_gbk(arc: Rar4Archive, arc5: Rar5Archive | None, fails: list[str]) -> None:
    members = _store_member_map(arc)
    expected = "中文.txt".encode("gbk")
    if list(members) != [expected]:
        fails.append(f"成员应为 GBK 原始字节名 {expected!r}，实际 {list(members)}")


def _check_encrypted_rar4(
    arc: Rar4Archive, arc5: Rar5Archive | None, fails: list[str]
) -> None:
    if not arc.files or not arc.files[0].flags & LHD_PASSWORD:
        fails.append("文件头应置 LHD_PASSWORD 加密标志")


def _check_volume_fake(
    arc: Rar4Archive, arc5: Rar5Archive | None, fails: list[str]
) -> None:
    if not arc.main_flags & MHD_VOLUME or not arc.main_flags & MHD_FIRSTVOLUME:
        fails.append("主头应同时置 MHD_VOLUME | MHD_FIRSTVOLUME")
    if not arc.files or not arc.files[0].flags & LHD_SPLIT_AFTER:
        fails.append("文件头应置 LHD_SPLIT_AFTER（延续到下一卷）")


def _check_service_rr(
    arc: Rar4Archive, arc5: Rar5Archive | None, fails: list[str]
) -> None:
    rr = [f for f in arc.files if f.btype == SERVICE_HEAD]
    if not rr or rr[0].name != b"RR":
        fails.append("应存在名为 'RR' 的 SERVICE(0x7A) 服务块")
    elif not rr[0].flags & 0x8000:  # LONG_BLOCK：声明本块携带 PACK_SIZE 数据区
        fails.append(
            "SERVICE 块应置 LONG_BLOCK（0x8000，与真实 rar 的 RR 块一致）；"
            "缺该标志时 unar/unrar 视为无数据区并解出 0 个文件"
        )
    if b"main.tex" not in _store_member_map(arc):
        fails.append("应存在 store 模式成员 main.tex")


def _check_subhead(
    arc: Rar4Archive, arc5: Rar5Archive | None, fails: list[str]
) -> None:
    if not arc.has_sub_head:
        fails.append("应存在 SUB_HEAD(0x78) 子块")
    if b"a.tex" not in _store_member_map(arc):
        fails.append("应存在 store 模式成员 a.tex")


def _check_compressed_rar4(
    arc: Rar4Archive, arc5: Rar5Archive | None, fails: list[str]
) -> None:
    members = _store_member_map(arc)
    if list(members) != [b"main.tex"]:
        fails.append(f"应恰好含单个成员 main.tex，实际 {list(members)}")
        return
    f = members[b"main.tex"]
    if f.method <= METHOD_STORE:
        fails.append(f"压缩方法应为 m1..m5，实际 {chr(f.method)!r}")
    if f.pack_size >= f.unp_size:
        fails.append(f"数据应实际压缩（pack={f.pack_size} >= unp={f.unp_size}）")
    if f.file_crc != SOURCE_TEX_CRC32:
        fails.append(
            f"FILE_CRC={f.file_crc:#x} 与源内容 CRC {SOURCE_TEX_CRC32:#x} 不符"
        )


def _check_compressed_rar5(
    arc: Rar4Archive, arc5: Rar5Archive | None, fails: list[str]
) -> None:
    if arc5 is None or not arc5.files:
        fails.append("RAR5 解析无文件头")
        return
    f = arc5.files[0]
    if f.name != "main.tex" or arc5.files != [f]:
        fails.append(f"应恰好含单个成员 main.tex，实际 {[x.name for x in arc5.files]}")
    if f.method == 0:
        fails.append("压缩方法应为 m1..m5（非 store）")
    if f.data_size is None or f.data_size >= f.unp_size:
        fails.append(f"数据应实际压缩（data={f.data_size} >= unp={f.unp_size}）")
    if f.file_crc32 != SOURCE_TEX_CRC32:
        fails.append(f"CRC32={f.file_crc32:#x} 与源内容 CRC {SOURCE_TEX_CRC32:#x} 不符")


def _check_encrypted_rar5(
    arc: Rar4Archive, arc5: Rar5Archive | None, fails: list[str]
) -> None:
    if arc5 is None or not arc5.files:
        fails.append("RAR5 解析无文件头")
        return
    f = arc5.files[0]
    if RAR5_EXTRA_CRYPT not in f.extra_types:
        fails.append(
            f"文件头 extra 区应含 CRYPT({RAR5_EXTRA_CRYPT:#x}) 加密记录，"
            f"实际 {[hex(t) for t in f.extra_types]}"
        )


def _check_volume4_part1(
    arc: Rar4Archive, arc5: Rar5Archive | None, fails: list[str]
) -> None:
    if not arc.main_flags & MHD_VOLUME:
        fails.append("主头应置 MHD_VOLUME（多卷集合）")
    if not arc.main_flags & MHD_FIRSTVOLUME:
        fails.append("主头应置 MHD_FIRSTVOLUME（第一卷）")


def _check_volume4_part2(
    arc: Rar4Archive, arc5: Rar5Archive | None, fails: list[str]
) -> None:
    if not arc.main_flags & MHD_VOLUME:
        fails.append("主头应置 MHD_VOLUME（多卷集合）")
    if arc.main_flags & MHD_FIRSTVOLUME:
        fails.append("后续卷不应置 MHD_FIRSTVOLUME")


# 期望表：与 tests/fixtures/rar/ 入库清单一一对应；新增 / 删除样本必须同步
EXPECTATIONS: dict[str, Expectation] = {
    "layout_flat.rar": Expectation(
        "rar4",
        "pass",
        "_check_flat",
        "store 直接内容布局（main.tex + notes/readme.txt）",
    ),
    "layout_top_dir.rar": Expectation(
        "rar4", "pass", "_check_top_dir", "store 顶层单目录布局（proj/）"
    ),
    "gbk_name.rar": Expectation(
        "rar4", "pass", "_check_gbk", "store GBK 原始字节文件名样例"
    ),
    "encrypted.rar": Expectation(
        "rar4",
        "reject",
        "_check_encrypted_rar4",
        "store 加密标志样例（数据未真实加密）",
    ),
    "volume.part1.rar": Expectation(
        "rar4", "reject", "_check_volume_fake", "store 多卷集第一卷形态样例"
    ),
    "service_rr.rar": Expectation(
        "rar4", "pass", "_check_service_rr", "SERVICE(0x7A) 恢复记录数据区 + 正常文件"
    ),
    "subhead_ntfs.rar": Expectation(
        "rar4", "pass", "_check_subhead", "SUB_HEAD(0x78) 子块数据区 + 正常文件"
    ),
    "compressed_rar4.rar": Expectation(
        "rar4", "pass", "_check_compressed_rar4", "rar 6.24 -ma4 -m5 真实压缩样例"
    ),
    "compressed_rar5.rar": Expectation(
        "rar5", "pass", "_check_compressed_rar5", "rar 6.24 -ma5 -m5 真实压缩样例"
    ),
    "encrypted_rar5.rar": Expectation(
        "rar5",
        "pass",
        "_check_encrypted_rar5",
        "rar 6.24 -ma5 -p 真实加密样例（AES-256）",
    ),
    "volume4.part1.rar": Expectation(
        "rar4", "reject", "_check_volume4_part1", "真实 RAR4 分卷第一卷（-v1k）"
    ),
    "volume4.part2.rar": Expectation(
        "rar4", "reject", "_check_volume4_part2", "真实 RAR4 分卷第二卷（-v1k）"
    ),
}


# ── 校验主流程 ──────────────────────────────────────────────────────


def _run_prescan(path: Path, expect_reject: bool, fails: list[str]) -> None:
    """生产管线预扫对账：预期放行的不得误拒，预期拒绝的必须命中 archive_unsafe。"""
    from backend.core.multiuser import upload, upload_rar

    try:
        upload_rar.reject_protected_rar(path)
        if expect_reject:
            fails.append("预扫意外放行（预期 archive_unsafe 拒绝）")
    except upload.ArchiveError as e:
        if not expect_reject:
            fails.append(f"预扫误拒（code={e.code}）：{e.message}")
        elif e.code != "archive_unsafe":
            fails.append(f"预扫拒绝码应为 archive_unsafe，实际 {e.code}")


def _extract_to(
    archive: Path, out_dir: Path
) -> tuple[str, str, "subprocess.CompletedProcess[bytes]"] | None:
    """经生产管线同一选择层与命令构造真实解压一次；无解压器返回 None。"""
    from backend.core.multiuser import upload_rar

    try:
        tool, binary = upload_rar.resolve_rar_extractor()
    except upload_rar.ArchiveError:
        return None
    cmd = upload_rar.RAR_EXTRACTOR_CMD_BUILDERS[tool](binary, archive, out_dir)
    proc = subprocess.run(
        cmd, stdin=subprocess.DEVNULL, capture_output=True, start_new_session=True
    )
    return tool, binary, proc


def _live_extraction_checks(results: list[str]) -> None:
    """有 unar/bsdtar 时的真实解压对账（可选增强，缺失时记 SKIP）。"""
    checks: list[tuple[Path, str]] = [
        (FIXTURE_DIR / name, kind)
        for name, kind in (
            ("compressed_rar4.rar", "content"),
            ("compressed_rar5.rar", "content"),
            ("encrypted_rar5.rar", "must_fail"),
            *(
                (FIXTURE_DIR / name, "nonempty")
                for name in (
                    "layout_flat.rar",
                    "layout_top_dir.rar",
                    "service_rr.rar",
                    # subhead_ntfs.rar 不做真实解压对账：手工构造的非规范
                    # NTFS 子块会被 bsdtar Header CRC error 拒绝，属
                    # docs/rar-extractor-fallback.md §八 风险 1 的已知限制
                    # （预扫 / 结构检查仍覆盖该样本）。
                )
            ),
        )
    ]
    with tempfile.TemporaryDirectory(prefix="rar-verify-live-") as td:
        out_dir = Path(td)
        for archive, kind in checks:
            work = out_dir / f"{archive.stem}_out"
            work.mkdir()
            run = _extract_to(archive, work)
            if run is None:
                results.append(f"live[{archive.name}]: SKIP（环境无 unar/bsdtar）")
                continue
            tool, _binary, proc = run
            produced = sorted(p for p in work.rglob("*") if p.is_file())
            if kind == "content":
                if proc.returncode != 0 or len(produced) != 1:
                    results.append(
                        f"live[{archive.name}]: FAIL（{tool} exit={proc.returncode}，"
                        f"产物 {[p.name for p in produced]}）"
                    )
                    continue
                digest = hashlib.sha256(produced[0].read_bytes()).hexdigest()
                if digest != SOURCE_TEX_SHA256:
                    results.append(
                        f"live[{archive.name}]: FAIL（解出内容 sha256 与源不符）"
                    )
                else:
                    results.append(f"live[{archive.name}]: OK（{tool} 解出源内容一致）")
            elif kind == "must_fail":
                if proc.returncode == 0:
                    results.append(
                        f"live[{archive.name}]: FAIL（{tool} 竟解压成功，拒绝路径失效）"
                    )
                else:
                    results.append(f"live[{archive.name}]: OK（{tool} 解压失败=拒绝）")
            else:
                if proc.returncode != 0 or not produced:
                    results.append(
                        f"live[{archive.name}]: FAIL（{tool} exit={proc.returncode}）"
                    )
                else:
                    results.append(
                        f"live[{archive.name}]: OK（{tool} 解出 {len(produced)} 个文件）"
                    )


def main() -> int:
    if not FIXTURE_DIR.is_dir():
        print(f"FAIL: fixtures 目录不存在: {FIXTURE_DIR}")
        return 1
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    unexpected = sorted(
        p.name for p in FIXTURE_DIR.glob("*.rar") if p.name not in EXPECTATIONS
    )
    if unexpected:
        print(f"FAIL: 入库样本缺少期望表条目（请同步 EXPECTATIONS）: {unexpected}")
        return 1

    failed = False
    for name, exp in EXPECTATIONS.items():
        path = FIXTURE_DIR / name
        fails: list[str] = []
        if not path.is_file():
            print(f"{name}: FAIL（文件缺失）")
            failed = True
            continue
        size = path.stat().st_size
        if size > MAX_FIXTURE_BYTES:
            fails.append(f"体积 {size} 字节超过入库上限 {MAX_FIXTURE_BYTES}")
        data = path.read_bytes()
        if exp.fmt == "rar4" and not data.startswith(RAR4_MARKER):
            fails.append("缺少 RAR4 魔数")
        if exp.fmt == "rar5" and not data.startswith(RAR5_MARKER):
            fails.append("缺少 RAR5 魔数")
        arc = parse_rar4(data) if data.startswith(RAR4_MARKER) else None
        arc5 = parse_rar5(data) if data.startswith(RAR5_MARKER) else None
        if arc is None and arc5 is None:
            fails.append("无法按 RAR4/RAR5 结构解析")
        else:
            getattr(sys.modules[__name__], exp.check)(arc, arc5, fails)
        _run_prescan(path, exp.prescan == "reject", fails)
        status = "FAIL" if fails else "OK"
        detail = f"（{exp.description}，{size} 字节）"
        print(f"{name}: {status}{detail}")
        for msg in fails:
            print(f"    - {msg}")
            failed = True

    live_results: list[str] = []
    _live_extraction_checks(live_results)
    for line in live_results:
        print(line)
        if ": FAIL" in line:
            failed = True

    print("verify_fixtures:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
