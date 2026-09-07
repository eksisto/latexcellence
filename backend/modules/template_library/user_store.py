"""用户模板库 CRUD（纯同步函数层，经 asyncio.to_thread 调度）。

双 root 注入：
    - 多用户模式：<users_root>/<user_id>/template_library/<template_id>/
      （user_id 来自认证依赖；目录归属校验由路径天然保证——只能读写
      当前用户库内的模板，他人模板 404）
    - 单用户模式：config.features.template_library.user_library_dir
      （全局共享目录，保持存量单用户行为）

模板 id：uuid4().hex（服务端生成）；内置模板 id 为目录名（见 builtin_store）。
manifest.json 与内置模板 schema 一致，含 id/name/category/main_file/source 等。
方法内实时调用 get_config()，不缓存路径。
"""

import json
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.multiuser import userspace

from . import builtin_store
from .models import (
    TemplateManifest,
    TemplateSummary,
)

logger = get_logger(__name__)

# 用户模板库目录名（多用户模式下挂在每个用户空间根下）。
# 注意：与 backend/core/multiuser/quota.py 的 TEMPLATE_LIBRARY_SUBDIR 保持一致
# （quota 将该目录并入「模板」衍生类别扫描，core 层不得反向依赖本模块）。
TEMPLATE_LIBRARY_SUBDIR = "template_library"

# file_count 口径：manifest.json 是服务端生成的库工件，与详情端点的文件树
# 一致地排除（否则列表计数比详情文件树恒多 1）
_COUNT_EXCLUDES = ("manifest.json",)


def library_root(user_id: str | None) -> Path:
    """解析当前模式的模板库根目录（多用户按用户隔离 / 单用户全局目录）。"""
    if userspace.is_multiuser() and user_id:
        return userspace.user_root(user_id) / TEMPLATE_LIBRARY_SUBDIR
    cfg = get_config()
    root = Path(cfg.features.template_library.user_library_dir)
    if not root.is_absolute():
        root = Path.cwd() / root
    return root


def template_dir(user_id: str | None, template_id: str) -> Path:
    """返回用户模板目录（校验 id 安全性；不确认存在性）。"""
    builtin_store.validate_template_id(template_id)
    return library_root(user_id) / template_id


def _summary(manifest: TemplateManifest, root: Path) -> TemplateSummary:
    return TemplateSummary(
        kind="user",
        file_count=builtin_store.count_files(root, exclude=_COUNT_EXCLUDES),
        **manifest.model_dump(),
    )


def list_templates(user_id: str | None) -> list[TemplateSummary]:
    """列出当前用户库内全部模板（按 id 排序；损坏条目跳过并告警）。"""
    root = library_root(user_id)
    result: list[TemplateSummary] = []
    if not root.is_dir():
        return result
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        manifest = builtin_store.load_manifest(entry)
        if manifest is None:
            continue
        result.append(_summary(manifest, entry))
    return result


def get_template(user_id: str | None, template_id: str) -> TemplateSummary | None:
    """按 id 返回用户模板摘要；不存在/损坏返回 None。"""
    root = template_dir(user_id, template_id)
    if not root.is_dir():
        return None
    manifest = builtin_store.load_manifest(root)
    if manifest is None:
        return None
    return _summary(manifest, root)


def create_template(
    user_id: str | None,
    source_root: Path,
    manifest: TemplateManifest,
    *,
    ignore: Callable[[str, list[str]], set[str]] | None = None,
) -> TemplateSummary:
    """把 source_root 文件树复制入库为新模板（id 服务端生成，写入 manifest）。

    manifest.id 以生成的 uuid 为准；ignore 为 shutil.copytree 的 ignore
    回调（清洗规则由服务层构造）。返回新模板摘要。
    """
    template_id = uuid.uuid4().hex
    dest = template_dir(user_id, template_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_root, dest, ignore=ignore, symlinks=False)
    manifest = manifest.model_copy(update={"id": template_id})
    _write_manifest(dest, manifest)
    logger.info(
        "User template created: id=%s user=%s source=%s",
        template_id,
        user_id or "local",
        manifest.source,
    )
    return _summary(manifest, dest)


def _write_manifest(template_dir_path: Path, manifest: TemplateManifest) -> None:
    """按阶段①字段顺序写 manifest.json（覆盖上传包自带同名文件）。"""
    ordered = manifest.to_manifest_dict()
    with open(template_dir_path / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)
        f.write("\n")


def update_meta(
    user_id: str | None,
    template_id: str,
    *,
    name: str | None = None,
    category: str | None = None,
    description: str | None = None,
) -> TemplateSummary:
    """改名/分类/描述（部分更新；模板不存在抛 FileNotFoundError）。"""
    root = template_dir(user_id, template_id)
    manifest = builtin_store.load_manifest(root)
    if manifest is None or not root.is_dir():
        raise FileNotFoundError(f"Template not found: {template_id}")
    updates: dict[str, str] = {}
    if name is not None:
        updates["name"] = name
    if category is not None:
        updates["category"] = category
    if description is not None:
        updates["description"] = description
    manifest = manifest.model_copy(update=updates)
    _write_manifest(root, manifest)
    logger.info("User template updated: id=%s user=%s", template_id, user_id or "local")
    return _summary(manifest, root)


def delete_template(user_id: str | None, template_id: str) -> None:
    """删除用户模板目录（不存在抛 FileNotFoundError）。"""
    root = template_dir(user_id, template_id)
    if not root.is_dir():
        raise FileNotFoundError(f"Template not found: {template_id}")
    shutil.rmtree(root)
    logger.info("User template deleted: id=%s user=%s", template_id, user_id or "local")


def resolve_file(
    user_id: str | None,
    template_id: str,
    rel_path: str,
    *,
    allowed_suffixes: tuple[str, ...] | None = None,
) -> Path:
    """安全解析用户模板内文件路径（复用 path_guard 校验链，违规抛 ValueError）。"""
    root = template_dir(user_id, template_id)
    return builtin_store.resolve_template_file(
        root, rel_path, allowed_suffixes=allowed_suffixes
    )
