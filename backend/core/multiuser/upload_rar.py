"""
多用户模式上传解压管线 —— rar 外部解压器子系统（多用户 v2 P2 / 回退层 /
部署收口阶段一沙箱化）。

从 upload.py 拆分而来（该文件越过单文件健康边界）：本模块只承载 rar 相关的
选择层、命令构造、文件名归一化、沙箱执行管线与 RAR4 头链预扫；zip/7z 及
共享校验原样留在 upload.py。

分层与导入方向：
    - 本模块依赖 upload.py 的共享原语（ArchiveError / UploadLimits /
      ExtractedProject / deadline 工具 / 大小写冲突校验 / 采样间隔）；
    - upload.extract_archive 在分发 .rar 时延迟导入本模块的 extract_rar()
      （函数级导入化解双向引用，两侧入口无论谁先加载都能正确初始化）；
    - 解压命令经 backend/utils/sandbox 的 SandboxRunner 执行（部署收口
      阶段一）：convert 镜像内置 unar / bsdtar，无网络、只读根文件系统、
      资源限额的安全基线与 latexmk / pandoc 等外部命令对齐。

rar 解压语义（unar 优先、bsdtar 回退）：
    - rar 无进程内解析库，走外部解压器管线：local 后端按宿主 PATH 探测
      （unar 优先，缺失回退 bsdtar；两者皆缺抛 `rar_extractor_missing`）；
      docker 后端不探测容器内 PATH，按 unar 优先的固定顺序取注册表
      builder（镜像保证两者齐备）；不跨解压器重试（部分解压状态下的配额/
      限时语义复杂化，且两工具对损坏包判定不同，结果不可复现）
    - 命令以 ExecSpec 描述（argv 形式、无 stdin 契约）：压缩包以只读方式
      bind mount 进容器（同绝对路径契约），解压目录（暂存区）以可写方式
      挂载为工作目录；加密包无法交互式索要密码（stdin 关闭），挂起不可能
    - mid-flight 拦截（超配额 / 超时）由宿主侧 asyncio 监视任务采样
      extract_dir 写盘量实现（extract_dir 经 bind mount 对宿主可见），
      越限置位 cancel_event，docker 后端杀容器、local 后端击杀进程组
    - 解压前对 RAR4 头链做最小预扫（宿主进程、纯 Python），命中加密 /
      分卷标志即确定性拒绝，不依赖解压器失败输出的措辞；文件 / 子块 /
      服务块统一按块头声明的数据区长度整段跳过（恢复记录 RR 等合法服务
      数据不得误拒），未知块或长度异常直接放行交权威复核兜底；RAR5 由
      输出关键词匹配与“零产物”兜底覆盖
    - unar 在 Linux 上会把名字里的 '/' 折叠为 '_'（RAR 归档内部规范分隔符
      是反斜杠），因此正常 RAR 包的子目录结构不受影响；GBK 等非 UTF-8 文件名
      依赖 unar 字符集探测。bsdtar/libarchive 不做字符集探测，非 UTF-8 名以
      surrogateescape 字节落盘，故 bsdtar 分支在执行成功后执行
      `normalize_rar_names()` 按 utf-8 → gbk → cp437 链原地归一化，
      防 lone surrogate 使文件树 JSON 序列化失败（与 zip 管线解码链一致）
    - 解压暂存、文件名归一化、大小写冲突扫描、零产物兜底等安全复核全部
      留在宿主进程（纯 Python），在沙箱执行前后照常运行
"""

import asyncio
import contextlib
import os
import shutil
import struct
import time
from pathlib import Path

from backend.core.logger import get_logger
from backend.utils.sandbox import (
    ExecResult,
    ExecSpec,
    SandboxMount,
    current_sandbox_user_id,
    get_sandbox_runner,
)

