"""
多用户模式上传解压管线 —— rar 外部解压器子系统（多用户 v2 P2 / 回退层）。

从 upload.py 拆分而来（该文件越过单文件健康边界）：本模块只承载 rar 相关的
选择层、命令构造、文件名归一化、外部进程管线与 RAR4 头链预扫；zip/7z 及
共享校验原样留在 upload.py。

分层与导入方向：
    - 本模块依赖 upload.py 的共享原语（ArchiveError / UploadLimits /
      ExtractedProject / deadline 工具 / 大小写冲突校验 / 采样间隔）；
    - upload.extract_archive 在分发 .rar 时延迟导入本模块的 extract_rar()
      （函数级导入化解双向引用，两侧入口无论谁先加载都能正确初始化）。

rar 解压语义（unar 优先、bsdtar 回退）：
    - rar 无进程内解析库，走外部解压器管线：unar 优先
      （`unar -f -q -D -o <dir> -- <file>`），二进制缺失时回退 bsdtar
      （`bsdtar -x -f <archive> -C <dir>`，libarchive 的 RAR reader 为 BSD
      授权独立实现，Alpine 等无 unar 源的主机可用）；两者皆缺抛
      `rar_extractor_missing`；不跨解压器重试（部分解压状态下的配额/
      限时语义复杂化，且两工具对损坏包判定不同，结果不可复现）
    - stdin 接 /dev/null 防止加密包交互式索要密码挂起，进程组限时击杀
    - 解压前对 RAR4 头链做最小预扫，命中加密 / 分卷标志即确定性拒绝，
      不依赖解压器失败输出的措辞；文件 / 子块 / 服务块统一按块头声明的
      数据区长度整段跳过（恢复记录 RR 等合法服务数据不得误拒），未知
      块或长度异常直接放行交权威复核兜底；RAR5 由输出关键词匹配与
      “零产物”兜底覆盖
    - unar 在 Linux 上会把名字里的 '/' 折叠为 '_'（RAR 归档内部规范分隔符
      是反斜杠），因此正常 RAR 包的子目录结构不受影响；GBK 等非 UTF-8 文件名
      依赖 unar 字符集探测。bsdtar/libarchive 不做字符集探测，非 UTF-8 名以
      surrogateescape 字节落盘，故 bsdtar 分支在进程成功后执行
      `normalize_rar_names()` 按 utf-8 → gbk → cp437 链原地归一化，
      防 lone surrogate 使文件树 JSON 序列化失败（与 zip 管线解码链一致）
"""

import os
import shutil
import signal
import struct
import subprocess
import threading
import time
from pathlib import Path

from backend.core.logger import get_logger
from .quota import dir_size
from .upload import (
    ArchiveError,
    ExtractedProject,
    UploadLimits,
    _check_deadline,
    _deadline,
    _validate_no_case_conflicts,
    _WATCH_INTERVAL_SECONDS,
)

logger = get_logger("multiuser.upload.rar")

_RAR4_MARKER = b"Rar!\x1a\x07\x00"
_RAR5_MARKER = b"Rar!\x1a\x07\x01\x00"

# RAR4 标志位（与 unrar headers.hpp 对应，已经真实 lsar/unar 对照验证）
_RAR4_MAIN_VOLUME = 0x0001  # MHD_VOLUME：多卷集合（分卷确定性拒绝）
_RAR4_MAIN_PASSWORD = 0x0080  # MHD_PASSWORD：整包头加密
# 注意 0x0040 是 MHD_PROTECT（恢复记录），合法包常见，不得误拒
_RAR4_FILE_PROTECTED = 0x0007  # LHD_SPLIT_BEFORE|AFTER|PASSWORD 掩码
_RAR4_FILE_LARGE = 0x0100  # LHD_LARGE：数据区超 u32（HIGH_PACK_SIZE 扩展）

