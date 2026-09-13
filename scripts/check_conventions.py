#!/usr/bin/env python3
"""AGENTS.md 约束扫描器 — 检查 ruff 覆盖不到的三类项目约定。

1. pydantic-v1-*      禁用 Pydantic v1 API：parse_obj / parse_raw / @validator /
                      @root_validator / class Config（AGENTS.md：必须用 v2 的
                      model_validate / model_dump / ConfigDict / field_validator）。
2. path-guard-bypass  禁止绕过 path_guard 拼接项目内路径（AGENTS.md「路径安全」）：
                      `xxx / file_path`、`os.path.join(root, ...)`、
                      同行 resolve() + is_relative_to() 手动包含判断。
                      唯一合法入口是 backend/utils/path_guard.py；tests 不扫描。
3. doc-hardcoded-count 文档禁止硬编码文件/模块/组件数量（AGENTS.md「文档中的数量
                      描述规范」），防止结构变化后数字过时。

用法：python scripts/check_conventions.py
发现违规时输出 `路径:行号: [规则] 说明` 并以非零码退出；无输出即通过。
pre-commit 与 scripts/check.sh 均会调用本脚本。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── 检查 1：Pydantic v1 残留 ────────────────────────────────────────────────
PY_SCAN_DIRS = ("backend", "tests", "scripts")
PY_SCAN_FILES = ("main.py", "conftest.py")
# 本脚本 docstring 需要引用禁用模式本身
PY_V1_WHITELIST = {Path("scripts/check_conventions.py")}
PY_V1_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("pydantic-v1-parse_obj", re.compile(r"\.parse_obj\(")),
    ("pydantic-v1-parse_raw", re.compile(r"\.parse_raw\(")),
    ("pydantic-v1-validator", re.compile(r"@(root_)?validator\b")),
    ("pydantic-v1-class-config", re.compile(r"^\s+class Config:\s*$")),
]

# ── 检查 2：path_guard 绕过（仅生产代码；tests 允许构造反例）───────────────
PATH_SCAN_DIRS = ("backend",)
PATH_SCAN_FILES = ("main.py",)
PATH_WHITELIST = {Path("backend/utils/path_guard.py")}
# containment 白名单：对「非项目根」做防越界包含检查属合法用法——
# upload.py 校验解压根（防 zip-slip）、project_tools 校验 rglob 未越出项目，
# 二者是纵深防御而非绕过 path_guard 打开项目文件
PATH_CONTAINMENT_WHITELIST = {
    Path("backend/core/multiuser/upload.py"),
    Path("backend/utils/project_tools/manager.py"),
}
PATH_BYPASS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # root / file_path、root_path / file_path、Path(root) / file_path 等
    ("path-guard-bypass", re.compile(r"\w+\s*/\s*file_path\b")),
    ("path-guard-bypass-join", re.compile(r"os\.path\.join\(\s*root\s*,")),
    (
        "path-guard-bypass-containment",
        re.compile(r"\.resolve\(\).*\.is_relative_to\("),
    ),
]

# ── 检查 3：文档硬编码数量 ─────────────────────────────────────────────────
DOC_FILES = ("README.md", "DEVELOPMENT.md", "DESIGN.md", "PROJECT_SUMMARY.md")
DOC_DIR = "docs"
# 先屏蔽「上限 / 最多 / 返回 N 个」等 API 限值措辞（允许中间夹杂 ≤3 个非数字字符，
# 如「最多返回」），再匹配结构化计数——两段式比 lookbehind 链更稳，避免从数字
# 中间重新起配绕过豁免
DOC_COUNT_EXEMPT = re.compile(r"(?:上限|最多|至多|超过|返回)\D{0,3}\d+\s*个")
DOC_COUNT_PATTERN = re.compile(
    r"\d+\s*个\s*((?:功能)?模块|页面|组件|文件|函数|脚本|工具|镜像|文档|[Hh]ook)"
)


def iter_files(base: Path, dirs: tuple[str, ...], files: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for name in files:
        p = base / name
        if p.is_file():
            found.append(p)
    for d in dirs:
        found.extend(sorted((base / d).rglob("*.py")))
    return found


def scan_py_v1() -> list[str]:
    issues = []
    for p in iter_files(REPO_ROOT, PY_SCAN_DIRS, PY_SCAN_FILES):
        if p.relative_to(REPO_ROOT) in PY_V1_WHITELIST:
            continue
        for lineno, line in enumerate(
            p.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if line.lstrip().startswith("#"):
                continue
            for rule, pattern in PY_V1_PATTERNS:
                if pattern.search(line):
                    issues.append(
                        f"{p.relative_to(REPO_ROOT)}:{lineno}: [{rule}] "
                        f"使用 Pydantic v1 API，请改用 v2（model_validate / "
                        f"ConfigDict / field_validator）"
                    )
    return issues


def scan_path_bypass() -> list[str]:
    issues = []
    for p in iter_files(REPO_ROOT, PATH_SCAN_DIRS, PATH_SCAN_FILES):
        if p.relative_to(REPO_ROOT) in PATH_WHITELIST:
            continue
        for lineno, line in enumerate(
            p.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if line.lstrip().startswith("#"):
                continue
            for rule, pattern in PATH_BYPASS_PATTERNS:
                if rule == "path-guard-bypass-containment" and (
                    p.relative_to(REPO_ROOT) in PATH_CONTAINMENT_WHITELIST
                ):
                    continue
                if pattern.search(line):
                    issues.append(
                        f"{p.relative_to(REPO_ROOT)}:{lineno}: [{rule}] "
                        f"疑似绕过 path_guard 拼接项目内路径，请改用 "
                        f"resolve_project_path(root, file_path)"
                    )
    return issues


def scan_doc_counts() -> list[str]:
    issues = []
    doc_paths = [REPO_ROOT / name for name in DOC_FILES]
    doc_paths.extend(sorted((REPO_ROOT / DOC_DIR).rglob("*.md")))
    for p in doc_paths:
        if not p.is_file():
            continue
        for lineno, line in enumerate(
            p.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if DOC_COUNT_PATTERN.search(DOC_COUNT_EXEMPT.sub("N个", line)):
                issues.append(
                    f"{p.relative_to(REPO_ROOT)}:{lineno}: [doc-hardcoded-count] "
                    f"文档硬编码数量，请改为「多个/若干」或动态统计"
                )
    return issues


def main() -> int:
    issues = scan_py_v1() + scan_path_bypass() + scan_doc_counts()
    for issue in issues:
        print(issue)
    if issues:
        print(f"\n共 {len(issues)} 处违反项目约定，修复后重试。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
