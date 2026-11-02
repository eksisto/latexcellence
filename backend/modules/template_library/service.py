"""模板库业务编排（零 LLM 依赖）。

职责：
    - 列表合并（内置 + 我的，category 筛选）、详情/文件树、源码查看
    - 内置模板缩略图 / PDF 预览解析（仅内置）
    - 上传 zip 建模板（复用 extract_archive 安全链 + 扩展名白名单收窄）
    - 从模板创建项目（多用户：配额预检 → move_into_user_space +
      create_project_row + schedule_auto_init 同一链路，超限 413；
      单用户：{name, target_dir} 落盘，非空冲突报错）
    - 当前项目另存为模板 / template_gen 会话产物入库（复用清洗规则）

约定：
    - 阻塞 I/O 与目录复制/扫描一律 asyncio.to_thread，不阻塞事件循环
    - 路径拼接统一经 path_guard（见 builtin_store.resolve_template_file）
    - 清洗：创建项目/另存为模板时排除 .git/ 与 LaTeX 编译产物（及内置
      模板的 manifest.json 与 assets/）；上传模板做扩展名白名单收窄，
      无扩展名杂项（.gitignore/Makefile 等）豁免校验且不入库（解压即清理）
    - 错误统一映射 HTTPException（结构化 detail：{"error": code, ...}）
"""

import asyncio
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.multiuser import userspace
from backend.core.multiuser.quota import (
    checkpoint_derived_usage_for_current_user,
    create_project_row,
    derived_usage_for_user,
    dir_size,
    effective_derived_quota,
    enforce_derived_quota_for_current_user,
    enforce_project_quota,
)
from backend.core.multiuser.upload import (
    ArchiveError,
    build_limits,
    extract_archive,
    move_into_user_space,
)
from backend.modules.git_manage import service as git_manage_service
from backend.modules.projects import service as projects_service
from backend.modules.template_gen.agent import load_sandbox
from backend.modules.template_gen.trace import TRACE_FILE

from . import builtin_store, user_store
from .models import (
    TEMPLATE_CATEGORIES,
    TemplateManifest,
    TemplateSummary,
    today_iso,
)

logger = get_logger(__name__)

# 项目名长度上限（与 projects.service 一致的命名口径）
_MAX_NAME_LENGTH = 200

# 源码查看扩展名（file 端点为纯文本通道；二进制素材走 thumbnail/preview 端点）
_VIEWABLE_SUFFIXES = frozenset(
    {
        ".tex",
        ".sty",
        ".cls",
        ".bib",
        ".bst",
        ".bbx",
        ".cbx",
        ".clo",
        ".def",
        ".cfg",
        ".fd",
        ".dtx",
        ".ins",
        ".ltx",
        ".tikz",
        ".md",
        ".txt",
        ".json",
    }
)

# 清洗排除的 LaTeX 编译产物（创建项目 / 另存为模板时剔除；上传模板原样保存）
_BUILD_ARTIFACT_SUFFIXES = (
    ".aux",
    ".log",
    ".out",
    ".toc",
    ".lof",
    ".lot",
    ".bbl",
    ".blg",
    ".fls",
    ".fdb_latexmk",
    ".synctex.gz",
    ".synctex(busy).gz",
    ".xdv",
    ".nav",
    ".snm",
    ".vrb",
    ".idx",
    ".ind",
    ".ilg",
    ".loa",
    ".bcf",
    ".run.xml",
    ".spl",
)

# 清洗一律排除的目录/文件名
_CLEAN_EXCLUDE_NAMES = frozenset({".git"})

# 内置模板资产 → (相对路径, media type)
_BUILTIN_ASSETS = {
    "thumbnail": ("assets/thumbnail.png", "image/png"),
    "preview": ("assets/preview.pdf", "application/pdf"),
}


def _http(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message})


def _map_archive_error(err: ArchiveError) -> HTTPException:
    """解压校验错误 → 稳定 HTTP 错误码（安全拒绝记录审计日志）。"""
    logger.warning(
        "Template upload rejected: code=%s message=%s", err.code, err.message
    )
    if err.code in ("quota_exceeded", "upload_too_large"):
        return _http(413, err.code, err.message)
    return _http(400, err.code, err.message)