# 带数据区的 RAR4 块类型：块头固定段之后的第一个 u32 即数据区字节数
# （FILE_HEAD 为 PACK_SIZE；SUB_HEAD / SERVICE——NTFS 流、注释、恢复
# 记录 RR 等——为 ADD_SIZE），三类布局一致，预扫可统一整段跳过
_RAR4_DATA_BLOCK_TYPES = (0x74, 0x78, 0x7A)
_RAR4_ENDARC_TYPE = 0x7B

# 解压器失败输出中的受保护包特征词（大小写不敏感匹配，unar/bsdtar 输出
# 通用；仅影响错误码细分，未命中也按 invalid_archive 拒绝，不影响安全性）
_RAR_PROTECTED_KEYWORDS = ("password", "encrypt", "volume")


def resolve_rar_extractor() -> tuple[str, str]:
    """可用性选择层：unar 优先，缺失时回退 bsdtar，皆缺抛明确错误。

    返回 (解压器名, 二进制绝对路径)。双工具并存环境恒选 unar（生产已
    验证且容错更宽），行为与回退层引入前逐字节一致。"""
    unar = shutil.which("unar")
    if unar is not None:
        return "unar", unar
    bsdtar = shutil.which("bsdtar")
    if bsdtar is not None:
        return "bsdtar", bsdtar
    raise ArchiveError(
        "rar_extractor_missing",
        "Server has no usable rar extractor (unar or bsdtar); "
        "cannot extract rar archives",
    )


def _build_unar_cmd(binary: str, archive_path: Path, extract_dir: Path) -> list[str]:
    """构造 unar 命令（现状不变）：-D 保持根归一化语义。"""
    return [
        binary,
        "-f",
        "-q",
        "-D",
        "-o",
        str(extract_dir),
        "--",
        str(archive_path),
    ]


def _build_bsdtar_cmd(binary: str, archive_path: Path, extract_dir: Path) -> list[str]:
    """构造 bsdtar 命令：`bsdtar -x -f <abs_archive> -C <abs_dir>`。

    归档与目录都用绝对路径；禁止 `-P`（会保留成员名的绝对路径语义），
    不使用任何 insecure 类开关。libarchive 默认剥离前导 '/' 并对 '..'
    有内建防护，但管线不依赖此声明——解压后 _validate_extracted 复核
    仍是唯一权威兜底。"""
    return [
        binary,
        "-x",
        "-f",
        str(Path(archive_path).resolve()),
        "-C",
        str(Path(extract_dir).resolve()),
    ]


# 命令构造按解压器名分发（模块级注册表，供测试与本仓 tools 脚本复用，
# 避免在管线外复制粘贴命令行拼装）
RAR_EXTRACTOR_CMD_BUILDERS = {
    "unar": _build_unar_cmd,
    "bsdtar": _build_bsdtar_cmd,
}


# bsdtar 分支文件名解码链：与 zip 管线的 UTF-8 标志优先 → GBK 回退 →
# cp437 兜底语义对齐（模块常量便于测试注入，运行期不修改）
RAR_NAME_DECODE_CHAIN = ("utf-8", "gbk", "cp437")


