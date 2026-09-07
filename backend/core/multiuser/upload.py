"""
多用户模式上传解压管线核心（纯同步函数层，P3 / P2-rar）。

职责：
    1. 压缩包接收校验：zip / 7z / rar 三种格式的安全解压
    2. 路径穿越 / 符号链接 / 设备文件 / 加密包 / 压缩炸弹 / 编码 / 大小写冲突拦截
    3. 配额约束：解压总量、文件数、单文件大小、压缩比、限时
    4. 项目根目录归一化（兼容“含顶层目录”与“直接内容”两种打包布局）
    5. 原子落位：把归一化后的项目移动到用户空间 `projects/<project_id>/`

约定：
    - 本模块为同步核心；上传路由通过 asyncio.to_thread 调度，禁止阻塞事件循环
    - 所有校验失败以 ArchiveError 表达（携带稳定错误码），由服务层映射 HTTP
    - 解压只写入独立暂存区，任何失败立即中止并交由服务层清理暂存区，
      绝不把未经验证的内容写入用户空间

rar 说明（多用户 v2 P2 / 回退层）：
    - rar 子系统（解压器选择层、外部进程管线、RAR4 预扫、bsdtar 分支
      文件名归一化）已拆分至 `upload_rar.py`；本模块的 extract_archive
      在分发 .rar 时延迟导入其 extract_rar()，安全语义见该模块 docstring
"""

import os
import re
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from backend.core.config import get_config
from backend.core.logger import get_logger

from . import userspace

logger = get_logger("multiuser.upload")

# 时间上限默认（秒）；超过则判定解压超时（防恶意超大/超慢压缩包）
DEFAULT_EXTRACT_TIMEOUT_SECONDS = 120


