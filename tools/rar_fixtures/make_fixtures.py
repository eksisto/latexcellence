#!/usr/bin/env python3
"""手工构造小型 RAR（v4 格式，store 方式）测试样例。

多用户上传管线 P2 的入库 fixtures 不依赖构建环境安装 rar/unar：
本脚本用纯字节拼装出最小合法 RAR 归档，产物提交进 tests/fixtures/rar/，
由 CI 与开发机上的 unar 真实解压验证。

用法（在仓库根目录）::

    python tools/rar_fixtures/make_fixtures.py

生成物（均为无压缩 store 方式，体积仅数百字节）：

    layout_flat.rar     直接内容布局（main.tex + notes\\readme.txt）
    layout_top_dir.rar  顶层单目录布局（proj\\main.tex + proj\\refs.bib）
    gbk_name.rar        GBK 编码文件名样例（中文.txt，验证解码行为）
    encrypted.rar       文件头带加密标志（仅验证被拒绝，数据未真正加密）
    volume.part1.rar    多卷集第一卷形态（仅验证被拒绝）
    service_rr.rar      SERVICE(0x7A) 恢复记录块带伪随机数据区 +
                        正常文件（回归：预扫须整段跳过数据区不误拒）
    subhead_ntfs.rar    SUB_HEAD(0x78) 子块（NTFS 流形态）带数据区 +
                        正常文件（同上）

格式要点（RAR 4.x）：
    - 主头固定 13 字节：块头 7（CRC+TYPE+FLAGS+SIZE）+ HighPosAv(2) + PosAv(4)；
    - 文件头 HEAD_SIZE 从 HEAD_CRC 起算 = 块头 7 + 固定段 25 + 名字长度；
    - 数据段紧跟文件头，随后是下一块；
    - 带数据区的三类块（FILE_HEAD 0x74 / SUB_HEAD 0x78 / SERVICE 0x7A）
      的数据区长度都在块头 body 起始的 u32（PACK_SIZE / ADD_SIZE），
      其中 0x7A 与文件头共用同一布局、0x78 为「DataSize + 附加段」；
    - RAR 归档内路径分隔符是反斜杠 '\\'（即使类 Unix 平台创建亦如此）——
      用正斜杠会被 unar 当成普通字符折叠为 '_'；
    - 头部 CRC 为 HEAD_TYPE 起至头部末尾的 CRC32 低 16 位。

注意：encrypted.rar / volume.part1.rar 只设置格式标志位用于触发拒绝路径，
不是可正常解密的归档；任何工具都不应尝试解密受保护包。
"""

from __future__ import annotations

import random
import struct
import sys
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "tests" / "fixtures" / "rar"

RAR_MARKER = b"Rar!\x1a\x07\x00"

# MAIN_HEAD 类型与标志位
MAIN_HEAD = 0x73
MHD_VOLUME = 0x0001  # 多卷集合
MHD_FIRSTVOLUME = 0x0100  # 标记为第一卷（注意 0x0080 是 MHD_PASSWORD）

# FILE_HEAD 类型、方法与标志位
FILE_HEAD = 0x74
SUB_HEAD = 0x78  # 子块（NTFS 流等）：块头后第一个 u32 为 DataSize
SERVICE_HEAD = 0x7A  # 服务块（恢复记录 RR 等）：与文件头共用同一布局
ENDARC_HEAD = 0x7B
METHOD_STORE = 0x30  # '0' = 存储（不压缩）
LHD_SPLIT_BEFORE = 0x0001  # 文件延续自上一卷
LHD_SPLIT_AFTER = 0x0002  # 文件延续到下一卷
LHD_PASSWORD = 0x0004  # 加密
LONG_BLOCK = 0x8000  # 块头携带 ADD_SIZE(DataSize) 字段

HOST_OS_UNIX = 3

# 固定的 DOS 时间：2026-08-22 12:00:00（避免产物随生成时间漂移）
DOS_FTIME = (2026 - 1980) << 25 | 8 << 21 | 22 << 16 | 12 << 11 | 0 << 5 | 0


def _header_crc(block: bytes) -> int:
    """RAR 头部 CRC：从 TYPE 字节到头部末尾的 CRC32 低 16 位。"""
    return zlib.crc32(block) & 0xFFFF


def _main_header(flags: int = 0) -> bytes:
    """构造归档主头（固定 13 字节）。

    RAR4 主头在块头之后无条件携带 HighPosAv(u16) 与 PosAv(u32) 两个字段，
    HEAD_SIZE 恒为 13；缺省写 0。"""
    body = struct.pack("<BHH", MAIN_HEAD, flags, 13) + struct.pack("<HI", 0, 0)
    return struct.pack("<H", _header_crc(body)) + body


def _endarc() -> bytes:
    body = struct.pack("<BHH", ENDARC_HEAD, 0x4000, 7)
    return struct.pack("<H", _header_crc(body)) + body


