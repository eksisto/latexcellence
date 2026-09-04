#!/usr/bin/env python3
"""手工构造小型 RAR（v4 格式，store 方式）测试样例。

多用户上传管线 P2 的入库 fixtures 不依赖构建环境安装 rar/unar：
本脚本用纯字节拼装出最小合法 RAR 归档，产物提交进 tests/fixtures/rar/，
由 CI 与开发机上的 unar 真实解压验证。入库后统一跑
`python tools/rar_fixtures/verify_fixtures.py` 做结构 + 预扫 + （有解压器
时的）真实解压对账，防止坏样本静默入库。

用法（在仓库根目录）::

    python tools/rar_fixtures/make_fixtures.py
    python tools/rar_fixtures/make_fixtures.py --verify tests/fixtures/rar/compressed_rar4.rar
    python tools/rar_fixtures/verify_fixtures.py

生成物（本脚本产物均为无压缩 store 方式，体积仅数百字节）：

    layout_flat.rar     直接内容布局（main.tex + notes\\readme.txt）
    layout_top_dir.rar  顶层单目录布局（proj\\main.tex + proj\\refs.bib）
    gbk_name.rar        GBK 编码文件名样例（中文.txt，验证解码行为）
    encrypted.rar       文件头带加密标志（仅验证被拒绝，数据未真正加密）
    volume.part1.rar    多卷集第一卷形态（仅验证被拒绝）
    service_rr.rar      SERVICE(0x7A) 恢复记录块带伪随机数据区 +
                        正常文件（回归：预扫须整段跳过数据区不误拒）
    subhead_ntfs.rar    SUB_HEAD(0x78) 子块（NTFS 流形态）带数据区 +
                        正常文件（同上）

另有真实 rar CLI 生成的压缩 / 加密 / 分卷样本（compressed_rar4.rar、
compressed_rar5.rar、encrypted_rar5.rar、volume4.part1.rar、
volume4.part2.rar）不经本脚本生成，见文末「真实压缩样本」段。

格式要点（RAR 4.x）：
    - 主头固定 13 字节：块头 7（CRC+TYPE+FLAGS+SIZE）+ HighPosAv(2) + PosAv(4)；
    - 文件头 HEAD_SIZE 从 HEAD_CRC 起算 = 块头 7 + 固定段 25 + 名字长度；
    - 数据段紧跟文件头，随后是下一块；
    - 带数据区的三类块（FILE_HEAD 0x74 / SUB_HEAD 0x78 / SERVICE 0x7A）
      的数据区长度都在块头 body 起始的 u32（PACK_SIZE / ADD_SIZE），
      其中 0x7A 与文件头共用同一布局、0x78 为「DataSize + 附加段」；
    - SERVICE / SUB 块必须置 LONG_BLOCK（0x8000）标志声明「本块携带
      PACK_SIZE 长度的数据区」——真实 rar 的 RR 块 flags=0xC000
      （LONG_BLOCK | 0x4000 skip-if-unknown）；缺 LONG_BLOCK 时
      unar/unrar 视为无数据区、随后在数据字节上瞎找下一块直至放弃
      （实测 lsar 列出 0 个成员），只有 bsdtar/libarchive 容忍；
    - RAR 归档内路径分隔符是反斜杠 '\\'（即使类 Unix 平台创建亦如此）——
      用正斜杠会被 unar 当成普通字符折叠为 '_'；
    - 头部 CRC 为 HEAD_TYPE 起至头部末尾的 CRC32 低 16 位。

注意：encrypted.rar / volume.part1.rar 只设置格式标志位用于触发拒绝路径，
不是可正常解密的归档；任何工具都不应尝试解密受保护包。

真实压缩样本（RAR 解压器回退层的遗留验收项，已入库）：
    本脚本只能构造 store（不压缩）样例——压缩流依赖专有算法，无法伪造。
    真实样本用 rarlab rar CLI 生成后入库（自产 .tex 数据，不分发 rar 软件
    本体，无许可负担）。再生成步骤：

        0. 工具版本：rar 5.x / 6.x（默认 RAR4 格式，`-ma5` 切 RAR5）。
           注意 rar 7.x 起 **移除了 `-ma4`**（无法再生成 RAR4 归档），
           Debian 源的 rar 7.11 不可用于 compressed_rar4.rar / volume4.*；
           无 root 环境可把 rarlab tarball 解到私有前缀使用，不入库。
        1. 源内容（两个压缩样本同源；固定 mtime 使再生成头部时间稳定）::

               cat > main.tex <<'EOF'
               \\documentclass{article}
               \\title{Compression Fixture}
               \\author{LaTeXcellence Test Suite}
               \\begin{document}
               \\maketitle
               This sample paragraph is generated for the rar fixture suite.
               TeX typesets the same sentence repeatedly to keep the archive small.
               \\section{Repeat}
               TeX typesets the same sentence repeatedly to keep the archive small.
               \\end{document}
               EOF
               touch -t 202608221200 main.tex

           分卷源 chapter.tex：普通文本压缩率太高，装不进两卷，需用固定
           种子 PRNG 的 base64 verbatim 块把压缩后体积撑到约 1.7KB（恰跨
           `-v1k` 两卷）::

               python3 - <<'EOF'
               import base64, random
               from pathlib import Path
               para = ("TeX typesets the same sentence repeatedly to keep the "
                       "archive small. The quick brown fox jumps over the lazy "
                       "dog while doing so.\\n")
               rng = random.Random(20260905)
               blob = base64.b64encode(rng.randbytes(1200)).decode("ascii")
               lines = "\\n".join(blob[i:i + 76] for i in range(0, len(blob), 76))
               body = ("\\\\documentclass{article}\\n\\\\title{Volume Fixture}\\n"
                       "\\\\begin{document}\\nThis chapter is a repeatable filler "
                       "used to span several rar volumes.\\n" + para * 8 +
                       "% Deterministic pseudo-random payload (seed 20260905), "
                       "compresses poorly\\n% so that the archive actually "
                       "splits into a multi-part set.\\n\\\\begin{verbatim}\\n"
                       + lines + "\\n\\\\end{verbatim}\\n\\\\end{document}\\n")
               Path("chapter.tex").write_bytes(body.encode("utf-8"))
               EOF
               touch -t 202608221200 chapter.tex

           期望指纹（写入 tools/rar_fixtures/verify_fixtures.py 常量，改动
           源内容必须同步）：main.tex 346 字节，CRC32 0x75EB3C4F，
           sha256 fbb50bfc...61d84c12（全值见 verify 脚本）。

               rar a -ma4 -m5 -ep -tsm compressed_rar4.rar main.tex
               rar a -ma5 -m5 -ep -tsm compressed_rar5.rar main.tex
               rar a -ma5 -m5 -ep -tsm -pFixtures2026 encrypted_rar5.rar main.tex
               rar a -ma4 -m5 -ep -tsm -v1k volume4.rar chapter.tex   # → part1/part2

           注：分卷选 RAR4 而非 RAR5——RAR5 分卷第一卷单独上传时 unar
           零产物退出（依赖「成功但零产物」兜底拒绝的弱信号）、bsdtar 报
           截断类错误；RAR4 分卷由预扫 MHD_VOLUME 在解压发起前确定性
           拒绝（双工具一致、不依赖解压器行为）。
        2. 校验：`python tools/rar_fixtures/verify_fixtures.py` 全绿后入库；
           `bsdtar -tf <样本>` 能列出成员属人工复核的加分项。
        3. 入库后在 tests/test_multiuser/test_upload_rar.py 的真实样本用例
           中登记（双工具参数化；加密 RAR5 的拒绝码按工具细分：
           unar 命中 password 关键词 → archive_unsafe，bsdtar 报块头
           尺寸错 → invalid_archive）。
"""

