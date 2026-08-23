"""style_modify Agent 会话存储与候选文件管理。

会话目录位于应用管理的缓存目录（默认 data/style_modify_sessions/），不进入用户项目。
真实项目始终只读；候选文件只写入 session baseline/overlay，shadow 目录用于隔离
编译。proposal 确认后通过 LatexProject 的公开批量事务接口写回真实项目。
"""

import asyncio
import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime
from difflib import unified_diff
from pathlib import Path
from typing import Any

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.multiuser.quota import (
    checkpoint_derived_usage_for_current_user,
    enforce_derived_quota_for_current_user,
)
from backend.core.workspace_manager import get_project_manager_for_workspace
from backend.modules.style_modify.models import (
    CompileError,
    CompileSummary,
    OperationFile,
    OperationMeta,
    Proposal,
    SessionMeta,
    StagedFile,
)
from backend.modules.style_modify.trace import forget_trace_lock, read_trace
from backend.utils.file_ops import atomic_write, compute_md5, safe_read
from backend.utils.latex_json_guard import validate_latex_field
from backend.utils.path_guard import resolve_project_path
from backend.utils.tex_compile import run_tex_compile

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_FILE_CTX_RE = re.compile(r"\(\s*((?:\.{1,2}/)?[\w./\-]+\.tex)\b")
_LINE_INFO_RE = re.compile(r"l\.(\d+)\s?(.*)")

