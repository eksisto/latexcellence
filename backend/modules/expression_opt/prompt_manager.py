"""
用户自定义提示词管理。

职责：
    1. 管理 prompts/expression/ 目录下的自定义提示词文件
    2. 提供 CRUD 操作：list / load / save / delete
    3. 管理"当前激活的提示词"（通过 marker file）

存储策略：
    - 每个提示词是一个 .txt 文件（UTF-8），文件名为 prompt name。
    - 目录通过 config.features.expression_opt.custom_prompts_dir 配置。
    - 当前激活的提示词通过一个特殊的 _active.txt 文件标识内容。
"""

import os
from pathlib import Path


from backend.core.config import get_config
from backend.core.logger import get_logger


class PromptManager:
    """
    提示词管理器（单例）。

    用法：
        pm = get_prompt_manager()
        names = pm.list_prompts()
        content = pm.load_prompt("academic-formal")
        pm.save_prompt("academic-formal", "请使用正式学术风格...")
        pm.delete_prompt("academic-formal")
        pm.set_active("academic-formal")
    """

    _ACTIVE_FILE = "_active.txt"

    def __init__(self):
        self._logger = get_logger("prompt_manager")

    def _get_dir(self) -> Path:
        cfg = get_config()
        prompt_dir = Path(cfg.features.expression_opt.custom_prompts_dir)
        if not prompt_dir.is_absolute():
            prompt_dir = Path.cwd() / prompt_dir
        prompt_dir.mkdir(parents=True, exist_ok=True)
        return prompt_dir

    def list_prompts(self) -> list[dict[str, str]]:
        """
        列出所有自定义提示词。

        返回：
            [{ "name": "academic-formal", "active": true }, ...]
        """
        prompt_dir = self._get_dir()
        active_name = self._read_active()
        result = []

        if prompt_dir.exists():
            for f in sorted(prompt_dir.glob("*.txt")):
                if f.name == self._ACTIVE_FILE:
                    continue
                name = f.stem
                result.append(
                    {
                        "name": name,
                        "active": name == active_name,
                    }
                )

        return result

    def load_prompt(self, name: str) -> str | None:
        """
        加载指定提示词内容。

        参数：
            name: 提示词名称（不含 .txt 后缀）

        返回：
            提示词文本内容，不存在时返回 None
        """
        prompt_dir = self._get_dir()
        file_path = prompt_dir / f"{name}.txt"
        if not file_path.exists():
            self._logger.warning(f"Prompt not found: {name}")
            return None
        return file_path.read_text(encoding="utf-8").strip()

    def save_prompt(self, name: str, content: str) -> None:
        """
        保存提示词（创建或覆盖）。

        参数：
            name: 提示词名称
            content: 提示词文本
        """
        prompt_dir = self._get_dir()
        file_path = prompt_dir / f"{name}.txt"
        file_path.write_text(content, encoding="utf-8")
        self._logger.info(f"Saved prompt: {name}")

    def delete_prompt(self, name: str) -> bool:
        """
        删除指定提示词。

        参数：
            name: 提示词名称

        返回：
            True 表示删除成功，False 表示文件不存在
        """
        prompt_dir = self._get_dir()
        file_path = prompt_dir / f"{name}.txt"
        if not file_path.exists():
            self._logger.warning(f"Prompt not found for deletion: {name}")
            return False
        file_path.unlink()
        self._logger.info(f"Deleted prompt: {name}")
        return True

    def set_active(self, name: str) -> None:
        """
        设置当前激活的提示词名称。

        参数：
            name: 提示词名称，空字符串表示使用默认提示词
        """
        prompt_dir = self._get_dir()
        active_file = prompt_dir / self._ACTIVE_FILE
        active_file.write_text(name, encoding="utf-8")
        self._logger.info(f"Set active prompt: {name or 'default'}")

    def get_active_prompt_content(self) -> str | None:
        """
        获取当前激活的提示词内容。

        返回：
            提示词文本，未设置激活时返回 None
        """
        active_name = self._read_active()
        if not active_name:
            return None
        return self.load_prompt(active_name)

    def _read_active(self) -> str | None:
        """读取 _active.txt 中的激活提示词名称。"""
        prompt_dir = self._get_dir()
        active_file = prompt_dir / self._ACTIVE_FILE
        if not active_file.exists():
            return None
        return active_file.read_text(encoding="utf-8").strip()


# 全局单例
_instance: PromptManager | None = None


def get_prompt_manager() -> PromptManager:
    global _instance
    if _instance is None:
        _instance = PromptManager()
    return _instance
