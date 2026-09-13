#!/usr/bin/env python3
"""zcode PostToolUse hook：文档同步提醒（只读，不修改任何文件）。

本 hook 只做一件事：编辑 backend/、frontend/src/ 或配置文件时，经
additionalContext 注入一条文档同步提醒（maintain-latexcellence-docs /
latexcellence-config-sync）。

约定：
- stdin 是 hook JSON（含 tool_input.file_path）；解析失败一律静默放行（exit 0）。
- stdout 只允许严格 JSON（多余 key 会导致输出校验失败），仅在有提醒时输出。
- 仓库外的文件不做任何处理。

Windows 注意：command 经 shell 调用 python3，Windows 侧需改用 process 类型或
调整解释器名（见 docs/development.md「约束机器化」）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _find_file_path(obj: object) -> str:
    """在 hook payload 里递归找第一个 file_path 字段。"""
    if isinstance(obj, dict):
        v = obj.get("file_path")
        if isinstance(v, str) and v:
            return v
        for value in obj.values():
            found = _find_file_path(value)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_file_path(item)
            if found:
                return found
    return ""


def _doc_reminder(rel_posix: str) -> str | None:
    if rel_posix == "main.py" or rel_posix.startswith(("backend/", "frontend/src/")):
        return (
            f"文档同步提醒（maintain-latexcellence-docs）：刚修改了 {rel_posix}；"
            "若模块行为、API、SSE 事件或配置随之变化，完成任务前须同步更新 docs/ 对应文档。"
        )
    if rel_posix.startswith(("config.json", "config.example.json")):
        return (
            "配置同步提醒（latexcellence-config-sync）：修改了配置结构，必须按该 skill 检查单同步"
            "设置分区组件 / SettingsDialog.handleSave / WelcomePage.featureGroups / backend AppConfig。"
        )
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    raw = (payload.get("tool_input") or {}).get("file_path") or _find_file_path(payload)
    if not raw:
        return 0

    root = Path(
        os.environ.get("ZCODE_PROJECT_DIR") or Path(__file__).resolve().parent.parent
    ).resolve()
    try:
        rel = Path(raw).resolve().relative_to(root)
    except (ValueError, OSError):
        return 0  # 仓库外或无法解析：不处理

    reminder = _doc_reminder(rel.as_posix())
    if reminder:
        print(json.dumps({"additionalContext": reminder}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
