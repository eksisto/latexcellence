"""
写作助手的助手提示词管理。

职责：
    1. 管理 data/prompts/writing_chat/ 目录下的提示词文件（每个文件 = 一个助手）
    2. 提供 CRUD 操作：list / load / save / delete
    3. 管理"当前激活的助手"（通过 _active.txt marker file）

存储策略：与 expression_opt 的 prompt_manager 同一模式。
"""

import re
from pathlib import Path


from backend.core.config import get_config
from backend.core.logger import get_logger

_SAFE_NAME_RE = re.compile(r"^[\w-]+$")


def _validate_prompt_name(name: str, *, allow_empty: bool = False) -> str:
    """校验提示词名称可安全用作文件名，防止路径穿越。

    只允许字母/数字/下划线/连字符（\\w 覆盖中文等 Unicode 字符），
    拒绝路径分隔符、'..'、空字节与 Windows 保留字符。
    allow_empty=True 供 set_active 的"取消激活"场景使用。
    """
    if not isinstance(name, str):
        raise ValueError("Prompt name must be a string")
    name = name.strip()
    if allow_empty and not name:
        return name
    if not name:
        raise ValueError("Prompt name must not be empty")
    if "\x00" in name:
        raise ValueError("Prompt name contains illegal characters")
    if not _SAFE_NAME_RE.fullmatch(name):
        raise ValueError(
            "Prompt name may only contain letters, digits, underscores and hyphens"
        )
    return name


class WritingChatPromptManager:
    _ACTIVE_FILE = "_active.txt"

    def __init__(self):
        self._logger = get_logger("writing_chat.prompt_manager")

    def _get_dir(self) -> Path:
        cfg = get_config()
        d = Path(cfg.features.writing_chat.custom_prompts_dir)
        if not d.is_absolute():
            d = Path.cwd() / d
        d.mkdir(parents=True, exist_ok=True)
        return d

    def list_prompts(self) -> list[dict[str, str]]:
        prompt_dir = self._get_dir()
        active_name = self._read_active()
        result = []
        if prompt_dir.exists():
            for f in sorted(prompt_dir.glob("*.txt")):
                if f.name == self._ACTIVE_FILE:
                    continue
                name = f.stem
                result.append({"name": name, "active": name == active_name})
        return result

    def load_prompt(self, name: str) -> str | None:
        prompt_dir = self._get_dir()
        name = _validate_prompt_name(name)
        file_path = prompt_dir / f"{name}.txt"
        if not file_path.exists():
            return None
        return file_path.read_text(encoding="utf-8").strip()

    def save_prompt(self, name: str, content: str) -> None:
        prompt_dir = self._get_dir()
        name = _validate_prompt_name(name)
        file_path = prompt_dir / f"{name}.txt"
        file_path.write_text(content, encoding="utf-8")
        self._logger.info(f"Saved writing_chat prompt: {name}")

    def delete_prompt(self, name: str) -> bool:
        prompt_dir = self._get_dir()
        name = _validate_prompt_name(name)
        file_path = prompt_dir / f"{name}.txt"
        if not file_path.exists():
            return False
        file_path.unlink()
        return True

    def set_active(self, name: str) -> None:
        prompt_dir = self._get_dir()
        name = _validate_prompt_name(name, allow_empty=True)
        active_file = prompt_dir / self._ACTIVE_FILE
        active_file.write_text(name, encoding="utf-8")
        self._logger.info(f"Set active writing_chat prompt: {name or 'default'}")

    def get_active_prompt_content(self) -> str | None:
        active_name = self._read_active()
        if not active_name:
            return None
        return self.load_prompt(active_name)

    def get_active_name(self) -> str | None:
        return self._read_active()

    def _read_active(self) -> str | None:
        prompt_dir = self._get_dir()
        active_file = prompt_dir / self._ACTIVE_FILE
        if not active_file.exists():
            return None
        return active_file.read_text(encoding="utf-8").strip()


_instance: WritingChatPromptManager | None = None


def get_prompt_manager() -> WritingChatPromptManager:
    global _instance
    if _instance is None:
        _instance = WritingChatPromptManager()
    return _instance