def sanitize_template_name(name: str, fallback: str = "我的模板") -> str:
    """清洗模板名：去掉路径分隔符/控制字符，压缩空白，限制长度。"""
    if not name:
        return fallback
    cleaned = re.sub(r"[\r\n\t/\\:\*\?\"<>|]+", " ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        return fallback
    return cleaned[:_MAX_NAME_LENGTH]


def _make_clean_ignore(
    *,
    exclude_builtin_meta: bool = False,
    extra_names: tuple[str, ...] = (),
    extra_suffixes: tuple[str, ...] = (),
):
    """构造 shutil.copytree 的 ignore 回调（清洗规则唯一实现）。

    - 排除 .git/ 与编译产物扩展名（任意层级）
    - 符号链接一律剔除：copytree 的 symlinks=False 会把链接目标内容
      内联复制，项目内指向用户空间外的链接可借此把任意文件带进模板库
      或新建项目（同模块文件枚举/打包路径均已跳过符号链接）
    - exclude_builtin_meta：另排除内置模板的 manifest.json 与 assets/
    - extra_names / extra_suffixes：来源特定排除（如 template_gen 的
      trace.jsonl 与 PDF 编译输出）
    """

    def _ignore(_directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            if (Path(_directory) / name).is_symlink():
                ignored.add(name)
                continue
            if name in _CLEAN_EXCLUDE_NAMES or name in extra_names:
                ignored.add(name)
                continue
            low = name.lower()
            if any(low.endswith(suffix) for suffix in _BUILD_ARTIFACT_SUFFIXES):
                ignored.add(name)
                continue
            if any(low.endswith(suffix) for suffix in extra_suffixes):
                ignored.add(name)
                continue
            if exclude_builtin_meta and name in builtin_store.BUILTIN_META_EXCLUDES:
                ignored.add(name)
        return ignored

    return _ignore


def detect_main_file(root: Path) -> str:
    """自动探测模板主文件（返回相对 POSIX 路径；无 .tex 抛 ValueError）。

    优先级：根下 main.tex → 含 \\documentclass 的最浅 .tex → 最浅 .tex。
    """
    if (root / "main.tex").is_file():
        return "main.tex"
    try:
        candidates = sorted(
            (p for p in root.rglob("*.tex") if p.is_file() and not p.is_symlink()),
            key=lambda p: (len(p.relative_to(root).parts), p.name),
        )
    except OSError:
        candidates = []
    if not candidates:
        raise ValueError("No LaTeX source file found in template")
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if re.search(r"^[^%\n]*\\documentclass", text):
            return path.relative_to(root).as_posix()
    return candidates[0].relative_to(root).as_posix()


def _actor_id(actor: dict | None) -> str | None:
    """当前用户 id；单用户模式（actor=None）返回 None（走全局库目录）。"""
    return actor["id"] if actor else None


async def _resolve_template_root(
    user_id: str | None, template_id: str
) -> tuple[Path, str]:
    """解析模板根目录与 kind：先查内置再查用户库；越权/不存在一律 404。"""
    try:
        builtin_store.validate_template_id(template_id)
    except ValueError as e:
        raise _http(400, "invalid_id", str(e)) from e
    if await asyncio.to_thread(builtin_store.get_template, template_id) is not None:
        return builtin_store.template_dir(template_id), "builtin"
    if (
        await asyncio.to_thread(user_store.get_template, user_id, template_id)
        is not None
    ):
        return user_store.template_dir(user_id, template_id), "user"
    raise _http(404, "not_found", "Template not found")


async def _require_user_template(user_id: str | None, template_id: str) -> None:
    """要求模板存在且不是内置模板（内置只读，改名/删除拒绝）。"""
    if await asyncio.to_thread(builtin_store.get_template, template_id) is not None:
        raise _http(
            400,
            "builtin_readonly",
            "Built-in templates cannot be modified or deleted",
        )
    if not await asyncio.to_thread(user_store.get_template, user_id, template_id):
        raise _http(404, "not_found", "Template not found")


def _manifest_from_source(
    source_root: Path,
    *,
    source: str,
    name: str | None,
    fallback_name: str,
    category: str | None,
    description: str,
    author: str,
) -> TemplateManifest:
    """从源目录构造入库 manifest：带 manifest 时继承元数据，缺失时自动生成
    （zip/项目名为名、other 分类、自动探测 main_file）。

    字段优先级：请求显式提供 > 已有 manifest > 自动回退（fallback_name/other）。
    """
    existing = builtin_store.load_manifest(source_root)
    if category is not None and category not in TEMPLATE_CATEGORIES:
        raise _http(
            400,
            "invalid_category",
            f"Invalid category (must be one of {', '.join(sorted(TEMPLATE_CATEGORIES))})",
        )
    if existing is not None:
        base = existing.model_dump()
    else:
        try:
            main_file = detect_main_file(source_root)
        except ValueError as e:
            raise _http(400, "no_main_file", str(e)) from e
        base = {
            "id": "pending",
            "name": "",
            "name_en": "",
            "description": "",
            "category": "other",
            "main_file": main_file,
            "engine": None,
            "author": "",
            "license": "",
            "source": source,
            "created_at": today_iso(),
        }
    final_name = sanitize_template_name(name or base.get("name") or fallback_name or "")
    base.update(
        {
            "name": final_name,
            "category": category or base.get("category") or "other",
            "description": description if description else base.get("description", ""),
            "author": author or base.get("author", ""),
            "source": source,
            "created_at": today_iso(),
        }
    )
    try:
        return TemplateManifest.model_validate(base)
    except Exception as e:
        logger.warning("Generated manifest invalid for %s: %s", source_root, e)
        raise _http(400, "invalid_manifest", "Template metadata is invalid") from e


# ── 查询类 ───────────────────────────────────────────────────────────
async def list_templates(actor: dict | None, category: str | None) -> dict:
    """模板列表：内置 + 我的，支持 category 筛选。"""
    user_id = _actor_id(actor)
    builtin = await asyncio.to_thread(builtin_store.list_templates)
    mine = await asyncio.to_thread(user_store.list_templates, user_id)
    items: list[TemplateSummary] = list(builtin) + list(mine)
    if category:
        items = [t for t in items if t.category == category]
    return {"templates": [t.model_dump() for t in items]}


async def get_template(actor: dict | None, template_id: str) -> dict:
    """模板详情 + 文件树（相对路径，绝不暴露服务器绝对路径）。"""
    user_id = _actor_id(actor)
    root, kind = await _resolve_template_root(user_id, template_id)
    exclude = (
        builtin_store.BUILTIN_META_EXCLUDES if kind == "builtin" else ("manifest.json",)
    )
    summary = (
        await asyncio.to_thread(builtin_store.get_template, template_id)
        if kind == "builtin"
        else await asyncio.to_thread(user_store.get_template, user_id, template_id)
    )
    if summary is None:
        raise _http(404, "not_found", "Template not found")
    files = await asyncio.to_thread(
        lambda: builtin_store.iter_template_files(root, exclude=exclude)
    )
    detail = summary.model_dump()
    detail["files"] = [f.model_dump() for f in files]
    return detail


async def read_file(actor: dict | None, template_id: str, rel_path: str) -> dict:
    """模板内源码查看（path_guard + 文本扩展名双重白名单）。"""
    user_id = _actor_id(actor)
    root, _kind = await _resolve_template_root(user_id, template_id)
    allowed = tuple(get_config().features.template_library.allowed_extensions)
    try:
        resolved = await asyncio.to_thread(
            builtin_store.resolve_template_file,
            root,
            rel_path,
            allowed_suffixes=allowed,
        )
    except ValueError as e:
        raise _http(400, "invalid_path", str(e)) from e
    if resolved.suffix.lower() not in _VIEWABLE_SUFFIXES:
        raise _http(
            400,
            "not_viewable",
            "Only text source files can be viewed online",
        )
    try:
        content = await asyncio.to_thread(
            lambda: resolved.read_text(encoding="utf-8", errors="replace")
        )
    except OSError as e:
        # 完整错误（含服务器绝对路径）只进日志，响应消息仅携带相对路径
        # （多用户模式下响应/错误消息不得泄露服务器路径）
        logger.warning("Template file read failed: %s: %s", resolved, e)
        raise _http(400, "read_failed", f"Failed to read file: {rel_path}") from e
    return {"path": rel_path, "content": content}


async def resolve_builtin_asset(
    actor: dict | None, template_id: str, asset: str
) -> tuple[Path, str]:
    """解析内置模板元资源（thumbnail/preview）；仅内置模板支持。"""
    rel_path, media_type = _BUILTIN_ASSETS[asset]
    user_id = _actor_id(actor)
    root, kind = await _resolve_template_root(user_id, template_id)
    if kind != "builtin":
        raise _http(
            404, "not_found", "Preview assets are only available for built-in templates"
        )
    suffix = Path(rel_path).suffix
    try:
        resolved = await asyncio.to_thread(
            builtin_store.resolve_template_file,
            root,
            rel_path,
            allowed_suffixes=(suffix,),
        )
    except ValueError as e:
        raise _http(404, "not_found", str(e)) from e
    if not resolved.is_file():
        raise _http(404, "not_found", "Asset not found for this template")
    return resolved, media_type


# ── 上传建库 ─────────────────────────────────────────────────────────
async def upload_template(
    actor: dict | None,
    archive: UploadFile,
    name: str | None,
    category: str | None,
) -> dict:
    """上传 zip 建模板：安全解压 → 扩展名白名单收窄 → 无 manifest 自动生成。"""
    user_id = _actor_id(actor)
    tl_cfg = get_config().features.template_library
    filename = archive.filename or ""
    if not filename.lower().endswith(".zip"):
        raise _http(400, "invalid_archive", "Only .zip archives are supported")

    # 多用户：入库前先做衍生配额预检（已超限直接拒绝，模板库占「模板」类别）
    await asyncio.to_thread(enforce_derived_quota_for_current_user, 0, "templates")

    staging = Path(tempfile.mkdtemp(prefix="template_upload_"))
    try:
        zip_path = staging / "upload.zip"
        max_bytes = tl_cfg.max_upload_mb * 1024 * 1024
        try:
            await asyncio.to_thread(_write_upload, archive, zip_path, max_bytes)
        except ArchiveError as e:
            raise _map_archive_error(e)
        extract_dir = staging / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=False)
        limits = build_limits(_extraction_budget(max_bytes))
        try:
            extracted = await asyncio.to_thread(
                extract_archive, zip_path, extract_dir, limits
            )
        except ArchiveError as e:
            raise _map_archive_error(e)

        root = extracted.root
        await asyncio.to_thread(_remove_extensionless_files, root)
        try:
            await asyncio.to_thread(_check_allowed_extensions, root)
        except ArchiveError as e:
            raise _map_archive_error(e)
        # manifest 构造含 rglob 全树扫描与逐文件读取探测主文件，入线程池
        manifest = await asyncio.to_thread(
            _manifest_from_source,
            root,
            source="upload",
            name=name,
            fallback_name=_stem_from_filename(filename),
            category=category,
            description="",
            author=_author_name(actor),
        )
        # 入库前按解压后体量做衍生配额校验（多用户；单用户无配额语义）
        await asyncio.to_thread(
            enforce_derived_quota_for_current_user, extracted.size, "templates"
        )
        summary = await asyncio.to_thread(
            user_store.create_template, user_id, root, manifest
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    await asyncio.to_thread(checkpoint_derived_usage_for_current_user, "templates")
    return {"template": summary.model_dump()}


def _author_name(actor: dict | None) -> str:
    if actor and actor.get("username"):
        return str(actor["username"])
    return ""


def _write_upload(archive: UploadFile, dest: Path, max_bytes: int) -> None:
    """流式写入上传 zip 并实时校验大小上限（同步，工作线程执行）。"""
    written = 0
    with open(dest, "wb") as dst:
        while True:
            chunk = archive.file.read(1 << 20)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                raise ArchiveError(
                    "upload_too_large",
                    f"Template archive exceeds upload size limit ({max_bytes} bytes)",
                )
            dst.write(chunk)


def _extraction_budget(max_bytes: int) -> int:
    """解压阶段体量预算（build_limits 的 remaining_quota_bytes 口径）。

    - 多用户：取当前用户「模板」类别剩余衍生配额，解压超预算即时拒绝，
      避免落满暂存区后才被入库前复核打回（与 projects.service 上传创建
      传入剩余项目配额的接缝语义一致）
    - 单用户：无配额语义，退化为上传包大小上限
    """
    user_id = userspace.get_current_user_id()
    if userspace.is_multiuser() and user_id:
        remaining = effective_derived_quota(user_id) - derived_usage_for_user(user_id)
        return max(remaining, 0)
    return max_bytes


def _stem_from_filename(filename: str) -> str:
    """从上传文件名提取模板基名（去 .zip 扩展名）。"""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if name.lower().endswith(".zip"):
        name = name[: -len(".zip")]
    return name.strip()


def _remove_extensionless_files(root: Path) -> None:
    """清理无扩展名文件（.gitignore/Makefile/LICENSE 等）及其空目录。

    无扩展名文件的 Path.suffix 为空串，不在白名单语义内，统一视为上传包
    冗余：不入库。删除后自底向上剔除变空的子目录（rmdir 仅对空目录生效）。
    """
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix == "":
            try:
                path.unlink()
            except OSError as e:
                logger.warning("Failed to remove extensionless file %s: %s", path, e)
    for path in sorted(
        (p for p in root.rglob("*") if p.is_dir() and not p.is_symlink()),
        reverse=True,
    ):
        try:
            path.rmdir()
        except OSError:
            pass


def _check_allowed_extensions(root: Path) -> None:
    """扩展名白名单收窄：上传包内出现白名单外文件类型直接拒绝。

    无扩展名文件（.gitignore/Makefile 等，Path.suffix 为空串）豁免校验，
    由 _remove_extensionless_files 统一清理，不入库。
    """
    allowed = {
        ext.lower() for ext in get_config().features.template_library.allowed_extensions
    }
    for entry in builtin_store.iter_template_files(root):
        suffix = Path(entry.path).suffix.lower()
        if suffix and suffix not in allowed:
            raise ArchiveError(
                "invalid_file_type",
                f"Template contains files of disallowed type: {entry.path}",
            )


# ── 修改 / 删除 ──────────────────────────────────────────────────────
async def update_template(
    actor: dict | None,
    template_id: str,
    *,
    name: str | None,
    category: str | None,
    description: str | None,
) -> dict:
    """改名/分类/描述（仅自己的模板；内置只读）。"""
    user_id = _actor_id(actor)
    await _require_user_template(user_id, template_id)
    if category is not None and category not in TEMPLATE_CATEGORIES:
        raise _http(
            400,
            "invalid_category",
            f"Invalid category (must be one of {', '.join(sorted(TEMPLATE_CATEGORIES))})",
        )
    try:
        summary = await asyncio.to_thread(
            user_store.update_meta,
            user_id,
            template_id,
            # 空白名视为未提供（否则会清洗成兜底名"我的模板"，违背请求语义）
            name=sanitize_template_name(name) if name and name.strip() else None,
            category=category,
            description=description,
        )
    except FileNotFoundError as e:
        raise _http(404, "not_found", str(e)) from e
    return {"template": summary.model_dump()}


async def delete_template(actor: dict | None, template_id: str) -> dict:
    """删除自己的模板（内置只读；他人模板 404 不泄露存在性）。"""
    user_id = _actor_id(actor)
    await _require_user_template(user_id, template_id)
    try:
        await asyncio.to_thread(user_store.delete_template, user_id, template_id)
    except FileNotFoundError as e:
        raise _http(404, "not_found", str(e)) from e
    await asyncio.to_thread(checkpoint_derived_usage_for_current_user, "templates")
    logger.info("Template deleted: id=%s user=%s", template_id, user_id or "local")
    return {"status": "ok", "template_id": template_id}


# ── 从模板创建项目 ───────────────────────────────────────────────────
async def create_project(
    actor: dict | None, template_id: str, *, name: str, target_dir: str | None
) -> dict:
    """复制清洗后的模板文件树创建项目。

    多用户：配额预检 → move_into_user_space + create_project_row +
    schedule_auto_init 同一链路（与上传创建一致），超限 413，同名自动去重。
    单用户：创建 <target_dir>/<项目名>/，目标非空冲突 409。
    """
    user_id = _actor_id(actor)
    project_name = projects_service.sanitize_project_name(name)
    if not project_name:
        raise _http(400, "invalid_name", "Project name cannot be empty")
    root, kind = await _resolve_template_root(user_id, template_id)

    staging = Path(tempfile.mkdtemp(prefix="template_project_"))
    project_root = staging / "project"
    try:
        # manifest.json 一律剔除：内置模板的库元数据自不待言，用户模板库内的
        # manifest.json 也是服务端生成的库工件，不应混入新建项目
        ignore = _make_clean_ignore(
            exclude_builtin_meta=(kind == "builtin"),
            extra_names=("manifest.json",),
        )
        await asyncio.to_thread(
            shutil.copytree, root, project_root, ignore=ignore, symlinks=False
        )
        if userspace.is_multiuser():
            return await _create_project_multiuser(user_id, project_root, project_name)
        return await _create_project_singleuser(project_root, project_name, target_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


async def _create_project_multiuser(
    user_id: str, project_root: Path, name: str
) -> dict:
    """多用户落位：配额 → move_into_user_space → 建行 → 自动初始化 Git。"""
    project_id = uuid.uuid4().hex
    size = await asyncio.to_thread(lambda: dir_size(project_root))
    # 配额预检（超限 413，项目不落位）
    await asyncio.to_thread(enforce_project_quota, user_id, size)
    try:
        await asyncio.to_thread(
            move_into_user_space, project_root, project_root, user_id, project_id
        )
    except Exception:
        # 落位失败清理用户空间残留（与 projects.service 同一兜底语义）
        dest = userspace.project_dir(user_id, project_id)
        await asyncio.to_thread(shutil.rmtree, dest, ignore_errors=True)
        raise
    try:
        row = await asyncio.to_thread(
            create_project_row, user_id, name, size, project_id=project_id
        )
    except Exception:
        dest = userspace.project_dir(user_id, project_id)
        await asyncio.to_thread(shutil.rmtree, dest, ignore_errors=True)
        raise
    # 落位成功后异步自动初始化 Git（best-effort，与上传创建一致）
    git_manage_service.schedule_auto_init(user_id, row["id"])
    logger.info(
        "Project created from template: user=%s project=%s name=%s",
        user_id,
        row["id"],
        row["name"],
    )
    return {"project": {"project_id": row["id"], "name": row["name"]}}


async def _create_project_singleuser(
    project_root: Path, name: str, target_dir: str | None
) -> dict:
    """单用户落盘：创建 <target_dir>/<项目名>/；目标非空冲突报错。"""
    if not target_dir or not target_dir.strip():
        raise _http(
            400, "target_dir_required", "target_dir is required in single-user mode"
        )
    target = (Path(target_dir) / name).resolve()
    if target.exists():
        if not target.is_dir():
            raise _http(
                409, "target_not_empty", "Target path exists and is not a directory"
            )
        if any(target.iterdir()):
            raise _http(
                409,
                "target_not_empty",
                f"Target directory is not empty: {name}",
            )
    await asyncio.to_thread(
        lambda: shutil.copytree(project_root, target, dirs_exist_ok=True)
    )
    logger.info("Project created from template at %s", target)
    return {"project": {"name": name, "path": str(target)}}


# ── 另存为模板 / template_gen 入库 ───────────────────────────────────
async def save_project_as_template(
    actor: dict | None,
    binding: dict,
    *,
    name: str,
    category: str | None,
    description: str,
) -> dict:
    """当前项目另存为模板（复用清洗规则；binding 来自 workspace 访问依赖）。

    多用户：binding 携带 owner_id/project_id（root 按用户空间权威解析）；
    单用户：经 workspace 绑定的项目实例取根目录（未打开 400）。
    """
    user_id = _actor_id(actor)
    if userspace.is_multiuser():
        owner_id = binding.get("owner_id")
        project_id = binding.get("project_id")
        if not owner_id or not project_id:
            raise _http(400, "invalid_workspace", "Workspace is not bound to a project")
        root = userspace.project_dir(owner_id, project_id)
    else:
        from backend.core.workspace_manager import get_project_manager_for_workspace

        project = get_project_manager_for_workspace(binding.get("workspace_id") or "")
        if not project.is_open:
            raise _http(400, "invalid_workspace", "No project opened in this workspace")
        root = Path(project.root_path)

    return await _ingest_from_directory(
        user_id,
        root,
        source="project_save",
        name=sanitize_template_name(name),
        fallback_name=name,
        category=category,
        description=description,
        author=_author_name(actor),
    )


async def save_template_gen(
    actor: dict | None,
    *,
    session_id: str,
    name: str,
    category: str | None,
    description: str,
) -> dict:
    """template_gen 会话产物入库（剔除 PDF/trace/编译辅助文件后复制）。"""
    user_id = _actor_id(actor)
    cfg = get_config()
    legacy_root = Path(cfg.features.template_gen.output_dir)
    if not legacy_root.is_absolute():
        legacy_root = Path.cwd() / legacy_root
    output_root = userspace.user_resource_root("templates", legacy_root)
    try:
        sandbox = load_sandbox(str(output_root), session_id)
    except ValueError as e:
        raise _http(400, "invalid_session", str(e)) from e
    if not sandbox.is_dir():
        raise _http(404, "not_found", "Template generation session not found")

    return await _ingest_from_directory(
        user_id,
        sandbox,
        source="template_gen",
        name=sanitize_template_name(name),
        fallback_name=name,
        category=category,
        description=description,
        author=_author_name(actor),
        extra_names=(TRACE_FILE,),
        extra_suffixes=(".pdf",),
    )


async def _ingest_from_directory(
    user_id: str | None,
    source_root: Path,
    *,
    source: str,
    name: str | None,
    fallback_name: str,
    category: str | None,
    description: str,
    author: str,
    extra_names: tuple[str, ...] = (),
    extra_suffixes: tuple[str, ...] = (),
) -> dict:
    """清洗复制源目录 → 自动生成 manifest → 衍生配额校验 → 入库 → 记账。"""
    ignore = _make_clean_ignore(extra_names=extra_names, extra_suffixes=extra_suffixes)
    staging = Path(tempfile.mkdtemp(prefix="template_ingest_"))
    try:
        stage_root = staging / "template"
        await asyncio.to_thread(
            shutil.copytree, source_root, stage_root, ignore=ignore, symlinks=False
        )
        # manifest 构造含 rglob 全树扫描与逐文件读取探测主文件，入线程池
        manifest = await asyncio.to_thread(
            _manifest_from_source,
            stage_root,
            source=source,
            name=name,
            fallback_name=fallback_name,
            category=category,
            description=description,
            author=author,
        )
        size = await asyncio.to_thread(lambda: dir_size(stage_root))
        await asyncio.to_thread(
            enforce_derived_quota_for_current_user, size, "templates"
        )
        summary = await asyncio.to_thread(
            user_store.create_template, user_id, stage_root, manifest
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    await asyncio.to_thread(checkpoint_derived_usage_for_current_user, "templates")
    return {"template": summary.model_dump()}
