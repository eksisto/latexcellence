"""python_sandbox 审计钩子防护（安全监听器）。

在沙箱子进程执行用户脚本前，通过 ``sys.addaudithook()`` 安装审计钩子，
对危险操作直接拦截并抛出 ``PermissionError``：

- 读取沙箱外文件（`open` / `os.open` 读模式，及目录枚举 `os.listdir` / `os.scandir`、
  `os.readlink` 均要求路径在白名单根内）
- 执行系统命令、启动子进程（subprocess / os.system / os.exec / os.spawn / os.fork 等）
- 访问网络（socket 全系列）
- 删除 / 修改沙箱外文件、创建系统链接（仅允许沙箱内路径）
- ctypes：拦截进程句柄加载（``CDLL(None)`` / ``pythonapi``）、按路径加载库，
  以及解析到危险符号（system / socket / open 等）；允许系统库裸名加载
  （numpy / scipy / matplotlib 正常需要）
- 其它危险操作：os.chdir、进程信号（os.kill/os.killpg）、权限切换（os.setuid 等）一律禁止

同时由调用方把临时目录、matplotlib 缓存目录重定向进沙箱，
避免正常绘图流程（tempfile、字体缓存）被误拦。

警告：这是 Python 层的纵深防御（on-host），**并非操作系统级沙箱**。
审计钩子拦截的是 CPython 已接入审计机制的调用，不限制 CPU / 内存，
且无法阻止第三方库中未经过审计的系统调用路径。
"""

import os
from pathlib import Path


def _collect_font_dirs() -> list[str]:
    """返回只读系统字体目录白名单，供 matplotlib 字体扫描使用。"""
    dirs: list[str] = []
    windir = os.environ.get("WINDIR")
    if windir:
        dirs.append(os.path.join(windir, "Fonts"))
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        dirs.append(os.path.join(local_appdata, "Microsoft", "Windows", "Fonts"))
    home = os.path.expanduser("~")
    dirs.extend(
        [
            "/usr/share/fonts",
            "/usr/local/share/fonts",
            os.path.join(home, ".fonts"),
            os.path.join(home, ".local", "share", "fonts"),
            "/System/Library/Fonts",
            "/Library/Fonts",
            os.path.join(home, "Library", "Fonts"),
        ]
    )
    return dirs