from .quota import dir_size
from .upload import (
    _WATCH_INTERVAL_SECONDS,
    ArchiveError,
    ExtractedProject,
    UploadLimits,
    _check_deadline,
    _deadline,
    _validate_no_case_conflicts,
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
    """local 后端专用的可用性选择层：探测宿主 PATH（unar 优先，缺失回退
    bsdtar，皆缺抛明确错误），返回 (解压器名, 二进制绝对路径)。

    仅宿主语义：docker 后端不探测容器内 PATH（镜像保证 unar/bsdtar 齐备，
    见 `_select_rar_extractor`）。双工具并存环境恒选 unar（生产已验证且
    容错更宽），行为与回退层引入前逐字节一致。"""
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


# docker 后端的固定选择顺序（不探测容器内 PATH；convert 镜像保证齐备）
_DOCKER_EXTRACTOR_PREFERENCE = ("unar", "bsdtar")


def _select_rar_extractor(backend: str) -> tuple[str, str]:
    """按执行后端选择解压器，返回 (解压器名, 命令首参)。

    - local：宿主 PATH 探测（`resolve_rar_extractor`，二进制缺失抛
      `rar_extractor_missing`——该错误码仅 local 后端产生）；
    - docker：按固定优先序取注册表 builder，命令首参为裸二进制名（由
      容器 PATH 解析），不做任何宿主探测。"""
    if backend == "local":
        return resolve_rar_extractor()
    for name in _DOCKER_EXTRACTOR_PREFERENCE:
        if name in RAR_EXTRACTOR_CMD_BUILDERS:
            return name, name
    raise ArchiveError(
        "rar_extractor_missing",
        "No rar extractor registered for the sandbox image",
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
    """经 SandboxRunner 解压 rar 到 extract_dir（unar 优先，bsdtar 回退）。

    - local 后端：宿主两个二进制都缺失 → `rar_extractor_missing` 明确报错
      （部署层负责安装其一：Debian 系 `apt install unar`，Alpine 系
      `apk add libarchive-tools`）；docker 后端由 convert 镜像保证齐备，
      daemon 不可达时按 sandbox_unavailable → 503 硬失败（mode=auto 探测
      失败自动回落 local，行为与沙箱化前一致）；
    - 命令以 ExecSpec 描述（argv、无 stdin），压缩包只读挂载、解压目录
      可写挂载；mid-flight 超配额 / 超时由宿主侧监视任务置位 cancel_event
      硬中断（docker 杀容器 / local 击杀进程组）；
    - deadline 折算为 spec.timeout_seconds + ExecResult.timed_out，错误码
      与用户文案不变；
    - bsdtar 分支成功退出后先做文件名归一化再进入后续扫描；
    - 成功但零产物视为多卷集静默失败，按 invalid_archive 拒绝。
    返回的 size 为 0 占位——外层 extract_archive 统一用 _validate_extracted
    的复核结果覆盖。"""
    deadline = _deadline(limits)
    _check_deadline(deadline)

    # 解压前预扫 RAR4 头链（宿主进程、纯 Python）：加密 / 分卷标志确定性拒绝
    reject_protected_rar(archive_path, max_blocks=limits.max_files * 4 + 1024)

    # 本函数保持同步签名（upload.extract_archive 的同步核心经
    # asyncio.to_thread 调度，工作线程内无事件循环）：解压执行段在此线程内
    # 建立独立事件循环跑完（runner 的并发闸门与探测锁均跨循环安全）
    extractor, result, breach = asyncio.run(
        _extract_via_sandbox(archive_path, extract_dir, limits, deadline)
    )

    # breach 仅在进程确以非零退出（被取消 / 自身报错）时采信：
    # returncode==0 说明解压器已在预算内正常完成，此时监视任务在完成前后
    # 一个采样 tick 内的过期采样属误报——真实超配额由外层 _validate_extracted
    # 以最终落盘量权威复核，不受此放行影响
    if breach and result.returncode != 0:
        if breach[0] == "quota_exceeded":
            raise ArchiveError(
                "quota_exceeded", "Extracted size exceeds remaining project quota"
            )
        raise ArchiveError("invalid_archive", "Archive extraction timed out")

    if result.timed_out:
        raise ArchiveError("invalid_archive", "Archive extraction timed out")

    if result.returncode != 0:
        text = (result.stdout + "\n" + result.stderr).lower()
        if any(kw in text for kw in _RAR_PROTECTED_KEYWORDS):
            raise ArchiveError(
                "archive_unsafe",
                "Encrypted/volume rar archives are not allowed",
            )
        logger.info(
            "%s failed (exit=%s) for %s: %s",
            extractor,
            result.returncode,
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


async def _extract_via_sandbox(
    archive_path: Path,
    extract_dir: Path,
    limits: UploadLimits,
    deadline: float,
) -> tuple[str, ExecResult, list[str]]:
    """异步编排：选后端 → 选解压器 → 构造 ExecSpec → 监视任务 + 沙箱执行。

    返回 (解压器名, ExecResult, breach)。breach 由监视任务在置位
    cancel_event 前写入（同一事件循环内顺序可见，无竞态）。"""
    runner = get_sandbox_runner()
    backend = await runner.resolve_backend("convert")
    extractor, binary = _select_rar_extractor(backend)
    logger.info(
        "rar extractor selected: %s (%s) | sandbox backend=%s",
        extractor,
        binary,
        backend,
    )

    # 归档与解压目录统一取真实绝对路径：docker 后端按同绝对路径契约 bind
    # mount，命令参数（bsdtar 的 -f/-C 等）与挂载源必须指向同一路径，
    # resolve() 消除路径中符号链接组件在宿主与 daemon 视角下的分歧
    archive_real = Path(archive_path).resolve()
    dir_real = Path(extract_dir).resolve()
    command = RAR_EXTRACTOR_CMD_BUILDERS[extractor](binary, archive_real, dir_real)
    remaining = deadline - time.monotonic()
    spec = ExecSpec(
        command=command,
        cwd=str(dir_real),  # 解压目录：默认 rw 挂载进容器
        image_kind="convert",
        mounts=[SandboxMount(host_path=str(archive_real), read_only=True)],
        # 既有 deadline 折算为执行超时（ExecSpec 要求 >0；临界过期场景由
        # 后端立即超时返回 timed_out，错误码与文案不变）
        timeout_seconds=max(remaining, 0.05),
    )

    cancel_event = asyncio.Event()
    breach: list[str] = []
    watcher = asyncio.ensure_future(
        _rar_watch_task(extract_dir, limits, deadline, cancel_event, breach)
    )
    try:
        result = await runner.execute(
            spec,
            user_id=current_sandbox_user_id(),
            cancel_event=cancel_event,
        )
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
    return extractor, result, breach


async def _rar_watch_task(
    extract_dir: Path,
    limits: UploadLimits,
    deadline: float,
    cancel_event: asyncio.Event,
    breach: list[str],
) -> None:
    """宿主侧异步监视任务：周期采样解压目录写盘量，越限置位 cancel_event。

    extract_dir 经 bind mount 对宿主可见，dir_size 采样逻辑与沙箱化前的
    监视线程一致（符号链接不计）；超过剩余配额或超过限时即置位 cancel_event
    （docker 后端杀容器、local 后端击杀进程组），并把错误码追加到 breach。
    采样为同步阻塞遍历，经 asyncio.to_thread 调度避免阻塞事件循环。"""
    while True:
        await asyncio.sleep(_WATCH_INTERVAL_SECONDS)
        used = await asyncio.to_thread(dir_size, extract_dir)
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
        cancel_event.set()
        return


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