class ArchiveError(ValueError):
    """上传/解压业务失败（ValueError 子类，复用项目标准异常约定）。

    code 用于前端稳定翻译（invalid_archive / archive_unsafe /
    quota_exceeded / upload_too_large 等），由服务层映射 HTTP。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class UploadLimits:
    """一把解压任务的硬约束（来自配置 + 实时剩余配额）。"""

    max_archive_bytes: int
    max_files: int
    max_file_size_bytes: int
    max_compression_ratio: int
    remaining_quota_bytes: int
    timeout_seconds: int = DEFAULT_EXTRACT_TIMEOUT_SECONDS


@dataclass
class ExtractedProject:
    """解压完成的临时项目信息。"""

    root: Path  # 归一化后的项目根目录（位于暂存区）
    size: int  # 解压后总大小（字节，不含符号链接）
    file_count: int  # 文件数


def build_limits(remaining_quota_bytes: int) -> UploadLimits:
    """从实时配置构造上传限制（不在模块级缓存配置）。"""
    cfg = get_config().multi_user
    return UploadLimits(
        max_archive_bytes=cfg.max_upload_archive_mb * 1024 * 1024,
        max_files=cfg.max_upload_files,
        max_file_size_bytes=cfg.max_upload_file_size_mb * 1024 * 1024,
        max_compression_ratio=cfg.max_compression_ratio,
        remaining_quota_bytes=remaining_quota_bytes,
    )


# ── 入口 ─────────────────────────────────────────────────────────
def extract_archive(
    archive_path: Path, extract_dir: Path, limits: UploadLimits
) -> ExtractedProject:
    """解压 archive_path 到 extract_dir，校验通过后返回 ExtractedProject。

    支持 .zip / .7z / .rar。任何安全问题/限额超限抛 ArchiveError。"""
    if not archive_path.is_file():
        raise ArchiveError("invalid_archive", "Uploaded file is not an archive")
    suffix = archive_path.suffix.lower()
    if suffix == ".zip":
        result = _extract_zip(archive_path, extract_dir, limits)
    elif suffix == ".7z":
        result = _extract_7z(archive_path, extract_dir, limits)
    elif suffix == ".rar":
        # 延迟导入：upload_rar 依赖本模块的共享原语（ArchiveError 等），
        # 函数级导入化解双向引用，两侧入口无论谁先加载都能正确初始化
        from .upload_rar import extract_rar

        result = extract_rar(archive_path, extract_dir, limits)
    else:
        raise ArchiveError(
            "invalid_archive", "Unsupported archive type (only .zip, .7z and .rar)"
        )

    # 解压后兜底复核（大小/文件数/符号链接/越界），三种格式统一执行
    size, count = _validate_extracted(extract_dir, limits)
    if count != result.file_count:
        result.file_count = count
    if size != result.size:
        result.size = size
    root = normalize_project_root(extract_dir)
    return ExtractedProject(root=root, size=result.size, file_count=result.file_count)


# ── zip ──────────────────────────────────────────────────────────
def _extract_zip(
    archive_path: Path, extract_dir: Path, limits: UploadLimits
) -> ExtractedProject:
    deadline = _deadline(limits)
    try:
        zf = zipfile.ZipFile(str(archive_path), "r")
    except (zipfile.BadZipFile, OSError, ValueError) as e:
        raise ArchiveError("invalid_archive", f"Invalid or corrupt zip archive: {e}")
    try:
        infos = zf.infolist()
        names: list[str] = []
        total_uncompressed = 0
        total_compressed = 0
        file_count = 0
        for info in infos:
            if not _is_regular_zinfo(info):
                raise ArchiveError(
                    "archive_unsafe",
                    "Archive contains unsupported entries (symlink/device/special file)",
                )
            name = _decode_zip_name(info)
            rel = _validate_relative_name(name)
            names.append(rel)
            if rel.endswith("/"):
                continue  # 目录条目
            file_count += 1
            size = info.file_size
            csize = max(1, info.compress_size)
            if size > limits.max_file_size_bytes:
                raise ArchiveError(
                    "upload_too_large",
                    f"Single file exceeds size limit: {rel}",
                )
            if info.flag_bits & 0x1:
                raise ArchiveError(
                    "archive_unsafe", "Encrypted zip archives are not allowed"
                )
            _check_ratio(size, csize, limits)
            total_uncompressed += size
            total_compressed += csize
            _check_total(total_uncompressed, limits)
            _check_count(file_count, limits)

        _validate_no_case_conflicts(names)
        if file_count > limits.max_files:
            _check_count(file_count, limits)

        # 开始解压（预检已通过；写盘仍逐文件校验，防头部与实际不符的伪造包）
        actual_written = 0
        extracted_files = 0
        for info, rel in zip(infos, names):
            _check_deadline(deadline)
            target = (extract_dir / rel).resolve()
            _ensure_inside(target, extract_dir)
            if rel.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                src = zf.open(info)
            except RuntimeError as e:
                # 加密/特殊成员在读取时才报错（预检标志位可能缺失）
                raise ArchiveError("archive_unsafe", f"Cannot extract member: {e}")
            with src, open(target, "wb") as dst:
                n = _copy_limited(src, dst, limits, deadline)
                actual_written += n
                extracted_files += 1
            if actual_written > limits.remaining_quota_bytes:
                raise ArchiveError(
                    "quota_exceeded", "Extracted size exceeds remaining project quota"
                )
        return ExtractedProject(
            root=extract_dir, size=actual_written, file_count=extracted_files
        )
    finally:
        zf.close()


def _is_regular_zinfo(info: zipfile.ZipInfo) -> bool:
    """只允许常规文件 / 目录；拒绝符号链接、设备、FIFO、socket。"""
    mode = (info.external_attr >> 16) & 0o170000
    if mode == 0:
        return True  # 无权限位信息视为普通条目
    return mode in (0o100000, 0o040000)


def _decode_zip_name(info: zipfile.ZipInfo) -> str:
    """解码 zip 成员名：UTF-8 标志优先，否则尝试 GBK，再回退 cp437。

    Python 3.x 的 `orig_filename` 是 str（非 UTF-8 标志下以 cp437 解码），
    要尝试 GBK 需先按 cp437 编码回收原始字节，再按 GBK 解码。"""
    flag_utf8 = bool(info.flag_bits & 0x800)
    raw = getattr(info, "orig_filename", None)
    if flag_utf8 or raw is None:
        return _uri_unescape(info.filename)
    try:
        raw_bytes = raw.encode("cp437")
        return _uri_unescape(raw_bytes.decode("gbk"))
    except (UnicodeDecodeError, UnicodeEncodeError):
        return _uri_unescape(info.filename)


_RE_URI_ESC = re.compile(r"%([0-9A-Fa-f]{2})")


def _uri_unescape(name: str) -> str:
    """少数打包工具会对中文名做 %XX 编码，解码后做常规路径校验。"""
    try:
        decoded = _RE_URI_ESC.sub(
            lambda m: bytes([int(m.group(1), 16)]).decode("utf-8"), name
        )
        return decoded
    except (UnicodeDecodeError, ValueError):
        return name


# ── 7z ───────────────────────────────────────────────────────────
def _extract_7z(
    archive_path: Path, extract_dir: Path, limits: UploadLimits
) -> ExtractedProject:
    deadline = _deadline(limits)
    import py7zr

    try:
        fp = open(archive_path, "rb")
    except OSError as e:
        raise ArchiveError("invalid_archive", f"Cannot read 7z archive: {e}")
    try:
        try:
            z = py7zr.SevenZipFile(fp, "r")
        except Exception as e:  # Bad7zFile / py7zr 各类解析失败
            raise ArchiveError("invalid_archive", f"Invalid or corrupt 7z archive: {e}")
        try:
            if z.needs_password():
                raise ArchiveError(
                    "archive_unsafe", "Encrypted/volume 7z archives are not allowed"
                )
            entries = list(z.list())
            names: list[str] = []
            total_uncompressed = 0
            total_compressed = 0
            file_count = 0
            for e in entries:
                if getattr(e, "is_symlink", False) or (
                    not getattr(e, "is_directory", False)
                    and not getattr(e, "archivable", True)
                ):
                    raise ArchiveError(
                        "archive_unsafe",
                        "Archive contains unsupported entries (symlink/device/special file)",
                    )
                fname = getattr(e, "filename", "")
                rel = _validate_relative_name(fname)
                names.append(rel)
                if getattr(e, "is_directory", False):
                    continue
                file_count += 1
                size = int(getattr(e, "uncompressed", 0) or 0)
                raw_csize = int(getattr(e, "compressed", 0) or 0)
                if size > limits.max_file_size_bytes:
                    raise ArchiveError(
                        "upload_too_large", f"Single file exceeds size limit: {rel}"
                    )
                # solid 模式下 py7zr 对单成员报告 compressed=0（压缩数据
                # 在共享 solid 块中无法归属），此时跳过单条目压缩比检查；
                # 解压后的 _validate_extracted 与实时监视线程已提供压缩弹
                # 炸防护
                if raw_csize > 0:
                    _check_ratio(size, max(1, raw_csize), limits)
                total_uncompressed += size
                total_compressed += max(1, raw_csize)
                _check_total(total_uncompressed, limits)
                _check_count(file_count, limits)
            _validate_no_case_conflicts(names)
            if file_count > limits.max_files:
                _check_count(file_count, limits)

            # 解压（预检通过后落盘）。py7zr 整包一次性写出且头部声明可伪造，
            # 无法像 zip 那样逐字节受限拷贝（逐成员提取在 solid 块上又是
            # 二次方复杂度）；由后台监视线程周期统计已写盘总量并执行限时，
            # 超限即关闭底层文件句柄硬中断解压——残余过冲约为一个采样
            # 间隔的写入量（方案 §8.2"实时累计、超限立即中止"）
            _check_deadline(deadline)
            z.reset()
            stop = threading.Event()
            breach: list[str] = []
            watcher = _start_extract_watch(
                fp, extract_dir, limits, deadline, stop, breach
            )
            try:
                z.extract(path=str(extract_dir))
            except Exception as e:
                if breach:
                    if breach[0] == "quota_exceeded":
                        raise ArchiveError(
                            "quota_exceeded",
                            "Extracted size exceeds remaining project quota",
                        )
                    raise ArchiveError(
                        "invalid_archive", "Archive extraction timed out"
                    )
                raise ArchiveError("invalid_archive", f"Corrupt 7z archive: {e}")
            finally:
                stop.set()
                watcher.join(timeout=5)
            size, count = _validate_extracted(extract_dir, limits)
            return ExtractedProject(root=extract_dir, size=size, file_count=count)
        finally:
            try:
                z.close()
            except Exception:
                pass
    finally:
        try:
            fp.close()
        except Exception:
            pass


# 监视线程采样间隔（秒）；间隔内的写入量是超限中止的残余过冲上界
_WATCH_INTERVAL_SECONDS = 1.0


def _sample_written_bytes(extract_dir: Path, remaining_quota_bytes: int) -> int:
    """单趟 os.scandir 统计解压已写盘字节（v2 P4 采样计数器）。

    与 quota.dir_size 同口径（符号链接不计入），用于替代监视线程每秒一次
    的 rglob 全遍历：
    - os.scandir 单趟遍历，条目类型由目录项 d_type 直接给出，每文件只做
      一次必要的 lstat（Path.rglob 需 is_file / is_symlink / stat 多次
      系统调用与对象构造开销）；
    - 累计超过 remaining_quota_bytes 立即短路返回——超限快速路径无需
      完成全树扫描。
    文件系统不提供写入推送通知，最坏复杂度仍与产物规模线性；7z 走 py7zr
    整包写出、解压侧无逐写入回调可挂，这是不引入 OS 钩子前提下每采样周期
    的最小观测开销。"""

    def _walk(stack: list[Path]) -> int:
        total = 0
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for entry in it:
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            total += entry.stat(follow_symlinks=False).st_size
                            if total > remaining_quota_bytes:
                                return total  # 已确定超限：立即短路
                        except OSError:
                            continue
            except OSError:
                continue
        return total

    return _walk([extract_dir])


def _start_extract_watch(
    fp,
    extract_dir: Path,
    limits: UploadLimits,
    deadline: float,
    stop: threading.Event,
    breach: list[str],
) -> threading.Thread:
    """后台监视 7z 解压的实时写盘总量与限时（方案 §8.2）。

    周期采样 extract_dir 已写盘量（符号链接不计；v2 P4 起走
    _sample_written_bytes 采样计数器，不再每秒全量 rglob），超过剩余配额
    或超过限时即关闭归档文件句柄中断解压，并把错误码追加到 breach。"""

    def watch() -> None:
        while not stop.wait(_WATCH_INTERVAL_SECONDS):
            used = _sample_written_bytes(extract_dir, limits.remaining_quota_bytes)
            if used > limits.remaining_quota_bytes:
                code = "quota_exceeded"
            elif time.monotonic() > deadline:
                code = "invalid_archive"
            else:
                continue
            breach.append(code)
            logger.warning(
                "Aborting 7z extraction mid-flight: %s (written=%d)",
                code,
                used,
            )
            try:
                fp.close()
            except OSError:
                pass
            return

    thread = threading.Thread(target=watch, name="extract-watch", daemon=True)
    thread.start()
    return thread


# ── 通用路径/安全校验 ────────────────────────────────────────────
def validate_member_path(name: str) -> str:
    """公开入口：把归档成员/分片清单路径归一化为安全的相对 POSIX 路径。

    供分片上传会话（v2 P5）在 init 时复用同一套穿越/绝对路径/'..' 校验，
    避免外部模块直接访问私有实现。目录名以 '/' 结尾（本模块内部约定）。"""
    return _validate_relative_name(name)


def validate_no_case_conflicts(names: list[str]) -> None:
    """公开入口：大小写不敏感重名（含目录前缀冲突）校验。

    分片上传 directory 会话在 init 时对 manifest 清单做同等校验。"""
    _validate_no_case_conflicts(names)


def finalize_directory_layout(
    extract_dir: Path, limits: UploadLimits
) -> ExtractedProject:
    """把已按 manifest 拼装完成的 extracted 布局收尾为项目根（v2 P5）。

    与 extract_archive 的尾部一致：安全兜底复核（大小/文件数/单文件上限/
    符号链接/越界/剩余配额）→ 项目根归一化。供 directory 分片会话绕过
    压缩包解压、直接走既有校验链。"""
    size, count = _validate_extracted(extract_dir, limits)
    root = normalize_project_root(extract_dir)
    return ExtractedProject(root=root, size=size, file_count=count)


def _validate_relative_name(name: str) -> str:
    """把归档成员名归一化为安全的相对 POSIX 路径（'/' 分隔）。

    拒绝绝对路径、盘符、'..'、空段导致的穿越。目录名以 '/' 结尾。"""
    if "\x00" in name:
        raise ArchiveError(
            "archive_unsafe", "Archive member name contains illegal chars"
        )
    # 统一分隔符（zip 规范用 /，兼容少数用 \\ 的包）
    norm = name.replace("\\", "/")
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
        raise ArchiveError("archive_unsafe", "Archive contains absolute paths")
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if not parts:
        raise ArchiveError("invalid_archive", "Archive contains empty member path")
    if ".." in parts:
        raise ArchiveError("archive_unsafe", "Archive contains '..' path traversal")
    rel = "/".join(parts)
    if norm.endswith("/"):
        rel = rel + "/"
    return rel


def _validate_no_case_conflicts(names: list[str]) -> None:
    """大小写不敏感重名（含目录前缀冲突）直接报错。

    例：README.tex 与 readme.tex；以及 'a' 与 'a/'、'A/b' 与 'a' 等。"""
    seen: dict[str, str] = {}
    for name in names:
        rel = name.rstrip("/")
        parts = rel.split("/")
        # 每个祖先前缀也要检查（防止 'A' 目录与 'a/' 目录冲突）
        prefixes = []
        for i in range(1, len(parts) + 1):
            prefixes.append("/".join(parts[:i]))
        for p in prefixes:
            key = p.casefold()
            if key in seen and seen[key] != p:
                raise ArchiveError(
                    "archive_unsafe",
                    f"Case-insensitive duplicate path in archive: {seen[key]} vs {p}",
                )
            seen[key] = p


def _check_ratio(size: int, csize: int, limits: UploadLimits) -> None:
    if size > csize * limits.max_compression_ratio:
        raise ArchiveError(
            "archive_unsafe",
            "Archive looks like a compression bomb (excessive compression ratio)",
        )


def _check_total(total: int, limits: UploadLimits) -> None:
    if total > limits.remaining_quota_bytes:
        raise ArchiveError(
            "quota_exceeded", "Extracted size exceeds remaining project quota"
        )


def _check_count(count: int, limits: UploadLimits) -> None:
    if count > limits.max_files:
        raise ArchiveError(
            "upload_too_large",
            f"Archive contains too many files (limit {limits.max_files})",
        )


def _ensure_inside(target: Path, root: Path) -> None:
    target_r = target.resolve()
    root_r = root.resolve()
    if not target_r.is_relative_to(root_r):
        raise ArchiveError("archive_unsafe", "Archive member escapes extraction root")


def _copy_limited(
    src, dst, limits: UploadLimits, deadline: float, chunk: int = 1 << 20
) -> int:
    """拷贝并实时累计大小/限时/限流；返回写入字节数。"""
    written = 0
    while True:
        _check_deadline(deadline)
        data = src.read(chunk)
        if not data:
            break
        written += len(data)
        if written > limits.max_file_size_bytes:
            raise ArchiveError(
                "upload_too_large", "Single file exceeds size limit during extraction"
            )
        if written > limits.remaining_quota_bytes:
            raise ArchiveError(
                "quota_exceeded", "Extracted size exceeds remaining project quota"
            )
        dst.write(data)
    return written


def _validate_extracted(extract_dir: Path, limits: UploadLimits) -> tuple[int, int]:
    """解压后兜底复核：总大小、文件数、单文件大小、符号链接、越界。"""
    root = extract_dir.resolve()
    total = 0
    count = 0
    for p in root.rglob("*"):
        if p.is_symlink():
            raise ArchiveError("archive_unsafe", "Extracted content contains a symlink")
        if not p.resolve().is_relative_to(root):
            raise ArchiveError(
                "archive_unsafe", "Extracted content escapes extraction root"
            )
        if p.is_file():
            size = p.stat().st_size
            if size > limits.max_file_size_bytes:
                raise ArchiveError(
                    "upload_too_large",
                    "Single file exceeds size limit after extraction",
                )
            total += size
            count += 1
            if total > limits.remaining_quota_bytes:
                raise ArchiveError(
                    "quota_exceeded",
                    "Extracted size exceeds remaining project quota after extraction",
                )
            if count > limits.max_files:
                _check_count(count, limits)
    return total, count


# ── 布局归一化与落位 ─────────────────────────────────────────────
def normalize_project_root(extract_dir: Path) -> Path:
    """判断项目根目录。

    - 暂存区只有一个顶层条目且为目录 → 该目录为项目根（兼容“打包了上层目录”）
    - 其余情况 → 暂存区本身为项目根（兼容“直接打包项目内容”）
    """
    children = [c for c in extract_dir.iterdir() if not c.name.startswith("__MACOSX")]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return extract_dir


def move_into_user_space(
    extract_dir: Path,
    project_root: Path,
    user_id: str,
    project_id: str,
) -> Path:
    """把归一化后的项目根目录移动到用户空间 projects/<project_id>/，返回目标路径。

    - project_root == extract_dir（直接内容布局）→ 移动其全部子项
    - project_root 为单一子目录 → 整个目录移动为项目根
    移动完成后暂存区残留（含空 extract_dir）由调用方/清理任务清除。
    """
    dest = userspace.project_dir(user_id, project_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if project_root.resolve() == extract_dir.resolve():
        dest.mkdir(parents=True, exist_ok=True)
        for child in list(project_root.iterdir()):
            shutil.move(str(child), str(dest))
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(project_root), str(dest))
    return dest


# ── 计时工具 ─────────────────────────────────────────────────────
def _deadline(limits: UploadLimits) -> float:
    return time.monotonic() + limits.timeout_seconds


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise ArchiveError("invalid_archive", "Archive extraction timed out")