def normalize_rar_names(extract_dir: Path) -> None:
    """bsdtar 分支专用：把 surrogateescape 落盘的原始字节名归一化为文本名。

    libarchive 不做字符集探测，非 UTF-8 的 RAR 原始字节名经 Python
    surrogateescape 落盘（如 GBK 名落盘为 `\\xd6\\xd0\\xce\\xc4.txt`），
    lone surrogate 直接进 JSON 响应会使 UTF-8 序列化失败。处理策略：
    自底向上遍历全部条目，名字含 surrogate 者回收原始字节并按
    utf-8 → gbk → cp437 链解码，成功则 bytes 路径原地改名；目标名已
    存在或全链解码失败按 invalid_archive 拒绝（宁可拒收，不入库坏名）。

    实现要点：
    - 改名必须自底向上：os.scandir 后序收集保证子目录条目先于父目录
      入列，再按路径长度降序处理——后代路径恒比祖先长，深层必先改名，
      父目录改名后不会残留失效的子路径前缀；合法 UTF-8 名为 no-op；
    - 用 os.scandir 显式遍历且不跟随符号链接目录：本步骤先于
      _validate_extracted 的符号链接拒绝执行，绝不沿链接走到解压根外；
    - 全程 bytes 路径操作，绕过 str/surrogate 边界；
    - 仅 bsdtar 分支调用（unar 自带字符集探测，无需遍历开销）。

    GBK 字节对覆盖面广可能误还原，与 zip 管线同源的已知限制，接受
    同等级风险。"""

    def _decode_raw_name(raw: bytes) -> str | None:
        for enc in RAR_NAME_DECODE_CHAIN:
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return None

    def _collect_bottom_up(dir_b: bytes, out: list[bytes]) -> None:
        """后序收集目录树条目（bytes 路径）；不递进符号链接目录。"""
        try:
            with os.scandir(dir_b) as it:
                children = [
                    (entry.name, entry.is_dir(follow_symlinks=False)) for entry in it
                ]
        except OSError:
            return  # 条目消失 / 权限等：留待权威复核兜底
        for name_b, is_dir in children:
            full_b = os.path.join(dir_b, name_b)
            if is_dir:
                _collect_bottom_up(full_b, out)
            out.append(full_b)

    all_paths: list[bytes] = []
    _collect_bottom_up(os.fsencode(extract_dir), all_paths)
    for full_b in sorted(all_paths, key=len, reverse=True):
        name_b = os.path.basename(full_b)
        name = os.fsdecode(name_b)
        try:
            name.encode("utf-8")
            continue  # 合法 Unicode 名（无 lone surrogate）：no-op
        except UnicodeEncodeError:
            pass
        decoded = _decode_raw_name(name_b)
        if decoded is None or decoded == name:
            raise ArchiveError(
                "invalid_archive",
                "Undecodable non-UTF-8 rar member name "
                f"({name_b!r}) extracted by bsdtar",
            )
        target_b = os.path.join(os.path.dirname(full_b), os.fsencode(decoded))
        if os.path.lexists(target_b):
            raise ArchiveError(
                "invalid_archive",
                f"Normalized member name conflicts with existing entry: {decoded}",
            )
        logger.info(
            "Normalizing rar member name extracted by bsdtar: %r -> %r",
            name,
            decoded,
        )
        os.rename(full_b, target_b)


