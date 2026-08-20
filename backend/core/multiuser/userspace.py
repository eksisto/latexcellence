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
    - user_id 一律为服务端生成的 UUID hex，绝不采用用户提交值，因此
      此处不做路径穿越校验；对用户可控的（相对项目）路径，P2 将新增
      基于 path_guard.resolve_project_path() 的统一解析入口 resolve_user_path()
    - 方法内实时调用 get_config()，不在 __init__ 缓存路径
"""

from pathlib import Path

from backend.core.config import get_config

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


def user_root(user_id: str) -> Path:
    """返回指定用户的根目录。"""
    return get_users_root() / user_id


def project_dir(user_id: str, project_id: str) -> Path:
    """返回指定用户某项目的根目录。"""
    return user_root(user_id) / "projects" / project_id


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
