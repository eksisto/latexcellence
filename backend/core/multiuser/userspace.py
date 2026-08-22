"""
多用户模式用户空间目录布局。

目录结构（对应方案 §4.3）：

    <users_root>/<user_id>/
        ├── projects/               # 项目目录：<projects>/<project_id>/
        ├── writing_chat_sessions/
        ├── style_modify_sessions/
        ├── templates/
        └── plots/

约束：
    - users_root 来自 multi_user.users_root（服务器配置，非用户输入）
    - user_id / project_id 生产环境均为服务端生成的 UUID hex；user_root() /
      project_dir() 仍对组件做防穿越校验（防御纵深）：即使未来出现直接
      拼接用户输入的代码，也无法借 id 组件逃逸出用户空间
    - 方法内实时调用 get_config()，不在 __init__ 缓存路径
"""

import re
from contextvars import ContextVar
from pathlib import Path

from backend.core.config import get_config
from backend.core.logger import get_logger

# id 组件安全模式：字母/数字/下划线/连字符（覆盖 UUID hex 与测试用简单 id），
# 拒绝空值、路径分隔符、'..'、点开头等一切可造成路径逃逸的形态
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# 衍生数据子目录（与方案 §9.2 统计口径一致）
DERIVED_SUBDIRS = (
    "writing_chat_sessions",
    "style_modify_sessions",
    "templates",
    "plots",
)

logger = get_logger("multiuser.userspace")

# ── 请求级当前用户上下文（P6 会话类资源归属校验） ────────────────────────
# 多用户模式下，会话类资源（写作会话 / style_modify 会话 / 模板 / 绘图）按
# user_id 目录隔离。各模块在解析资源根目录时读取该上下文，实现"资源路径必须
# 位于当前用户衍生目录内"的归属校验。ContextVar 由路由依赖注入（见
# dependencies.current_user_context），异步请求与 asyncio.to_thread 均可见。
_current_user_id: ContextVar[str | None] = ContextVar(
    "multiuser_current_user_id", default=None
)


def is_multiuser() -> bool:
    """当前是否处于多用户模式（实时读取配置）。"""
    return bool(get_config().multi_user.enabled)


def set_current_user_id(user_id: str | None) -> None:
    """设置请求级当前用户上下文。None 表示未登录/单用户模式。

    v2 P1b 起由 AccessSecurityMiddleware 在多用户模式会话验证成功后全局
    注入（覆盖全部 /api 端点）；P6 的 current_user_context 路由依赖保留
    （幂等重设，兼容测试与无中间件场景）。
    """
    _current_user_id.set(user_id)


def get_current_user_id() -> str | None:
    """读取请求级当前用户上下文；单用户模式/未登录返回 None。"""
    return _current_user_id.get()


def user_resource_root(subdir_name: str, legacy_root: Path) -> Path:
    """统一会话类资源根目录解析入口（P6 存量路径改造）。

    - 单用户模式：返回存量全局路径（legacy_root），保持既有行为不变；
    - 多用户模式：返回当前用户衍生目录 <users_root>/<user_id>/<subdir_name>/，
      实现按用户隔离；未取到当前用户（理论上仅发生在绕过路由直调时）回退
      legacy_root 并记录告警，保证单用户回归不受影响。

    subdir_name 必须属于 DERIVED_SUBDIRS（目录名对应方案 §4.3 用户空间布局）。
    """
    if not is_multiuser():
        return Path(legacy_root)
    user_id = get_current_user_id()
    if not user_id:
        logger.warning(
            "Multiuser mode active but no current-user context; "
            "falling back to legacy root %s (subdir %s)",
            legacy_root,
            subdir_name,
        )
        return Path(legacy_root)
    d = derived_dir(user_id, subdir_name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def iterate_user_resource_roots(subdir_name: str) -> list[Path]:
    """多用户模式：返回所有用户空间中 subdir_name 子目录的列表（仅返回已存在的）。

    供会话类资源的后台清理任务使用（单用户模式返回空列表，由存量全局路径清理覆盖）。
    """
    if subdir_name not in DERIVED_SUBDIRS:
        raise ValueError(f"Unknown derived subdir: {subdir_name}")
    if not is_multiuser():
        return []
    roots_root = get_users_root()
    if not roots_root.is_dir():
        return []
    result: list[Path] = []
    for user_dir in sorted(roots_root.iterdir()):
        child = user_dir / subdir_name
        if child.is_dir():
            result.append(child)
    return result


def get_users_root() -> Path:
    """返回用户空间根目录（实时读取配置）。"""
    return Path(get_config().multi_user.users_root)


def get_staging_root() -> Path:
    """返回上传暂存区根目录（实时读取配置）。"""
    return Path(get_config().multi_user.staging_root)


def _validate_id_component(kind: str, value: str) -> str:
    """校验路径组件（user_id / project_id）不含路径穿越形态，违规抛 ValueError。"""
    if not value or not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"Unsafe {kind} component: {value!r}")
    return value


def get_archive_root() -> Path:
    """返回用户删除时的衍生数据归档根目录（<users_root 的父目录>/archive）。"""
    return get_users_root().parent / "archive"


def user_root(user_id: str) -> Path:
    """返回指定用户的根目录。"""
    return get_users_root() / _validate_id_component("user_id", user_id)


def project_dir(user_id: str, project_id: str) -> Path:
    """返回指定用户某项目的根目录。"""
    return (
        get_users_root()
        / _validate_id_component("user_id", user_id)
        / "projects"
        / _validate_id_component("project_id", project_id)
    )


def derived_dir(user_id: str, subdir_name: str) -> Path:
    """返回用户某类衍生数据目录；subdir_name 必须属于 DERIVED_SUBDIRS。"""
    if subdir_name not in DERIVED_SUBDIRS:
        raise ValueError(f"Unknown derived subdir: {subdir_name}")
    return user_root(user_id) / subdir_name


def ensure_user_dirs(user_id: str) -> Path:
    """注册 / 首次使用时创建用户空间目录结构，返回用户根目录。"""
    root = user_root(user_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "projects").mkdir(parents=True, exist_ok=True)
    for sub in DERIVED_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root
