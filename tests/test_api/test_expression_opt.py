"""
expression_opt 模块集成测试。

覆盖内容：
    - Pydantic 模型校验（ExpressionOptResult / ExpressionChangeItem）
    - preview_paragraphs LaTeX 剥离
    - _optimize_one_paragraph LLM mock
    - marker 保护验证
    - Pydantic 校验失败 → 回退
    - apply_changes marker 还原写回
    - prompt_manager CRUD
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from backend.modules.expression_opt.service import (
    ExpressionOptService,
    ExpressionOptResult,
    ExpressionChangeItem,
)
from backend.modules.expression_opt.prompt_manager import PromptManager


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def temp_project():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        main_content = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"\section{Introduction}" + "\n"
            r"This method is not very good for the purpose." + "\n"
            r"It has many weaknesses in practical application." + "\n"
            r"\end{document}"
        )
        (root / "main.tex").write_text(main_content, encoding="utf-8")

        yield root


@pytest.fixture
def service():
    return ExpressionOptService()


# ── Pydantic model tests ───────────────────────────────────────────────────


class TestExpressionOptResultModel:
    """ExpressionOptResult / ExpressionChangeItem 模型校验测试。"""

    def test_valid_minimal(self):
        data = {
            "paragraph_index": 0,
            "original": "hello world",
            "optimized": "hello world",
            "changes": [],
        }
        result = ExpressionOptResult.model_validate(data)
        assert result.paragraph_index == 0
        assert result.optimized == "hello world"
        assert result.changes == []

    def test_valid_with_changes(self):
        data = {
            "paragraph_index": 1,
            "original": "This method is not very good.",
            "optimized": "This approach is suboptimal.",
            "changes": [
                {
                    "type": "rewrite",
                    "original": "is not very good",
                    "optimized": "is suboptimal",
                    "reason": "more concise",
                },
            ],
        }
        result = ExpressionOptResult.model_validate(data)
        assert len(result.changes) == 1
        assert result.changes[0].type == "rewrite"
        assert result.changes[0].optimized == "is suboptimal"

    def test_change_item_defaults(self):
        data = {
            "type": "rewrite",
            "original": "bad phrasing",
            "optimized": "better phrasing",
        }
        change = ExpressionChangeItem.model_validate(data)
        assert change.reason == ""

    def test_missing_required_field(self):
        data = {"paragraph_index": 0, "original": "text"}
        with pytest.raises(ValidationError):
            ExpressionOptResult.model_validate(data)

    def test_wrong_type(self):
        data = {
            "paragraph_index": "wrong",
            "original": "text",
            "optimized": "text",
            "changes": [],
        }
        with pytest.raises(ValidationError):
            ExpressionOptResult.model_validate(data)


# ── service tests with mocked LLM ──────────────────────────────────────────


class TestPreviewParagraphs:
    """预览段落测试。"""

    def test_strips_latex_commands(self, temp_project, service):
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))

        result = service.preview_paragraphs("main.tex", workspace_id=ws_id)
        assert result["total"] >= 1
        assert result["file_path"] == "main.tex"

        for p in result["paragraphs"]:
            assert "plain_text" in p
            assert r"\documentclass" not in p["plain_text"]
            assert r"\begin" not in p["plain_text"]


class TestOptimizeOneParagraph:
    """LLM 调用 + 校验逻辑测试。"""

    @pytest.fixture
    def mock_llm(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_successful_optimization(self, mock_llm, service):
        plain = r"__CMD_0000__ This method is not very good."
        original_tex = r"\textbf{This method is not very good.}"

        mock_llm.chat = AsyncMock(return_value={
            "paragraph_index": 0,
            "original": plain,
            "optimized": r"__CMD_0000__ This approach is suboptimal.",
            "changes": [
                {
                    "type": "rewrite",
                    "original": "is not very good",
                    "optimized": "is suboptimal",
                    "reason": "more concise",
                }
            ],
        })

        result = await service._check_one_paragraph(
            mock_llm, 0, 5, plain, original_tex, "main.tex"
        )

        assert result["paragraph_index"] == 5
        assert result["optimized"] == r"__CMD_0000__ This approach is suboptimal."
        assert len(result["changes"]) == 1
        assert result["changes"][0]["type"] == "rewrite"
        assert result["changes"][0]["reason"] == "more concise"

    @pytest.mark.asyncio
    async def test_no_changes_needed(self, mock_llm, service):
        plain = r"Text with __MATH_0000__ formula."
        original_tex = r"Text with $x^2$ formula."

        mock_llm.chat = AsyncMock(return_value={
            "paragraph_index": 0,
            "original": plain,
            "optimized": plain,
            "changes": [],
        })

        result = await service._check_one_paragraph(
            mock_llm, 0, 0, plain, original_tex, "main.tex"
        )

        assert result["paragraph_index"] == 0
        assert result["optimized"] == plain
        assert result["changes"] == []

    @pytest.mark.asyncio
    async def test_marker_mismatch_falls_back(self, mock_llm, service):
        plain = r"Text with __CMD_0000__ marker."
        original_tex = r"Text with \textbf{marker.}"

        mock_llm.chat = AsyncMock(return_value={
            "paragraph_index": 0,
            "original": plain,
            "optimized": "Text with marker.",
            "changes": [],
        })

        result = await service._check_one_paragraph(
            mock_llm, 0, 0, plain, original_tex, "main.tex"
        )

        assert result["optimized"] == plain
        assert result.get("marker_error") is True

    @pytest.mark.asyncio
    async def test_pydantic_validation_retry_exhausted(self, mock_llm, service):
        plain = "Some text."
        original_tex = "Some text."

        mock_llm.chat = AsyncMock(return_value={"bad": "json"})

        result = await service._check_one_paragraph(
            mock_llm, 0, 3, plain, original_tex, "main.tex"
        )

        assert result["optimized"] == plain
        assert result["changes"] == []
        assert mock_llm.chat.call_count == 3

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back(self, mock_llm, service):
        plain = "Some text."
        original_tex = "Some text."

        mock_llm.chat = AsyncMock(side_effect=Exception("Network error"))

        result = await service._check_one_paragraph(
            mock_llm, 0, 7, plain, original_tex, "main.tex"
        )

        assert result["paragraph_index"] == 7
        assert result["optimized"] == plain
        assert result["changes"] == []


class TestApplyChanges:
    """修改写回测试。"""

    def test_apply_restores_markers(self, temp_project, service):
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))

        preview = service.preview_paragraphs("main.tex", workspace_id=ws_id)
        operation_id = preview["operation_id"]

        paragraphs = pm.get_paragraphs("main.tex")
        for para in paragraphs:
            if "not very good" in para.text:
                new_text = para.text.replace("not very good", "suboptimal")
                result = service.apply_changes(
                    "main.tex",
                    [{"paragraph_index": para.index, "new_text": new_text}],
                    workspace_id=ws_id,
                    operation_id=operation_id,
                )
                assert result["applied"] == 1
                break

        content = (temp_project / "main.tex").read_text(encoding="utf-8")
        assert "not very good" not in content
        assert "suboptimal" in content


# ── prompt_manager tests ───────────────────────────────────────────────────


class TestPromptManager:
    """提示词管理器 CRUD 测试。"""

    @pytest.fixture
    def pm(self, tmp_path, monkeypatch):
        prompts_dir = tmp_path / "prompts" / "expression"
        prompts_dir.mkdir(parents=True)

        import backend.core.config as config_mod
        # Patch get_config to return a config with our temp dir
        monkeypatch.setattr(
            "backend.modules.expression_opt.prompt_manager.get_config",
            lambda: _MockConfig(str(prompts_dir)),
        )

        pm = PromptManager()
        return pm

    def test_list_empty(self, pm):
        prompts = pm.list_prompts()
        assert prompts == []

    def test_save_and_list(self, pm):
        pm.save_prompt("test-style", "Use formal language.")
        prompts = pm.list_prompts()
        assert len(prompts) == 1
        assert prompts[0]["name"] == "test-style"
        assert prompts[0]["active"] is False

    def test_load_prompt(self, pm):
        pm.save_prompt("test-style", "Use formal language.")
        content = pm.load_prompt("test-style")
        assert content == "Use formal language."

    def test_load_nonexistent(self, pm):
        content = pm.load_prompt("nonexistent")
        assert content is None

    def test_delete_prompt(self, pm):
        pm.save_prompt("test-style", "Use formal language.")
        assert pm.delete_prompt("test-style") is True
        assert pm.list_prompts() == []

    def test_delete_nonexistent(self, pm):
        assert pm.delete_prompt("nonexistent") is False

    def test_set_and_get_active(self, pm):
        pm.save_prompt("test-style", "Use formal language.")
        pm.set_active("test-style")
        prompts = pm.list_prompts()
        active = [p for p in prompts if p["active"]]
        assert len(active) == 1
        assert active[0]["name"] == "test-style"

        content = pm.get_active_prompt_content()
        assert content == "Use formal language."

    def test_no_active_by_default(self, pm):
        pm.save_prompt("test-style", "Use formal language.")
        content = pm.get_active_prompt_content()
        assert content is None

    def test_clear_active(self, pm):
        pm.save_prompt("test-style", "Use formal language.")
        pm.set_active("test-style")
        pm.set_active("")
        content = pm.get_active_prompt_content()
        assert content is None

    def test_save_overwrites(self, pm):
        pm.save_prompt("test-style", "Version 1")
        pm.save_prompt("test-style", "Version 2")
        content = pm.load_prompt("test-style")
        assert content == "Version 2"


class _MockConfig:
    """Mock config for prompt manager tests."""
    def __init__(self, prompts_dir):
        self.features = _MockFeatures(prompts_dir)


class _MockFeatures:
    def __init__(self, prompts_dir):
        self.expression_opt = _MockExpressionOptConfig(prompts_dir)


class _MockExpressionOptConfig:
    def __init__(self, prompts_dir):
        self.custom_prompts_dir = prompts_dir
        self.default_prompt = "Default prompt."
        self.enabled = True
        self.batch_size = 3