def _file_header(
    name_bytes: bytes,
    data: bytes,
    *,
    flags: int,
    host_os: int = HOST_OS_UNIX,
    attr: int = 0o644,
    unp_ver: int = 20,
    btype: int = FILE_HEAD,
) -> bytes:
    """构造文件头（不含数据段）；SERVICE_HEAD(0x7A) 与文件头共用布局。"""
    # HEAD_SIZE 从 HEAD_CRC 起算：块头 7（CRC+TYPE+FLAGS+SIZE）+
    # 固定段 25（PACK/UNP/HOST_OS/FILE_CRC/FTIME/UNP_VER/METHOD/
    # NAME_SIZE/ATTR）+ 文件名 N；unrar 以 CurBlockPos + HEAD_SIZE
    # 定位数据段与下一块。
    head_size = 32 + len(name_bytes)
    body = struct.pack(
        "<BHHIIBIIBBHI",
        btype,
        flags,
        head_size,
        len(data),  # PACK_SIZE
        len(data),  # UNP_SIZE
        host_os,
        zlib.crc32(data) & 0xFFFFFFFF,  # FILE_CRC
        DOS_FTIME,
        unp_ver,
        METHOD_STORE,
        len(name_bytes),
        attr,
    )
    body += name_bytes
    return struct.pack("<H", _header_crc(body)) + body


def _sub_head_header(data_size: int, tag: bytes = b"") -> bytes:
    """构造 SUB_HEAD(0x78) 子块头（不含数据段）。

    块头固定段之后的第一个 u32 即数据区长度（ADD_SIZE/DataSize），
    随后是类型相关附加段；置 LONG_BLOCK 标志与真实 NTFS 流子块一致。"""
    body = struct.pack("<BHH", SUB_HEAD, LONG_BLOCK, 11 + len(tag))
    body += struct.pack("<I", data_size) + tag
    return struct.pack("<H", _header_crc(body)) + body


def build_rar(entries: list[tuple[bytes, bytes]], *, main_flags: int = 0) -> bytes:
    """把 [(name_bytes, content)] 拼装为完整 RAR 字节流。

    name 使用归档内部原始字节；子路径一律用反斜杠分隔（RAR 规范）。"""
    out = bytearray(RAR_MARKER + _main_header(main_flags))
    for name, content in entries:
        if isinstance(content, str):
            content = content.encode("utf-8")
        out += _file_header(name, content, flags=0)
        out += content  # 数据段紧跟文件头之后
    out += _endarc()
    return bytes(out)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    samples: dict[str, bytes] = {
        # 直接内容布局：顶层多个条目，normalize_project_root 应取解压根
        "layout_flat.rar": build_rar(
            [
                (b"main.tex", b"\\documentclass{article}\nHello\n"),
                (b"notes\\readme.txt", b"flat layout\n"),
            ]
        ),
        # 顶层单目录布局：唯一顶层目录应被归一化为项目根
        "layout_top_dir.rar": build_rar(
            [
                (b"proj\\main.tex", b"\\documentclass{article}\nWorld\n"),
                (b"proj\\refs.bib", b"@misc{x, title={X}}\n"),
            ]
        ),
        # GBK 文件名：原始字节名，验证 unar 的字符集探测行为
        "gbk_name.rar": build_rar([("中文.txt".encode("gbk"), b"gbk name\n")]),
    }

    # 加密样例：文件头置加密标志（仅触发拒绝路径，不做真实加密）
    enc = bytearray(RAR_MARKER + _main_header())
    enc += _file_header(b"secret.tex", b"classified\n", flags=LHD_PASSWORD)
    enc += _endarc()
    samples["encrypted.rar"] = bytes(enc)

    # 分卷样例：主头声明多卷且为第一卷，文件延续到下一卷（缺下一卷必被拒），
    # 与真实多卷集第一卷的形态一致
    vol = bytearray(RAR_MARKER + _main_header(MHD_VOLUME | MHD_FIRSTVOLUME))
    vol += _file_header(b"data.bin", b"\x00" * 32, flags=LHD_SPLIT_AFTER)
    vol += b"\x00" * 32
    vol += _endarc()
    samples["volume.part1.rar"] = bytes(vol)

    # 恢复记录样例：SERVICE(0x7A) 服务块与文件头共用布局（名字 RR），
    # 带伪随机数据区——回归「预扫须按 DataSize 整段跳过数据区」，
    # 数据用固定种子的 PRNG 生成，保证产物逐字节可复现
    rr_data = random.Random(20260822).randbytes(1024)
    tex_body = b"\\documentclass{article}\nHello\n"
    rr = bytearray(RAR_MARKER + _main_header())
    rr += _file_header(b"RR", rr_data, flags=0, btype=SERVICE_HEAD)
    rr += rr_data  # 数据段紧跟服务头之后
    rr += _file_header(b"main.tex", tex_body, flags=0)
    rr += tex_body
    rr += _endarc()
    samples["service_rr.rar"] = bytes(rr)

    # NTFS 子块样例：SUB_HEAD(0x78) 带 DataSize 数据区，同样验证预扫放行
    ntfs_data = random.Random(1).randbytes(256)
    sub = bytearray(RAR_MARKER + _main_header())
    sub += _sub_head_header(len(ntfs_data))
    sub += ntfs_data  # 数据段紧跟子块头之后
    sub += _file_header(b"a.tex", b"x", flags=0)
    sub += b"x"
    sub += _endarc()
    samples["subhead_ntfs.rar"] = bytes(sub)

    for fname, blob in samples.items():
        target = OUT_DIR / fname
        target.write_bytes(blob)
        print(f"wrote {target.relative_to(REPO_ROOT)} ({len(blob)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
