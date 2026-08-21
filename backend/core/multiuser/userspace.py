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
from pathlib import Path

from backend.core.config import get_config

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
