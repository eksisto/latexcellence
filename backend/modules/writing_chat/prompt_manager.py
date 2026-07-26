"""
写作助手 Chat 的助手提示词管理。

职责：
    1. 管理 prompts/writing_chat/ 目录下的提示词文件（每个文件 = 一个助手）
    2. 提供 CRUD 操作：list / load / save / delete
    3. 管理"当前激活的助手"（通过 _active.txt marker file）

存储策略：与 expression_opt 的 prompt_manager 同一模式。
"""

from pathlib import Path


from backend.core.config import get_config
from backend.core.logger import get_logger


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
        file_path = prompt_dir / f"{name}.txt"
        if not file_path.exists():
            return None
        return file_path.read_text(encoding="utf-8").strip()

    def save_prompt(self, name: str, content: str) -> None:
        prompt_dir = self._get_dir()
        file_path = prompt_dir / f"{name}.txt"
        file_path.write_text(content, encoding="utf-8")
        self._logger.info(f"Saved writing_chat prompt: {name}")

    def delete_prompt(self, name: str) -> bool:
        prompt_dir = self._get_dir()
        file_path = prompt_dir / f"{name}.txt"
        if not file_path.exists():
            return False
        file_path.unlink()
        return True

    def set_active(self, name: str) -> None:
        prompt_dir = self._get_dir()
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
