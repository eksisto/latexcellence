"""提示词管理器的名称校验（防止路径穿越）。"""

from pathlib import Path

import pytest

from backend.core.config import get_config
from backend.modules.expression_opt.prompt_manager import (
    PromptManager,
    get_prompt_manager,
)
from backend.modules.writing_chat.prompt_manager import (
    WritingChatPromptManager,
    get_prompt_manager as get_writing_prompt_manager,
)


@pytest.fixture
def expression_prompt_dir(tmp_path, monkeypatch):
    prompt_dir = tmp_path / "expression_prompts"
    prompt_dir.mkdir()
    monkeypatch.setattr(
        get_config().features.expression_opt,
        "custom_prompts_dir",
        str(prompt_dir),
    )
    return prompt_dir


@pytest.fixture
def writing_prompt_dir(tmp_path, monkeypatch):
    prompt_dir = tmp_path / "writing_prompts"
    prompt_dir.mkdir()
    monkeypatch.setattr(
        get_config().features.writing_chat,
        "custom_prompts_dir",
        str(prompt_dir),
    )
    return prompt_dir


TRAVERSAL_NAMES = [
    "../escape",
    "..\\escape",
    "a/b",
    "a\\b",
    "..",
    "name.txt",
    "C:/absolute",
    "C:\\absolute",
]


class TestExpressionPromptManager:
    def test_valid_names_work(self, expression_prompt_dir):
        pm = PromptManager()
        pm.save_prompt("academic-formal", "内容")
        pm.save_prompt("学术研究", "中文内容")
        assert pm.load_prompt("academic-formal") == "内容"
        assert pm.load_prompt("学术研究") == "中文内容"
        assert pm.delete_prompt("academic-formal") is True

    @pytest.mark.parametrize("name", TRAVERSAL_NAMES)
    def test_traversal_names_rejected(self, expression_prompt_dir, name):
        pm = PromptManager()
        with pytest.raises(ValueError):
            pm.save_prompt(name, "x")
        with pytest.raises(ValueError):
            pm.load_prompt(name)
        with pytest.raises(ValueError):
            pm.delete_prompt(name)
        with pytest.raises(ValueError):
            pm.set_active(name)
        # 不应在提示词目录之外创建任何文件
        assert list(expression_prompt_dir.parent.iterdir()) == [expression_prompt_dir]

    def test_set_active_allows_empty(self, expression_prompt_dir):
        PromptManager().set_active("")
        assert (expression_prompt_dir / "_active.txt").read_text(encoding="utf-8") == ""


class TestWritingChatPromptManager:
    def test_valid_names_work(self, writing_prompt_dir):
        pm = WritingChatPromptManager()
        pm.save_prompt("assistant-a", "内容")
        pm.save_prompt("学术研究", "中文内容")
        assert pm.load_prompt("assistant-a") == "内容"
        assert pm.load_prompt("学术研究") == "中文内容"
        assert pm.delete_prompt("assistant-a") is True

    @pytest.mark.parametrize("name", TRAVERSAL_NAMES)
    def test_traversal_names_rejected(self, writing_prompt_dir, name):
        pm = WritingChatPromptManager()
        with pytest.raises(ValueError):
            pm.save_prompt(name, "x")
        with pytest.raises(ValueError):
            pm.load_prompt(name)
        with pytest.raises(ValueError):
            pm.delete_prompt(name)
        with pytest.raises(ValueError):
            pm.set_active(name)
        assert list(writing_prompt_dir.parent.iterdir()) == [writing_prompt_dir]

    def test_set_active_allows_empty(self, writing_prompt_dir):
        WritingChatPromptManager().set_active("")
        assert (writing_prompt_dir / "_active.txt").read_text(encoding="utf-8") == ""

    def test_managers_are_singletons(self):
        assert get_prompt_manager() is get_prompt_manager()
        assert get_writing_prompt_manager() is get_writing_prompt_manager()
