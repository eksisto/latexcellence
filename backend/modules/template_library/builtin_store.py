"""内置模板发现与共享文件原语（纯同步函数层，经 asyncio.to_thread 调度）。

- 扫描 builtin_templates/ 下各模板目录的 manifest.json，不做启动期缓存
  （每次请求实时读盘，与配置热重载/模板热更新语义一致）
- 本模块同时承载 builtin_store / user_store 共用的底层文件原语：
  模板 id 校验、文件树枚举、path_guard 文件解析

内置模板目录约定（阶段①）：
    <BUILTIN_ROOT>/<template_id>/manifest.json + assets/（thumbnail.png +
    preview.pdf 元资源）+ 根下 LaTeX 工程文件
"""

import json
import re
from pathlib import Path

from pydantic import ValidationError

from backend.core.logger import get_logger
from backend.utils.path_guard import resolve_project_path

from .models import TemplateFileEntry, TemplateManifest, TemplateSummary

logger = get_logger(__name__)

# 内置模板根目录（包内资源，随代码分发）
BUILTIN_ROOT = Path(__file__).resolve().parent / "builtin_templates"

# 模板 id 安全模式：字母/数字/下划线/连字符（内置 id 为目录名，用户 id 为 uuid hex），
# 拒绝空值、路径分隔符、'..' 等一切可造成路径逃逸的形态
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# 内置模板元资源：创建项目/文件树时排除（非工程文件）
BUILTIN_META_EXCLUDES = ("manifest.json", "assets")


def validate_template_id(template_id: str) -> str:
    """校验模板 id 不含路径穿越形态，违规抛 ValueError。"""
    if not template_id or not SAFE_ID_RE.fullmatch(template_id):
        raise ValueError(f"Unsafe template id: {template_id!r}")
    return template_id


def load_manifest(template_dir: Path) -> TemplateManifest | None:
    """读取模板目录下的 manifest.json；缺失/损坏返回 None（跳过不中断发现）。"""
    path = template_dir / "manifest.json"
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return TemplateManifest.model_validate(data)
    except (OSError, ValueError, ValidationError) as e:
        logger.warning("Invalid template manifest %s: %s", path, e)
        return None


def _summary(manifest: TemplateManifest, kind: str, file_count: int) -> TemplateSummary:
    return TemplateSummary(
        kind=kind,
        file_count=file_count,
        **manifest.model_dump(),
    )


def list_templates() -> list[TemplateSummary]:
    """发现全部内置模板（按 id 排序，损坏 manifest 的目录跳过并告警）。"""
    result: list[TemplateSummary] = []
    if not BUILTIN_ROOT.is_dir():
        return result
    for entry in sorted(BUILTIN_ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        manifest = load_manifest(entry)
        if manifest is None:
            continue
        result.append(
            _summary(
                manifest,
                "builtin",
                count_files(entry, exclude=BUILTIN_META_EXCLUDES),
            )
        )
    return result


def get_template(template_id: str) -> TemplateSummary | None:
    """按 id 返回内置模板摘要；不存在/损坏返回 None。"""
    template_dir(template_id)  # id 安全校验
    root = BUILTIN_ROOT / template_id
    if not root.is_dir():
        return None
    manifest = load_manifest(root)
    if manifest is None:
        return None
    return _summary(
        manifest, "builtin", count_files(root, exclude=BUILTIN_META_EXCLUDES)
    )


def template_dir(template_id: str) -> Path:
    """返回内置模板目录（校验 id 安全性；不确认存在性）。"""
    validate_template_id(template_id)
    return BUILTIN_ROOT / template_id


# ── 共享文件原语（builtin_store / user_store 共用）────────────────────


def count_files(root: Path, *, exclude: tuple[str, ...] = ()) -> int:
    """统计模板目录内文件数（按首段目录名/文件名排除，跳过符号链接）。"""
    return len(iter_template_files(root, exclude=exclude))


def iter_template_files(
    root: Path, *, exclude: tuple[str, ...] = ()
) -> list[TemplateFileEntry]:
    """枚举模板文件树（相对 POSIX 路径 + 大小，排序稳定）。

    exclude 按「任意层级的目录名」与「文件名」精确匹配（如内置模板的
    manifest.json 与 assets/）；符号链接一律跳过。
    """
    files: list[TemplateFileEntry] = []
    root = root.resolve()
    if not root.is_dir():
        return files
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in exclude for part in rel.parts):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        files.append(TemplateFileEntry(path=rel.as_posix(), size=size))
    return files


def resolve_template_file(
    root: Path,
    rel_path: str,
    *,
    allowed_suffixes: tuple[str, ...] | None = None,
) -> Path:
    """把模板内相对路径安全解析为绝对路径（path_guard 唯一入口）。

    拒绝绝对路径 / '..' / 符号链接 / 越界逃逸 / 白名单外扩展名，
    违规抛 ValueError（由服务层映射 HTTP）。
    """
    return resolve_project_path(
        root,
        rel_path,
        allowed_suffixes=allowed_suffixes,
        allow_symlink=False,
    )