# 任意层级都应忽略的通用目录（缓存/版本控制/虚拟环境等）
_IGNORED_DIRS_ANY_DEPTH = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
}
# 仅项目根层级忽略的应用运行时目录。这些是应用自身在项目根下生成的
# 缓存/产物容器（运行时数据统一放在 data/ 下）；但用户项目中嵌套的、
# 名称相同的合法源码目录（如 thesis/data/、book/data/）必须保留，否则
# Agent 会看不到它们，shadow 编译也会因缺文件产生误导性失败。
_IGNORED_APP_DIRS = {
    "static",
    "dist",
    "build",
    "logs",
    "data",
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_sessions_root(cfg: Any | None = None) -> Path:
    """返回 style_modify 会话根目录（应用缓存目录，不是用户项目）。

    多用户模式：按当前用户隔离到 <users_root>/<user_id>/style_modify_sessions/；
    单用户模式：返回存量全局路径（cfg.features.style_modify.agent.sessions_dir），行为不变。
    """
    from backend.core.multiuser import userspace

    cfg = cfg or get_config()
    legacy_root = Path(cfg.features.style_modify.agent.sessions_dir)
    if not legacy_root.is_absolute():
        legacy_root = Path.cwd() / legacy_root
    return userspace.user_resource_root("style_modify_sessions", legacy_root).resolve()


def validate_session_id(session_id: str) -> None:
    if not session_id or not session_id.strip():
        raise ValueError("session_id cannot be empty")
    if not SESSION_ID_RE.match(session_id):
        raise ValueError("session_id contains invalid characters")


def validate_operation_id(operation_id: str) -> None:
    if not operation_id or not operation_id.strip():
        raise ValueError("operation_id cannot be empty")
    if not SESSION_ID_RE.match(operation_id):
        raise ValueError("operation_id contains invalid characters")


def project_fingerprint(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()


def load_session_dir(root: Path, session_id: str) -> Path:
    validate_session_id(session_id)
    session_dir = (root / session_id).resolve()
    if not session_dir.is_relative_to(root.resolve()):
        raise ValueError("session_id resolves outside the session root")
    if not session_dir.is_dir():
        raise ValueError(f"Session not found: {session_id}")
    return session_dir


def _write_meta(meta_path: Path, meta: SessionMeta) -> None:
    atomic_write(
        meta_path,
        meta.model_dump_json(indent=2, ensure_ascii=False),
    )


def _read_meta(meta_path: Path) -> SessionMeta:
    if not meta_path.exists():
        raise ValueError("Session meta not found")
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    return SessionMeta.model_validate(data)


def _safe_rel_path(rel_path: str) -> Path:
    if not rel_path or not rel_path.strip():
        raise ValueError("file_path must not be empty")
    if Path(rel_path).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", rel_path):
        raise ValueError("file_path must not be an absolute path")
    path = Path(rel_path)
    if ".." in path.parts:
        raise ValueError("file_path must not contain '..' segments")
    return path


def _allowed_project_path(parts: tuple[str, ...]) -> bool:
    """判断相对路径是否允许进入 Agent 视野 / shadow 副本。

    通用忽略目录在任何层级生效；应用运行时目录只在项目根层级忽略，
    嵌套的同名目录（如 thesis/data/）视为普通项目源码保留。
    """
    parent_parts = parts[:-1]
    if any(
        part in _IGNORED_DIRS_ANY_DEPTH or part.startswith(".") for part in parent_parts
    ):
        return False
    if parent_parts and parent_parts[0] in _IGNORED_APP_DIRS:
        return False
    return True


def _diff_text(original: str, modified: str, rel_path: str) -> str:
    from_lines = original.splitlines(keepends=True)
    to_lines = modified.splitlines(keepends=True)
    diff_lines = list(
        unified_diff(
            from_lines,
            to_lines,
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
        )
    )
    return "".join(diff_lines)


def _detect_main_file(shadow: Path) -> str | None:
    main = shadow / "main.tex"
    if main.exists():
        return "main.tex"
    candidates = sorted(shadow.rglob("*.tex"))
    for candidate in candidates:
        if candidate.name.startswith("."):
            continue
        try:
            content = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\\documentclass" in content:
            return candidate.relative_to(shadow).as_posix()
    return candidates[0].relative_to(shadow).as_posix() if candidates else None


def _trim_log(log: str, success: bool) -> str:
    if success:
        tail = log[-1500:] if len(log) > 1500 else log
        return "Compilation succeeded.\n\n" + tail
    lines = log.splitlines()
    err_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("!") or "Fatal error" in line or "Error:" in line:
            err_idx = i
            break
    if err_idx >= 0:
        start = max(0, err_idx - 10)
        end = min(len(lines), err_idx + 40)
        snippet = "\n".join(lines[start:end])
    else:
        snippet = log[-4000:] if len(log) > 4000 else log
    if len(snippet) > 4000:
        snippet = snippet[:4000] + "\n...[log truncated]"
    return "Compilation FAILED.\n\n" + snippet


def _extract_errors(log: str) -> list[CompileError]:
    errors: list[CompileError] = []
    lines = log.splitlines()
    last_file: str | None = None
    for i, line in enumerate(lines):
        for m in _FILE_CTX_RE.finditer(line):
            raw = m.group(1).lstrip("./")
            last_file = raw or last_file
        if not line.startswith("!"):
            continue
        err = CompileError(
            type=line[1:].strip(),
            file=last_file,
            line=None,
            detail="",
        )
        for j in range(i + 1, min(len(lines), i + 15)):
            m_line = _LINE_INFO_RE.match(lines[j])
            if m_line:
                err.line = int(m_line.group(1))
                err.detail = m_line.group(2).strip()
                break
            if lines[j].startswith("!"):
                break
        errors.append(err)
        if len(errors) >= 8:
            break
    return errors


class StyleModifySessionStore:
    """会话目录、overlay、proposal 和 operation 的持久化。"""

    def __init__(self) -> None:
        self._logger = get_logger("style_modify.session")
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    def guard(self, session_id: str):
        """会话互斥守卫（v2 P7）：进程内 asyncio.Lock + 跨进程 flock 叠加。

        flock 锁文件位于会话目录旁（``<root>/<session_id>.lock``），同机
        共享卷上跨副本有效；单机行为与原 asyncio.Lock 一致。
        """
        from backend.utils.session_mutex import combined_session_lock

        session_dir = get_sessions_root() / session_id
        return combined_session_lock(session_dir, self.lock(session_id))

    # ── 会话创建 / 读取 ─────────────────────────────────────────────

    def create_session(
        self,
        workspace_id: str,
        project_root: str,
        requirement: str,
        language: str = "zh",
    ) -> str:
        cfg = get_config()
        root = get_sessions_root(cfg)
        # 多用户模式：新建会话前检查衍生数据配额（超限抛 413，方案 §9.2）。
        # v2 P4：先对本分类做检查点增量刷新（仅扫 style_modify_sessions 目录）
        enforce_derived_quota_for_current_user(subdir="style_modify_sessions")
        session_id = uuid.uuid4().hex[:12]
        session_dir = root / session_id
        (session_dir / "baseline").mkdir(parents=True, exist_ok=True)
        (session_dir / "overlay").mkdir(parents=True, exist_ok=True)
        (session_dir / "shadow").mkdir(parents=True, exist_ok=True)

        meta = SessionMeta(
            session_id=session_id,
            workspace_id=workspace_id,
            project_root=str(Path(project_root).resolve()),
            project_fingerprint=project_fingerprint(Path(project_root)),
            status="created",
            requirement=requirement,
            language=language,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        _write_meta(session_dir / "meta.json", meta)
        # v2 P4 检查点：新会话落盘后刷新该用户该分类的用量缓存行
        checkpoint_derived_usage_for_current_user("style_modify_sessions")
        self._logger.info(
            "style_modify session created: %s workspace=%s project=%s",
            session_id,
            workspace_id,
            meta.project_root,
        )
        return session_id

    def load_meta(self, session_id: str) -> SessionMeta:
        root = get_sessions_root()
        session_dir = load_session_dir(root, session_id)
        return _read_meta(session_dir / "meta.json")

    def save_meta(self, session_id: str, meta: SessionMeta) -> None:
        root = get_sessions_root()
        session_dir = load_session_dir(root, session_id)
        meta.updated_at = now_iso()
        _write_meta(session_dir / "meta.json", meta)

    def require_workspace(self, meta: SessionMeta, workspace_id: str) -> None:
        if meta.workspace_id != workspace_id:
            raise ValueError("Session belongs to a different workspace")

    def session_dir(self, session_id: str) -> Path:
        return load_session_dir(get_sessions_root(), session_id)

    # ── 项目浏览工具 ────────────────────────────────────────────────

    def list_project_files(
        self,
        session_id: str,
        *,
        max_results: int = 500,
    ) -> list[dict[str, Any]]:
        meta = self.load_meta(session_id)
        cfg = get_config()
        allowed = tuple(cfg.features.style_modify.agent.allowed_suffixes)
        root = Path(meta.project_root).resolve()
        results: list[dict[str, Any]] = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix.lower() not in allowed:
                continue
            rel = p.relative_to(root).as_posix()
            parts = Path(rel).parts
            if not _allowed_project_path(parts):
                continue
            if os.path.islink(p):
                continue
            results.append({"path": rel, "size": p.stat().st_size})
            if len(results) >= max_results:
                break
        return results

    def search_project_text(
        self,
        session_id: str,
        query: str,
        *,
        max_results: int = 40,
    ) -> list[dict[str, Any]]:
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        if len(query) > 200:
            raise ValueError("query is too long")
        cfg = get_config()
        allowed = tuple(cfg.features.style_modify.agent.allowed_suffixes)
        max_chars = cfg.features.style_modify.agent.max_tool_output_chars
        meta = self.load_meta(session_id)
        root = Path(meta.project_root).resolve()
        needle = query.strip().casefold()
        results: list[dict[str, Any]] = []
        total_chars = 0
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in allowed:
                continue
            rel = p.relative_to(root).as_posix()
            parts = Path(rel).parts
            if not _allowed_project_path(parts):
                continue
            if os.path.islink(p):
                continue
            try:
                content = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line_no, line in enumerate(content.splitlines(), 1):
                if needle in line.casefold():
                    trimmed = line.strip()
                    if len(trimmed) > 300:
                        trimmed = trimmed[:300] + "..."
                    results.append({"path": rel, "line": line_no, "text": trimmed})
                    total_chars += len(trimmed) + len(rel) + 16
                    if len(results) >= max_results or total_chars >= max_chars:
                        return results
        return results

    def read_project_file(
        self,
        session_id: str,
        file_path: str,
    ) -> dict[str, Any]:
        meta = self.load_meta(session_id)
        cfg = get_config()
        allowed = tuple(cfg.features.style_modify.agent.allowed_suffixes)
        max_bytes = cfg.features.style_modify.agent.max_file_size_kb * 1024
        root = Path(meta.project_root).resolve()
        resolved = resolve_project_path(
            root,
            file_path,
            allowed_suffixes=allowed,
            allow_symlink=False,
        )
        if not resolved.exists() or not resolved.is_file():
            raise ValueError(f"File not found: {file_path}")
        if resolved.stat().st_size > max_bytes:
            raise ValueError(f"File too large: {file_path}")
        content = safe_read(resolved)
        return {
            "path": file_path,
            "content": content,
        }

    # ── overlay 候选修改 ────────────────────────────────────────────

    def stage_replace(
        self,
        session_id: str,
        file_path: str,
        content: str,
    ) -> StagedFile:
        meta = self.load_meta(session_id)
        cfg = get_config()
        allowed = tuple(cfg.features.style_modify.agent.allowed_suffixes)
        max_bytes = cfg.features.style_modify.agent.max_file_size_kb * 1024
        root = Path(meta.project_root).resolve()
        resolved = resolve_project_path(
            root,
            file_path,
            allowed_suffixes=allowed,
            allow_symlink=False,
        )
        if not resolved.exists() or not resolved.is_file():
            raise ValueError(f"File not found: {file_path}")
        if not content.strip():
            raise ValueError("Candidate content must not be empty")
        if len(content.encode("utf-8")) > max_bytes:
            raise ValueError(
                f"File {file_path} exceeds maximum size "
                f"of {cfg.features.style_modify.agent.max_file_size_kb} KB"
            )
        if resolved.suffix.lower() == ".bib":
            validate_latex_field(content, bibtex=True, relaxed=True)
        else:
            validate_latex_field(content, relaxed=True)

        original = safe_read(resolved)
        session_dir = self.session_dir(session_id)
        rel_path = Path(file_path)
        baseline_path = session_dir / "baseline" / rel_path
        overlay_path = session_dir / "overlay" / rel_path

        if not baseline_path.exists():
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(baseline_path, original)

        total_size = 0
        for folder in ("baseline", "overlay"):
            folder_path = session_dir / folder
            for p in folder_path.rglob("*"):
                if p.is_file():
                    total_size += p.stat().st_size
        existing_overlay_size = (
            overlay_path.stat().st_size if overlay_path.exists() else 0
        )
        total_size = total_size - existing_overlay_size + len(content.encode("utf-8"))
        max_session_bytes = cfg.features.style_modify.agent.max_session_size_kb * 1024
        if total_size > max_session_bytes:
            raise ValueError(
                f"Session candidate size exceeds "
                f"{cfg.features.style_modify.agent.max_session_size_kb} KB"
            )
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(overlay_path, content)

        staged = StagedFile(
            path=file_path,
            baseline_md5=compute_md5(baseline_path),
            candidate_md5=compute_md5(overlay_path),
            baseline_size=len(original.encode("utf-8")),
            candidate_size=len(content.encode("utf-8")),
            diff=_diff_text(original, content, file_path),
            changed=original != content,
        )
        self._logger.info(
            "style_modify staged %s (%d -> %d bytes)",
            file_path,
            staged.baseline_size,
            staged.candidate_size,
        )
        return staged

    def discard_file_changes(self, session_id: str, file_path: str) -> str:
        meta = self.load_meta(session_id)
        _safe_rel_path(file_path)
        session_dir = self.session_dir(session_id)
        overlay_path = session_dir / "overlay" / file_path
        if overlay_path.exists():
            overlay_path.unlink()
        self.save_meta(session_id, meta)
        return f"Discarded candidate changes for {file_path}"

    def list_staged_files(self, session_id: str) -> list[StagedFile]:
        session_dir = self.session_dir(session_id)
        baseline_root = session_dir / "baseline"
        overlay_root = session_dir / "overlay"
        if not baseline_root.exists():
            return []
        files: list[StagedFile] = []
        for baseline_path in sorted(baseline_root.rglob("*")):
            if not baseline_path.is_file():
                continue
            rel_path = baseline_path.relative_to(baseline_root)
            overlay_path = overlay_root / rel_path
            if not overlay_path.exists():
                continue
            original = safe_read(baseline_path)
            modified = safe_read(overlay_path)
            if original == modified:
                continue
            files.append(
                StagedFile(
                    path=rel_path.as_posix(),
                    baseline_md5=compute_md5(baseline_path),
                    candidate_md5=compute_md5(overlay_path),
                    baseline_size=len(original.encode("utf-8")),
                    candidate_size=len(modified.encode("utf-8")),
                    diff=_diff_text(original, modified, rel_path.as_posix()),
                    changed=original != modified,
                )
            )
        return files

    # ── shadow 编译 ─────────────────────────────────────────────────

    async def build_shadow(self, session_id: str) -> Path:
        meta = self.load_meta(session_id)
        root = Path(meta.project_root).resolve()
        session_dir = self.session_dir(session_id)
        shadow = session_dir / "shadow"
        await asyncio.to_thread(_copy_project, root, shadow)

        overlay_root = session_dir / "overlay"
        if overlay_root.exists():
            for p in sorted(overlay_root.rglob("*")):
                if not p.is_file():
                    continue
                rel = p.relative_to(overlay_root)
                target = shadow / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(target, safe_read(p))
        return shadow

    async def compile_staged_project(
        self,
        session_id: str,
        main_file: str | None = None,
    ) -> CompileSummary:
        meta = self.load_meta(session_id)
        shadow = await self.build_shadow(session_id)
        project = get_project_manager_for_workspace(meta.workspace_id)
        if main_file is None and project.is_open and project.main_file:
            main_file = project.main_file
        if main_file is None:
            main_file = _detect_main_file(shadow)
        if main_file is None:
            return CompileSummary(
                status="not_run",
                log="Could not determine the LaTeX main file for shadow compilation.",
            )

        # main_file 来自 LLM 工具参数，必须先经 resolve_project_path 校验：
        # 拒绝绝对路径 / '..' 越界 / 非 .tex 后缀，避免编译 shadow 目录外文件。
        try:
            resolved_main = resolve_project_path(
                shadow.resolve(),
                main_file,
                allowed_suffixes=(".tex",),
                allow_symlink=False,
            )
        except ValueError as e:
            self._logger.warning(
                "style_modify shadow compile rejected main_file %r: %s",
                main_file,
                e,
            )
            return CompileSummary(
                status="not_run",
                main_file=main_file,
                log=f"Invalid main file path: {e}",
            )
        if not resolved_main.is_file():
            return CompileSummary(
                status="not_run",
                main_file=main_file,
                log="Could not determine the LaTeX main file for shadow compilation.",
            )
        main_file = resolved_main.relative_to(shadow.resolve()).as_posix()

        cfg = get_config()
        timeout = cfg.features.style_modify.agent.shadow_compile_timeout
        self._logger.info(
            "style_modify shadow compile | session=%s main=%s",
            session_id,
            main_file,
        )
        try:
            tex_result = await run_tex_compile(
                main_file,
                shadow,
                timeout,
                extra_args=["-halt-on-error"],
            )
        except FileNotFoundError:
            return CompileSummary(
                status="not_run",
                main_file=main_file,
                log="latexmk is not installed or is not on PATH.",
            )

        if tex_result.timed_out:
            return CompileSummary(
                status="failed",
                main_file=main_file,
                log=f"Compilation timed out after {timeout}s.",
                timed_out=True,
            )

        status = "passed" if tex_result.success else "failed"
        return CompileSummary(
            status=status,
            main_file=main_file,
            log=_trim_log(tex_result.raw_output, tex_result.success),
            errors=(
                _extract_errors(tex_result.raw_output) if not tex_result.success else []
            ),
            timed_out=False,
        )

    # ── proposal ────────────────────────────────────────────────────

    def make_proposal(
        self,
        session_id: str,
        summary: str,
        compile_summary: CompileSummary,
    ) -> Proposal:
        meta = self.load_meta(session_id)
        files = self.list_staged_files(session_id)
        revision = meta.latest_revision + 1
        blocked: list[str] = []
        if not files:
            blocked.append("No staged changes")
        proposal = Proposal(
            revision=revision,
            requirement=meta.requirement,
            refinements=list(meta.refinements),
            summary=summary,
            files=files,
            compile=compile_summary,
            created_at=now_iso(),
            applicable=not blocked,
            blocked_reasons=blocked,
        )
        meta.proposal = proposal
        meta.latest_revision = revision
        meta.status = "ready"
        self.save_meta(session_id, meta)
        return proposal

    # ── 事务应用 / 撤销 ─────────────────────────────────────────────

    def apply_proposal(
        self,
        session_id: str,
        workspace_id: str,
        revision: int,
    ) -> OperationMeta:
        meta = self.load_meta(session_id)
        self.require_workspace(meta, workspace_id)
        if meta.proposal is None:
            raise ValueError("No proposal for this session")
        if meta.proposal.revision != revision:
            raise ValueError("Proposal revision does not match the latest proposal")
        if meta.latest_revision != revision:
            raise ValueError("Only the latest proposal revision can be applied")
        if meta.status == "applied" and meta.operation_id:
            return self.load_operation(meta.operation_id)
        if meta.status == "applying":
            raise ValueError("Session is already applying a proposal")
        if not meta.proposal.files:
            raise ValueError("No staged changes to apply")

        cfg = get_config()
        allowed = tuple(cfg.features.style_modify.agent.allowed_suffixes)
        project = get_project_manager_for_workspace(workspace_id)
        if not project.is_open:
            raise ValueError("No project opened")
        root = Path(project.root_path).resolve()
        if str(root) != str(Path(meta.project_root).resolve()):
            raise ValueError("Project root has changed since the proposal was created")
        if project_fingerprint(root) != meta.project_fingerprint:
            raise ValueError("Project fingerprint mismatch")

        session_dir = self.session_dir(session_id)
        updates: dict[str, str] = {}
        expected_md5s: dict[str, str] = {}
        for file_item in meta.proposal.files:
            resolved = resolve_project_path(
                root,
                file_item.path,
                allowed_suffixes=allowed,
                allow_symlink=False,
            )
            if not resolved.exists():
                raise ValueError(f"File not found: {file_item.path}")
            current_md5 = compute_md5(resolved)
            if current_md5 != file_item.baseline_md5:
                raise ValueError(
                    f"File {file_item.path} has been modified since the proposal "
                    "was created; refresh and regenerate the proposal"
                )
            overlay_path = session_dir / "overlay" / file_item.path
            if not overlay_path.exists():
                raise ValueError(f"Candidate file missing: {file_item.path}")
            candidate = safe_read(overlay_path)
            if file_item.path.lower().endswith(".bib"):
                validate_latex_field(candidate, bibtex=True, relaxed=True)
            else:
                validate_latex_field(candidate, relaxed=True)
            updates[file_item.path] = candidate
            expected_md5s[file_item.path] = file_item.baseline_md5

        op_id = uuid.uuid4().hex[:12]
        operations_root = get_sessions_root(cfg) / "operations"
        op_dir = operations_root / op_id
        before_dir = op_dir / "before"
        applied_dir = op_dir / "applied"
        before_dir.mkdir(parents=True, exist_ok=True)
        applied_dir.mkdir(parents=True, exist_ok=True)

        op_files: list[OperationFile] = []
        for file_item in meta.proposal.files:
            before_path = session_dir / "baseline" / file_item.path
            overlay_path = session_dir / "overlay" / file_item.path
            before_content = safe_read(before_path)
            applied_content = safe_read(overlay_path)
            target_before = before_dir / file_item.path
            target_applied = applied_dir / file_item.path
            target_before.parent.mkdir(parents=True, exist_ok=True)
            target_applied.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(target_before, before_content)
            atomic_write(target_applied, applied_content)
            op_files.append(
                OperationFile(
                    path=file_item.path,
                    baseline_md5=file_item.baseline_md5,
                    applied_md5=file_item.candidate_md5,
                    status="applied",
                )
            )

        op_meta = OperationMeta(
            operation_id=op_id,
            session_id=session_id,
            workspace_id=workspace_id,
            project_root=str(root),
            project_fingerprint=meta.project_fingerprint,
            revision=revision,
            status="applying",
            created_at=now_iso(),
            files=op_files,
        )
        _write_meta(op_dir / "meta.json", op_meta)
        meta.status = "applying"
        self.save_meta(session_id, meta)

        try:
            project.apply_whole_files(
                updates,
                expected_md5s=expected_md5s,
                allowed_suffixes=allowed,
            )
        except Exception as exc:
            self._logger.error("style_modify apply failed: %s", exc)
            op_meta.status = "failed"
            op_meta.applied_at = now_iso()
            for f in op_meta.files:
                f.status = "failed"
            _write_meta(op_dir / "meta.json", op_meta)
            meta.status = "ready"
            self.save_meta(session_id, meta)
            raise

        op_meta.status = "applied"
        op_meta.applied_at = now_iso()
        _write_meta(op_dir / "meta.json", op_meta)
        meta.operation_id = op_id
        meta.applied_revision = revision
        meta.status = "applied"
        self.save_meta(session_id, meta)
        self._logger.info("style_modify applied operation: %s", op_id)
        return op_meta

    def undo_operation(self, operation_id: str, workspace_id: str) -> OperationMeta:
        op_meta = self.load_operation(operation_id)
        if op_meta.workspace_id != workspace_id:
            raise ValueError("Operation belongs to a different workspace")
        if op_meta.status == "undone":
            return op_meta
        if op_meta.status != "applied":
            raise ValueError("Operation is not in applied state")

        cfg = get_config()
        allowed = tuple(cfg.features.style_modify.agent.allowed_suffixes)
        project = get_project_manager_for_workspace(workspace_id)
        if not project.is_open:
            raise ValueError("No project opened")
        root = Path(project.root_path).resolve()
        if str(root) != str(Path(op_meta.project_root).resolve()):
            raise ValueError("Project root has changed since the operation was applied")
        if project_fingerprint(root) != op_meta.project_fingerprint:
            raise ValueError("Project fingerprint mismatch")

        op_dir = get_sessions_root(cfg) / "operations" / operation_id
        updates: dict[str, str] = {}
        expected_md5s: dict[str, str] = {}
        for file_item in op_meta.files:
            resolved = resolve_project_path(
                root,
                file_item.path,
                allowed_suffixes=allowed,
                allow_symlink=False,
            )
            if not resolved.exists():
                raise ValueError(f"File not found: {file_item.path}")
            current_md5 = compute_md5(resolved)
            if current_md5 != file_item.applied_md5:
                raise ValueError(
                    f"File {file_item.path} was modified after it was applied; "
                    "refusing to overwrite newer changes"
                )
            before_path = op_dir / "before" / file_item.path
            if not before_path.exists():
                raise ValueError(f"Undo backup missing: {file_item.path}")
            updates[file_item.path] = safe_read(before_path)
            expected_md5s[file_item.path] = file_item.applied_md5

        project.apply_whole_files(
            updates,
            expected_md5s=expected_md5s,
            allowed_suffixes=allowed,
        )
        op_meta.status = "undone"
        op_meta.undone_at = now_iso()
        for f in op_meta.files:
            f.status = "undone"
        _write_meta(op_dir / "meta.json", op_meta)

        try:
            session_meta = self.load_meta(op_meta.session_id)
            session_meta.status = "ready"
            session_meta.operation_id = None
            session_meta.applied_revision = None
            self.save_meta(op_meta.session_id, session_meta)
        except ValueError:
            pass
        return op_meta

    def load_operation(self, operation_id: str) -> OperationMeta:
        validate_operation_id(operation_id)
        op_dir = get_sessions_root() / "operations" / operation_id
        meta_path = op_dir / "meta.json"
        if not meta_path.exists():
            raise ValueError(f"Operation not found: {operation_id}")
        return _read_operation_meta(meta_path)

    # ── 会话 / trace / 删除 ─────────────────────────────────────────

    def get_session(self, session_id: str, workspace_id: str) -> dict[str, Any]:
        meta = self.load_meta(session_id)
        self.require_workspace(meta, workspace_id)
        operation = None
        if meta.operation_id:
            try:
                operation = self.load_operation(meta.operation_id).model_dump()
            except ValueError:
                operation = None
        return {
            "session_id": meta.session_id,
            "workspace_id": meta.workspace_id,
            "project_root": meta.project_root,
            "status": meta.status,
            "requirement": meta.requirement,
            "language": meta.language,
            "created_at": meta.created_at,
            "updated_at": meta.updated_at,
            "latest_revision": meta.latest_revision,
            "applied_revision": meta.applied_revision,
            "proposal": (
                meta.proposal.model_dump() if meta.proposal is not None else None
            ),
            "files": [f.model_dump() for f in self.list_staged_files(session_id)],
            "operation": operation,
        }

    def get_trace(self, session_id: str, workspace_id: str) -> dict[str, Any]:
        meta = self.load_meta(session_id)
        self.require_workspace(meta, workspace_id)
        events = read_trace(self.session_dir(session_id))
        return {
            "session_id": session_id,
            "meta": {
                "requirement": meta.requirement,
                "language": meta.language,
                "status": meta.status,
                "latest_revision": meta.latest_revision,
            },
            "events": events,
        }

    def delete_session(self, session_id: str, workspace_id: str) -> bool:
        meta = self.load_meta(session_id)
        self.require_workspace(meta, workspace_id)
        if meta.status in ("running", "applying"):
            raise ValueError("Cannot delete a session that is running or applying")
        session_dir = self.session_dir(session_id)
        shutil.rmtree(session_dir)
        self._locks.pop(session_id, None)
        forget_trace_lock(session_dir)
        # v2 P7：清理会话目录旁的跨进程 flock 锁文件（幂等、尽力而为）
        from backend.utils.session_mutex import lock_file_for, remove_lock_file

        remove_lock_file(lock_file_for(session_dir))
        # v2 P4 检查点：删除触达 style_modify_sessions 分类，刷新用量缓存行
        checkpoint_derived_usage_for_current_user("style_modify_sessions")
        self._logger.info("style_modify session deleted: %s", session_id)
        return True


def _copy_project(root: Path, shadow: Path) -> None:
    if shadow.exists():
        shutil.rmtree(shadow)
    shadow.mkdir(parents=True, exist_ok=True)

    def ignore(directory: Path, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        directory_path = Path(directory)
        is_root = directory_path.resolve() == root.resolve()
        for name in names:
            if name in _IGNORED_DIRS_ANY_DEPTH or name.startswith("."):
                ignored.add(name)
                continue
            if is_root and name in _IGNORED_APP_DIRS:
                ignored.add(name)
                continue
            candidate = directory_path / name
            if os.path.islink(candidate):
                ignored.add(name)
        return ignored

    shutil.copytree(
        root,
        shadow,
        ignore=ignore,
        symlinks=False,
        dirs_exist_ok=True,
    )


def _read_operation_meta(meta_path: Path) -> OperationMeta:
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    return OperationMeta.model_validate(data)


_session_store: StyleModifySessionStore | None = None


def get_session_store() -> StyleModifySessionStore:
    global _session_store
    if _session_store is None:
        _session_store = StyleModifySessionStore()
    return _session_store