from __future__ import annotations

import random
import struct
import subprocess
import sys
import tempfile
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
SKIP_IF_UNKNOWN = 0x4000  # 旧解析器可跳过的块（真实 rar 的 RR 块两标志齐置）

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


def _verify(path: Path) -> int:
    """`--verify <file.rar>`：入库真实压缩样本前的最小校验（回退层验收用）。

    步骤：① 魔数检查（RAR4/RAR5）；② 复用上传管线预扫确认非受保护包
    （须在仓库根目录运行以导入 backend）；③ 环境有 unar 或 bsdtar 时
    经生产管线同一选择层与命令构造实际解压到临时目录，确认非零产物。
    任一步失败退出码为 1。"""
    if REPO_ROOT.name and str(REPO_ROOT) not in sys.path[:1]:
        sys.path.insert(0, str(REPO_ROOT))
    from backend.core.multiuser import upload_rar  # noqa: E402

    blob = path.read_bytes()
    if blob.startswith(RAR_MARKER):
        fmt = "RAR4"
    elif blob.startswith(b"Rar!\x1a\x07\x01\x00"):
        fmt = "RAR5"
    else:
        print(f"FAIL: {path} 缺少 RAR 魔数")
        return 1
    print(f"magic: {fmt} OK ({len(blob)} bytes)")

    try:
        upload_rar.reject_protected_rar(path)
    except upload_rar.ArchiveError as e:
        print(f"FAIL: 预扫拒绝该归档（code={e.code}）：{e.message}")
        return 1
    print("prescan: not protected OK")

    try:
        extractor, binary = upload_rar.resolve_rar_extractor()
    except upload_rar.ArchiveError:
        print("extract: SKIP（环境无 unar/bsdtar）")
        return 0
    with tempfile.TemporaryDirectory(prefix="rar-verify-") as td:
        tdp = Path(td)
        cmd = upload_rar.RAR_EXTRACTOR_CMD_BUILDERS[extractor](binary, path, tdp)
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            start_new_session=True,
        )
        files = [p for p in tdp.rglob("*") if p.is_file()]
        if proc.returncode != 0 or not files:
            print(f"FAIL: {extractor} 解压失败 (exit={proc.returncode})")
            return 1
        for f in sorted(files):
            print(f"extracted: {f.relative_to(tdp)} ({f.stat().st_size} bytes)")
    print("verify: PASS")
    return 0


def main() -> int:
    if "--verify" in sys.argv[1:]:
        idx = sys.argv.index("--verify")
        if len(sys.argv) != idx + 2 or not sys.argv[idx + 1].lower().endswith(".rar"):
            print(
                "用法: python tools/rar_fixtures/make_fixtures.py --verify <file.rar>\n"
                "（--verify 需要恰好一个 .rar 路径参数；不带 --verify 运行则重新生成 fixtures）"
            )
            return 2
        return _verify(Path(sys.argv[idx + 1]))

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
    # 数据用固定种子的 PRNG 生成，保证产物逐字节可复现。
    # flags 置 LONG_BLOCK | SKIP_IF_UNKNOWN（与真实 rar 的 RR 块一致）：
    # SERVICE 块缺 LONG_BLOCK 时 unar/unrar 视为无数据区，在数据字节上
    # 瞎找下一块直至放弃（实测解出 0 个文件），只有 bsdtar 容忍。
    rr_data = random.Random(20260822).randbytes(1024)
    tex_body = b"\\documentclass{article}\nHello\n"
    rr = bytearray(RAR_MARKER + _main_header())
    rr += _file_header(
        b"RR", rr_data, flags=LONG_BLOCK | SKIP_IF_UNKNOWN, btype=SERVICE_HEAD
    )
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