# 注意：模板体内不得出现裸 {} 字符（使用 .format() 渲染），
# 因此集合一律用元组，避免大括号冲突。
_BOOTSTRAP_TEMPLATE = r"""
import ctypes  # noqa: F401 — 必须在安装审计钩子前预加载 stdlib ctypes
import os
import sys

_ROOT = {root!r}

# 禁止沙箱代码卸载/篡改审计钩子（Python 3.12+ 提供 sys.removeaudithook）。
sys.removeaudithook = lambda *a, **kw: None

# 只读白名单根：沙箱本身、Python 安装目录（stdlib/site-packages）、系统字体目录。
_READ_ROOTS = [
    _ROOT,
    sys.prefix,
    sys.base_prefix,
    sys.exec_prefix,
    sys.base_exec_prefix,
] + {font_dirs!r}

_WRITE_OFLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _norm(p):
    if isinstance(p, bytes):
        p = os.fsdecode(p)
    p = os.fspath(p)
    if not os.path.isabs(p):
        p = os.path.join(_ROOT, p)
    return os.path.normpath(p)


def _inside(p, root):
    try:
        return os.path.commonpath([_norm(p), _norm(root)]) == _norm(root)
    except ValueError:
        return False


def _read_ok(p):
    return _inside(p, _ROOT) or any(_inside(p, r) for r in _READ_ROOTS if r)


def _write_ok(p):
    return _inside(p, _ROOT)


def _block(reason, target=None):
    if target is not None:
        reason = "%s: %s" % (
            reason,
            os.fsdecode(target) if isinstance(target, bytes) else target,
        )
    raise PermissionError("[sandbox] " + reason)


# ctypes 危险符号黑名单：这些符号一旦经 dlsym 解析，
# 可在不触发其它审计事件的前提下执行命令 / 访问网络 / 读写文件。
_DANGEROUS_SYMBOLS = (
    # POSIX 命令执行 / 进程
    "system", "execl", "execle", "execlp", "execv", "execve", "execvp",
    "execvpe", "fexecve", "fork", "forkpty", "vfork", "popen", "posix_spawn",
    "posix_spawnp", "daemon", "execveat",
    # Windows 进程 / 命令
    "winexec", "createprocess", "createprocessa", "createprocessw",
    "createprocessinternalw", "shellexecute", "shellexecutea", "shellexecutew",
    "shellexecuteex", "loadmodule", "createthread", "createremotethread",
    "writeprocessmemory", "readprocessmemory", "virtualalloc", "virtualprotect",
    "rtluserthreadstart", "queueuserapc", "setthreadcontext",
    # 网络
    "socket", "socketpair", "connect", "connectex", "send", "sendto",
    "sendmsg", "bind", "listen", "accept", "accept4", "gethostbyname",
    "getaddrinfo", "getnameinfo", "setsockopt", "getsockopt", "recv",
    "recvfrom", "wsastartup", "wsaconnect", "internetopen",
    "internetconnect", "httpsendrequest", "urldownloadtofile", "winhttpopen",
    "winhttpconnect",
    # 文件读写（绕过审计钩子的路径检查）
    "open", "openat", "open64", "creat", "fopen", "fopen64", "freopen",
    "remove", "removexattr", "unlink", "unlinkat", "rename", "renameat",
    "renameat2", "rmdir", "mkdir", "mkdirat", "chmod", "chown", "fchmod",
    "fchown", "truncate", "ftruncate", "write", "writev", "pwrite",
    "mknod", "mkfifo", "symlink", "link", "linkat", "mount", "umount",
    "createfile", "createfilea", "createfilew", "deletefile", "deletefilea",
    "deletefilew", "movefile", "movefilea", "movefilew", "writefile",
    "readfile", "setfileattributes", "removefile",
    # 底层 syscall / 内存
    "syscall", "syscall_64", "int80", "ptrace", "mmap", "mprotect",
    "mremap", "mlock", "munlock", "prctl", "madvise", "personality",
    "seccomp", "bpf", "reboot",
    # Windows 注册表
    "regopenkey", "regopenkeyex", "regsetvalue", "regcreatekey",
    "regdeletekey", "regdeletevalue", "regsavekey",
)


def _dangerous(name):
    if not isinstance(name, str):
        return False
    return name.lstrip("_").lower() in _DANGEROUS_SYMBOLS


def _audit(event, args):
    # 1) 命令执行 / 子进程
    if event in ("subprocess.Popen", "os.system", "os.fork", "os.forkpty"):
        _block("subprocess/command execution is blocked")
    if (
        event.startswith("os.spawn")
        or event.startswith("os.exec")
        or event.startswith("posix.exec")
        or event.startswith("posix.spawn")
    ):
        _block("subprocess/command execution is blocked")
    # 2) 网络
    if event.startswith("socket.") or event == "urllib.Request":
        _block("network access is blocked")
    if event.startswith("http.") or event.startswith("ftplib."):
        _block("network access is blocked")
    # 3) ctypes：允许系统库裸名加载（numpy/scipy/matplotlib 需要），
    #    但拦截进程句柄加载（CDLL(None)/pythonapi）与按路径加载恶意库，
    #    并拦截解析到危险符号（system/socket/open 等）。
    if event == "ctypes.dlopen":
        name = args[0] if args else None
        path = args[1] if len(args) > 1 else None
        if name is None:
            _block("ctypes process-handle loading (pythonapi) is blocked")
        if path is not None or (
            isinstance(name, str)
            and (
                os.path.isabs(name)
                or "/" in name
                or "\\" in name
                or ".." in name
            )
        ):
            _block("ctypes loading libraries by path is blocked", name)
    elif event == "ctypes.dlsym":
        name = args[1] if len(args) > 1 else None
        if _dangerous(name):
            _block("ctypes access to dangerous symbol is blocked", name)
    # 4) 进程信号 / 权限切换
    if event in ("os.kill", "os.killpg"):
        _block("process signaling is blocked")
    if event.startswith("os.setu") or event.startswith("os.setg"):
        _block("privilege change is blocked")
    # 5) 目录切换
    if event == "os.chdir":
        _block("os.chdir is blocked")
    # 6) 文件修改/删除/创建：仅允许沙箱内
    if event in (
        "os.remove", "os.unlink", "os.rmdir", "os.rename", "os.replace",
        "os.chmod", "os.chown", "os.chflags", "os.lchmod", "os.lchown",
        "os.truncate", "os.utime", "os.symlink", "os.link", "os.mknod",
        "os.mkdir", "os.makedirs",
    ):
        p = args[0] if args and isinstance(args[0], (str, bytes)) else None
        if p is None or not _write_ok(p):
            _block(event + " outside sandbox is blocked", p)
    # 7) 打开文件：按读写意图分流
    if event == "os.open":
        p, flags, _ = args
        if not isinstance(p, (str, bytes)):
            return
        if flags & _WRITE_OFLAGS:
            if not _write_ok(p):
                _block("write outside sandbox is blocked", p)
        elif not _read_ok(p):
            _block("read outside sandbox is blocked", p)
    elif event == "open":
        p, mode, flags = args
        if not isinstance(p, (str, bytes)):
            return
        if isinstance(mode, bytes):
            mode = os.fsdecode(mode)
        flags = flags or 0
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            flags & _WRITE_OFLAGS
        ):
            if not _write_ok(p):
                _block("write outside sandbox is blocked", p)
        elif not _read_ok(p):
            _block("read outside sandbox is blocked", p)
    # 8) 目录枚举与元数据读取：仅允许白名单根内。
    #    注意：os.stat / os.lstat / os.access 不触发审计事件，无法拦截，
    #    文件内容读取由上面的 open/os.open 分支负责。
    if event in ("os.listdir", "os.scandir", "os.readlink"):
        p = args[0] if args and isinstance(args[0], (str, bytes)) else None
        if p is not None and not _read_ok(p):
            _block(event + " outside sandbox is blocked", p)


sys.addaudithook(_audit)
"""


def render_guard_bootstrap(sandbox_root: str | Path) -> str:
    """生成注入沙箱子进程的防护引导代码（先安装审计钩子再执行用户脚本）。"""
    root = str(Path(sandbox_root).resolve())
    font_dirs = _collect_font_dirs()
    return _BOOTSTRAP_TEMPLATE.format(root=root, font_dirs=font_dirs)
