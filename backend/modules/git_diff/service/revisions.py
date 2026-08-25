"""Git revision 列表与校验。"""

from pathlib import Path, PurePosixPath

from backend.core.config import get_config
from backend.core.workspace_manager import get_project_manager_for_workspace

from .constants import (
    _COMMIT_HASH_RE,
    _REF_FORBIDDEN_SUBSTRINGS,
    _REF_NAME_RE,
    WORKTREE_REF,
)
from .models import GitRefsResult, GitRevision, ProjectContext
from . import process


async def list_git_revisions(workspace_id: str = "") -> GitRefsResult:
    """列出当前项目可选的 Git revision。"""

    context = await _get_project_context(workspace_id)
    current_branch = await process._get_optional_git_output(
        context.git_root,
        ["branch", "--show-current"],
    )
    status = await process._get_optional_git_output(
        context.git_root,
        ["status", "--porcelain"],
    )

    revisions = [
        GitRevision(
            value=WORKTREE_REF,
            label="Current worktree",
            kind="worktree",
            subject="Contains uncommitted changes and untracked files",
        )
    ]
    revisions.extend(await _list_commit_revisions(context.git_root))
    revisions.extend(
        await _list_ref_revisions(context.git_root, "refs/heads", "branch")
    )
    revisions.extend(await _list_ref_revisions(context.git_root, "refs/tags", "tag"))

    deduped = []
    seen = set()
    for revision in revisions:
        key = (revision.kind, revision.value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(revision)

    return GitRefsResult(
        # 多用户模式不向用户暴露服务器上的 Git 仓库绝对路径
        git_root=None if get_config().multi_user.enabled else str(context.git_root),
        current_branch=current_branch.strip(),
        is_dirty=bool(status.strip()),
        revisions=deduped,
    )


async def _get_project_context(workspace_id: str = "") -> ProjectContext:
    project = get_project_manager_for_workspace(workspace_id)
    if not project.is_open:
        raise RuntimeError("No project opened")

    root = Path(project.root_path).resolve()
    git_root_output = await process._run_git_checked(
        root, ["rev-parse", "--show-toplevel"]
    )
    git_root = Path(git_root_output.stdout.strip()).resolve()

    try:
        relative_prefix = root.relative_to(git_root)
    except ValueError as exc:
        raise RuntimeError("Opened project is not inside its Git repository") from exc

    prefix = PurePosixPath(relative_prefix.as_posix())
    if prefix.as_posix() == ".":
        prefix = PurePosixPath("")

    return ProjectContext(root=root, git_root=git_root, project_prefix=prefix)


async def _list_commit_revisions(git_root: Path) -> list[GitRevision]:
    result = await process._run_git(
        git_root,
        ["log", "--pretty=format:%H%x00%h%x00%s", "-n", "50"],
        timeout=30,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        # 全新仓库还没有任何提交，返回空列表（只保留 worktree 可选）
        if "does not have any commits yet" in stderr:
            return []
        raise RuntimeError(stderr or result.stdout.strip() or "Git log failed")

    revisions = []
    for line in result.stdout.splitlines():
        parts = line.split("\x00")
        if len(parts) < 3:
            continue
        full_hash, short_hash, subject = parts[0], parts[1], parts[2]
        revisions.append(
            GitRevision(
                value=full_hash,
                label=f"{short_hash} {subject}",
                kind="commit",
                short_hash=short_hash,
                subject=subject,
            )
        )
    return revisions


async def _list_ref_revisions(
    git_root: Path, ref_prefix: str, kind: str
) -> list[GitRevision]:
    result = await process._run_git_checked(
        git_root,
        [
            "for-each-ref",
            ref_prefix,
            "--format=%(refname:short)%00%(objectname)%00%(objectname:short)%00%(subject)",
        ],
    )
    revisions = []
    for line in result.stdout.splitlines():
        parts = line.split("\x00")
        if len(parts) < 4:
            continue
        name, full_hash, short_hash, subject = parts[0], parts[1], parts[2], parts[3]
        revisions.append(
            GitRevision(
                value=name,
                label=f"{name} ({short_hash})",
                kind=kind,
                short_hash=short_hash,
                subject=subject or full_hash,
            )
        )
    return revisions


async def _validate_revision(git_root: Path, ref: str) -> None:
    """校验 Git revision：只接受工作区标记、哈希或普通分支/标签名。

    拒绝任意 commit-ish（HEAD~1、refs/... 语法、range 等），并使用
    ``--end-of-options`` 隔离参数，防止以 ``-`` 开头的输入被当作 git 选项。
    """
    if ref == WORKTREE_REF:
        return
    if not ref or ref.strip() != ref:
        raise ValueError("Git revision must not be empty or padded with spaces")
    if ref.startswith("-"):
        raise ValueError("Git revision must not start with '-'")
    if not (_COMMIT_HASH_RE.fullmatch(ref) or _REF_NAME_RE.fullmatch(ref)):
        raise ValueError(f"Invalid Git revision: {ref}")
    if ref.endswith(("/", ".")) or any(
        token in ref for token in _REF_FORBIDDEN_SUBSTRINGS
    ):
        raise ValueError(f"Invalid Git revision: {ref}")
    result = await process._run_git(
        git_root,
        ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(f"Invalid Git revision: {ref}")