def extract_rar(
    archive_path: Path, extract_dir: Path, limits: UploadLimits
) -> ExtractedProject:
    """通过外部解压器（unar 优先，缺失回退 bsdtar）解压 rar 到 extract_dir。

    - 两个二进制都缺失 → `rar_extractor_missing` 明确报错（部署层负责
      安装其一：Debian 系 `apt install unar`，Alpine 系 `apk add
      libarchive-tools`）；
    - stdin 关闭防交互索要密码挂起；start_new_session 使进程组可整体击杀；
    - 监视线程周期统计写盘量，超剩余配额或超限时 SIGKILL 进程组硬中断；
    - bsdtar 分支成功退出后先做文件名归一化再进入后续扫描；
    - 成功但零产物视为多卷集静默失败，按 invalid_archive 拒绝。
    返回的 size 为 0 占位——外层 extract_archive 统一用 _validate_extracted
    的复核结果覆盖。"""
    extractor, binary = resolve_rar_extractor()
    build_cmd = RAR_EXTRACTOR_CMD_BUILDERS[extractor]
    logger.info("rar extractor selected: %s (%s)", extractor, binary)
    deadline = _deadline(limits)
    _check_deadline(deadline)

    # 解压前预扫 RAR4 头链：加密 / 分卷标志确定性拒绝（不解密任何受保护包）
    reject_protected_rar(archive_path, max_blocks=limits.max_files * 4 + 1024)

    try:
        proc = subprocess.Popen(
            build_cmd(binary, archive_path, extract_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as e:
        raise ArchiveError("invalid_archive", f"Cannot run {extractor}: {e}")

    stop = threading.Event()
    breach: list[str] = []
    watcher = _start_rar_watch(proc, extract_dir, limits, deadline, stop, breach)
    stdout_data = b""
    stderr_data = b""
    try:
        remaining = deadline - time.monotonic()
        try:
            stdout_data, stderr_data = proc.communicate(timeout=max(remaining, 0.0))
        except subprocess.TimeoutExpired:
            logger.warning("Aborting rar extraction mid-flight: timeout")
            breach.append("invalid_archive")
            _kill_process_group(proc)
            proc.communicate()
    finally:
        stop.set()
        watcher.join(timeout=5)

    # breach 仅在进程确以非零退出（被击杀 / 自身报错）时采信：
    # returncode==0 说明解压器已在预算内正常完成，此时 watcher 在完成前后
    # 一个采样 tick 内的过期采样属误报——真实超配额由外层 _validate_extracted
    # 以最终落盘量权威复核，不受此放行影响
    if breach and proc.returncode != 0:
        if breach[0] == "quota_exceeded":
            raise ArchiveError(
                "quota_exceeded", "Extracted size exceeds remaining project quota"
            )
        raise ArchiveError("invalid_archive", "Archive extraction timed out")

    if proc.returncode != 0:
        text = (
            stdout_data.decode("utf-8", "ignore")
            + "\n"
            + stderr_data.decode("utf-8", "ignore")
        ).lower()
        if any(kw in text for kw in _RAR_PROTECTED_KEYWORDS):
            raise ArchiveError(
                "archive_unsafe",
                "Encrypted/volume rar archives are not allowed",
            )
        logger.info(
            "%s failed (exit=%s) for %s: %s",
            extractor,
            proc.returncode,
            archive_path.name,
            " ".join(text.split())[:300],  # 压平换行，避免日志注入多行
        )
        raise ArchiveError("invalid_archive", "Invalid or corrupt rar archive")

    # bsdtar/libarchive 不做字符集探测：非 UTF-8 原始字节名以 surrogateescape
    # 落盘，须先归一化为合法 Unicode 名（unar 自带探测无需此步），否则
    # lone surrogate 会使文件树接口的 JSON 序列化失败
    if extractor == "bsdtar":
        normalize_rar_names(extract_dir)

    # 大小写不敏感重名复核：zip/7z 在解压前的名字清单上校验，rar 没有进程内
    # 清单，对解压产物做等价后置扫描——既避免 Linux 上歧义路径对入库，也防
    # data 目录落在大小写不敏感卷（macOS/Windows）时后写条目静默覆盖先写条目
    names = [str(p.relative_to(extract_dir)) for p in extract_dir.rglob("*")]
    _validate_no_case_conflicts(names)

    # 多卷集第一卷被单独上传时（暂存区无兄弟分卷），解压器可能静默零产物退出；
    # 合法空包对项目上传同样无意义，统一拒绝
    file_count = sum(1 for p in extract_dir.rglob("*") if p.is_file())
    if file_count == 0:
        raise ArchiveError(
            "invalid_archive",
            "No extractable content (possibly an unsupported multi-volume set)",
        )
    return ExtractedProject(root=extract_dir, size=0, file_count=file_count)


def _kill_process_group(proc: subprocess.Popen) -> None:
    """SIGKILL 整个解压器进程组（Popen 用 start_new_session 建独立进程组）。"""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        pass  # 进程已退出 / 权限不足等：以 communicate 的返回为准


def _start_rar_watch(
    proc: subprocess.Popen,
    extract_dir: Path,
    limits: UploadLimits,
    deadline: float,
    stop: threading.Event,
    breach: list[str],
) -> threading.Thread:
    """后台监视 rar 解压（unar/bsdtar）的实时写盘量与限时。

    周期采样 extract_dir 已写盘量（符号链接不计），超过剩余配额或超过
    限时即 SIGKILL 进程组硬中断解压，并把错误码追加到 breach。"""

    def watch() -> None:
        while not stop.wait(_WATCH_INTERVAL_SECONDS):
            used = dir_size(extract_dir)
            if used > limits.remaining_quota_bytes:
                code = "quota_exceeded"
            elif time.monotonic() > deadline:
                code = "invalid_archive"
            else:
                continue
            breach.append(code)
            logger.warning(
                "Aborting rar extraction mid-flight: %s (written=%d)", code, used
            )
            _kill_process_group(proc)
            return

    thread = threading.Thread(target=watch, name="rar-extract-watch", daemon=True)
    thread.start()
    return thread


def reject_protected_rar(archive_path: Path, max_blocks: int = 65536) -> None:
    """RAR4 头链最小预扫：命中加密 / 分卷标志即拒绝。

    只读头部字段、不解压任何内容。对带数据区的块（FILE_HEAD 0x74 /
    SUB_HEAD 0x78 / SERVICE 0x7A），统一按「块头 body 起始的 u32」
    整段跳过数据区——否则 pos 会落进恢复记录（MHD_PROTECT 合法包
    常见）等高熵数据中随机游走，把随机字节误判成受保护标志，造成
    合法归档被误拒。遇到 ENDARC、其他未知块类型或任何长度异常时
    直接安全返回放行，交由后续解压器（unar/bsdtar）实际解压与
    _validate_extracted 权威复核兜底，绝不在数据字节流上盲走。

    其余语义保持不变：RAR5 归档不做预扫（由输出关键词匹配与零产物
    兜底覆盖）；SFX 前缀等非常规布局同样跳过预扫；主头 MHD_VOLUME /
    MHD_PASSWORD 与文件头 LHD_SPLIT_BEFORE|AFTER|PASSWORD 确定性
    拒绝；LHD_LARGE（数据区超 u32 的 ≥4GiB 包，本就无法通过上传体积
    上限）按长度异常安全放行。

    max_blocks 限定头链遍历块数（合法归档 ≈ 文件数 + 少量服务头），
    防止构造的超小块垃圾包把预扫变成分钟级 CPU 空转；超限按
    invalid_archive 快速拒绝。"""
    try:
        total_size = archive_path.stat().st_size
        with open(archive_path, "rb") as fp:
            if fp.read(len(_RAR4_MARKER)) != _RAR4_MARKER:
                return  # 非 RAR4 魔数（含 RAR5）：交由解压器判定
            main = fp.read(7)
            # 相对主头起点：CRC(0..1)、TYPE(2)、FLAGS(3..4)、SIZE(5..6)
            if len(main) < 7 or main[2] != 0x73:
                return  # 主头缺失/异常：不在预扫层面下结论
            main_flags = struct.unpack_from("<H", main, 3)[0]
            if main_flags & (_RAR4_MAIN_VOLUME | _RAR4_MAIN_PASSWORD):
                raise ArchiveError(
                    "archive_unsafe",
                    "Encrypted/volume rar archives are not allowed",
                )
            pos = len(_RAR4_MARKER) + 13  # 主头固定 13 字节
            blocks = 0
            while 0 < pos < total_size:
                blocks += 1
                if blocks > max_blocks:
                    raise ArchiveError(
                        "invalid_archive",
                        "rar header chain too complex; archive rejected",
                    )
                fp.seek(pos)
                block = fp.read(11)  # 块头 7 字节 + DataSize u32
                if len(block) < 7:
                    return  # 头部截断：交由解压器报无效包
                _, btype, flags, hsize = struct.unpack_from("<HBHH", block)
                if hsize < 7:
                    return  # 结构损坏：交给解压器报无效包
                if btype == _RAR4_ENDARC_TYPE:
                    return
                if btype not in _RAR4_DATA_BLOCK_TYPES:
                    return  # 未知/不带数据区的块类型：不在预扫层面下结论
                if btype == 0x74:  # FILE_HEAD
                    if flags & _RAR4_FILE_PROTECTED:
                        raise ArchiveError(
                            "archive_unsafe",
                            "Encrypted/volume rar archives are not allowed",
                        )
                    if flags & _RAR4_FILE_LARGE:
                        return  # 数据区超预扫 u32 模型：安全放行交权威复核
                if len(block) < 11:
                    return  # DataSize 字段截断：长度异常安全返回
                data_size = int.from_bytes(block[7:11], "little")
                pos += hsize + data_size
    except OSError as e:
        raise ArchiveError("invalid_archive", f"Cannot read rar archive: {e}")
