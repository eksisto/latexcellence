"""
typo_check 模块集成测试。

覆盖内容：
    - Pydantic 模型校验（TypoCheckResult / TypoChangeItem）
    - preview_paragraphs LaTeX 剥离
    - _check_one_paragraph 的 LLM 响应处理（含 mock）
    - marker 保护验证（缺失 marker → 回退）
    - Pydantic 校验失败 → retry_with_validation 回退
    - apply_changes marker 还原写回
    - 空段落边界处理
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from backend.modules.typo_check.service import (
    TypoCheckService,
    TypoCheckResult,
    TypoChangeItem,
)
from backend.utils.latex_parser import MARKER_PATTERN

# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def temp_project():
    """创建临时 LaTeX 项目目录结构。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        main_content = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"\section{Introduction}" + "\n"
            r"This is a test paragrafh with a typo." + "\n"
            r"It also has speling errors." + "\n"
            r"\end{document}"
        )
        (root / "main.tex").write_text(main_content, encoding="utf-8")

        yield root


@pytest.fixture
def service():
    return TypoCheckService()


# ── Pydantic model tests ───────────────────────────────────────────────────


class TestTypoCheckResultModel:
    """TypoCheckResult / TypoChangeItem 模型校验测试。"""

    def test_valid_minimal(self):
        data = {
            "paragraph_index": 0,
            "original": "hello world",
            "corrected": "hello world",
            "changes": [],
        }
        result = TypoCheckResult.model_validate(data)
        assert result.paragraph_index == 0
        assert result.changes == []

    def test_valid_with_changes(self):
        data = {
            "paragraph_index": 1,
            "original": "Ths is rong",
            "corrected": r"This is wrong",
            "changes": [
                {
                    "type": "typo",
                    "original": "Ths",
                    "corrected": "This",
                    "explanation": "missing letter",
                },
                {
                    "type": "typo",
                    "original": "rong",
                    "corrected": "wrong",
                    "position": 8,
                    "explanation": "spelling error",
                },
            ],
        }
        result = TypoCheckResult.model_validate(data)
        assert len(result.changes) == 2
        assert result.changes[0].type == "typo"
        assert result.changes[0].original == "Ths"
        assert result.changes[1].position == 8

    def test_change_item_defaults(self):
        data = {
            "type": "typo",
            "original": "a",
            "corrected": "b",
        }
        change = TypoChangeItem.model_validate(data)
        assert change.type == "typo"
        assert change.explanation == ""
        assert change.position is None

    def test_missing_required_field(self):
        data = {
            "paragraph_index": 0,
            "corrected": "text",
        }
        with pytest.raises(ValidationError):
            TypoCheckResult.model_validate(data)

    def test_wrong_type(self):
        data = {
            "paragraph_index": "not_a_number",
            "original": "text",
            "corrected": "text",
            "changes": [],
        }
        with pytest.raises(ValidationError):
            TypoCheckResult.model_validate(data)


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
            assert "text" in p
            assert "line_start" in p
            assert "line_end" in p
            # 纯文本不应包含 LaTeX 命令
            assert r"\documentclass" not in p["plain_text"]
            assert r"\begin" not in p["plain_text"]
            assert r"\section" not in p["plain_text"]

    def test_preserves_plain_text_content(self, temp_project, service):
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))

        result = service.preview_paragraphs("main.tex", workspace_id=ws_id)
        all_plain = " ".join(p["plain_text"] for p in result["paragraphs"])
        assert "test paragrafh" in all_plain.lower()
        assert "speling" in all_plain.lower()


class TestCheckOneParagraph:
    """LLM 调用 + 校验逻辑测试。"""

    @pytest.fixture
    def mock_llm(self):
        """返回一个 mock LLM 客户端。"""
        return MagicMock()

    def _make_llm_response(self, idx, original, corrected, changes):
        """构造符合 TypoCheckResult 的 dict 响应。"""
        return {
            "paragraph_index": idx,
            "original": original,
            "corrected": corrected,
            "changes": changes,
        }

    @pytest.mark.asyncio
    async def test_successful_typo_fix(self, mock_llm, service):
        """LLM 返回正确的修改建议。"""
        plain = r"This is __CMD_0000__ a test paragrafh."
        original_tex = r"This is \textbf{a test paragrafh.}"

        mock_llm.chat = AsyncMock(
            return_value={
                "paragraph_index": 0,
                "original": plain,
                "corrected": r"This is __CMD_0000__ a test paragraph.",
                "changes": [
                    {
                        "type": "typo",
                        "original": "paragrafh",
                        "corrected": "paragraph",
                        "explanation": "spelling error",
                    }
                ],
            }
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 5, plain, original_tex, "main.tex"
        )

        assert result["paragraph_index"] == 5
        assert result["corrected"] == r"This is __CMD_0000__ a test paragraph."
        assert len(result["changes"]) == 1
        assert result["changes"][0]["original"] == "paragrafh"
        assert result["changes"][0]["corrected"] == "paragraph"
        assert "marker_error" not in result

    @pytest.mark.asyncio
    async def test_no_errors_found(self, mock_llm, service):
        """LLM 认为没有错误，返回原始文本。"""
        plain = r"Correct text with __MATH_0000__ formula."
        original_tex = r"Correct text with $x^2$ formula."

        mock_llm.chat = AsyncMock(
            return_value={
                "paragraph_index": 0,
                "original": plain,
                "corrected": plain,
                "changes": [],
            }
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 0, plain, original_tex, "main.tex"
        )

        assert result["paragraph_index"] == 0
        assert result["corrected"] == plain
        assert result["changes"] == []

    @pytest.mark.asyncio
    async def test_marker_mismatch_falls_back(self, mock_llm, service):
        """LLM 修改了 marker → 回退到原始文本。"""
        plain = r"Text with __CMD_0000__ marker."
        original_tex = r"Text with \textbf{marker.}"

        # LLM 返回的 corrected 缺少 marker
        mock_llm.chat = AsyncMock(
            return_value={
                "paragraph_index": 0,
                "original": plain,
                "corrected": "Text with marker.",  # 缺少 __CMD_0000__
                "changes": [],
            }
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 0, plain, original_tex, "main.tex"
        )

        assert result["corrected"] == plain  # 回退到原始
        assert result.get("marker_error") is True
        assert result["changes"] == []

    @pytest.mark.asyncio
    async def test_extra_marker_falls_back(self, mock_llm, service):
        """LLM 添加了新的 marker（幻觉）→ 回退。"""
        plain = r"Plain text only."
        original_tex = "Plain text only."

        # LLM 凭空添加 marker
        mock_llm.chat = AsyncMock(
            return_value={
                "paragraph_index": 0,
                "original": plain,
                "corrected": r"Plain __CMD_0099__ text only.",
                "changes": [],
            }
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 0, plain, original_tex, "main.tex"
        )

        assert result["corrected"] == plain
        assert result.get("marker_error") is True

    @pytest.mark.asyncio
    async def test_duplicated_marker_falls_back(self, mock_llm, service):
        """LLM 重复同一个 marker（计数不一致）→ 回退。"""
        plain = r"Text __CMD_0000__ and __CMD_0001__ end."
        original_tex = r"Text \textbf{and} \textit{end.}"

        # marker __CMD_0000__ 在输出中出现两次
        mock_llm.chat = AsyncMock(
            return_value={
                "paragraph_index": 0,
                "original": plain,
                "corrected": r"Text __CMD_0000__ and __CMD_0000__ end.",
                "changes": [],
            }
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 0, plain, original_tex, "main.tex"
        )

        assert result["corrected"] == plain
        assert result.get("marker_error") is True

    @pytest.mark.asyncio
    async def test_reordered_markers_falls_back(self, mock_llm, service):
        """LLM 交换两个 marker 的顺序（重排）→ 回退。"""
        plain = r"__CMD_0000__ before __CMD_0001__ after."
        original_tex = r"\textbf{before} \textit{after.}"

        # 输出中两个 marker 顺序互换
        mock_llm.chat = AsyncMock(
            return_value={
                "paragraph_index": 0,
                "original": plain,
                "corrected": r"__CMD_0001__ before __CMD_0000__ after.",
                "changes": [],
            }
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 0, plain, original_tex, "main.tex"
        )

        assert result["corrected"] == plain
        assert result.get("marker_error") is True

    @pytest.mark.asyncio
    async def test_pure_text_move_preserves_markers_passes(self, mock_llm, service):
        """纯文本被移动但 marker 序列不变（合法编辑）→ 正常通过。"""
        plain = r"__CMD_0000__ word1 __CMD_0001__ word2."
        original_tex = r"\textbf{word1} \textit{word2.}"

        # 纯文本 word1/word2 互换，marker 序列保持原顺序
        mock_llm.chat = AsyncMock(
            return_value={
                "paragraph_index": 0,
                "original": plain,
                "corrected": r"__CMD_0000__ word2 __CMD_0001__ word1.",
                "changes": [],
            }
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 0, plain, original_tex, "main.tex"
        )

        assert result.get("marker_error") is not True
        assert result["corrected"] == r"__CMD_0000__ word2 __CMD_0001__ word1."

    @pytest.mark.asyncio
    async def test_pydantic_validation_retry_exhausted(self, mock_llm, service):
        """LLM 连续返回不合规 JSON → 最终回退到原始文本。"""
        plain = "Some text."
        original_tex = "Some text."

        # LLM 始终返回不合法 JSON（缺少 required fields）
        mock_llm.chat = AsyncMock(
            return_value={
                "wrong_field": True,
            }
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 3, plain, original_tex, "main.tex"
        )

        # 重试耗尽后回退
        assert result["corrected"] == plain
        assert result["changes"] == []

        # retry_with_validation(max_retries=3) = 3 次总调用（含首次）
        assert mock_llm.chat.call_count == 3

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back(self, mock_llm, service):
        """LLM 调用抛异常 → 回退到原始文本。"""
        plain = "Some text."
        original_tex = "Some text."

        mock_llm.chat = AsyncMock(side_effect=Exception("Network error"))

        result = await service._check_one_paragraph(
            mock_llm, 0, 7, plain, original_tex, "main.tex"
        )

        assert result["paragraph_index"] == 7
        assert result["corrected"] == plain
        assert result["changes"] == []

    @pytest.mark.asyncio
    async def test_marker_stability_after_llm_roundtrip(self, mock_llm, service):
        """验证 LLM 不改动任何 marker 时，marker 完整保留。"""
        plain = r"__CMD_0000__ hello __MATH_0001__ world __CMD_0002__"
        original_tex = r"\textbf{hello} $x^2$ world \textit{}"

        corrected = r"__CMD_0000__ Hello __MATH_0001__ world __CMD_0002__"

        mock_llm.chat = AsyncMock(
            return_value={
                "paragraph_index": 0,
                "original": plain,
                "corrected": corrected,
                "changes": [
                    {
                        "type": "typo",
                        "original": "hello",
                        "corrected": "Hello",
                        "explanation": "capitalization",
                    }
                ],
            }
        )

        result = await service._check_one_paragraph(
            mock_llm, 0, 0, plain, original_tex, "main.tex"
        )

        assert result["corrected"] == corrected
        assert len(result["changes"]) == 1
        assert "marker_error" not in result


class TestApplyChanges:
    """修改写回测试。"""

    def test_apply_restores_markers(self, temp_project, service):
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))

        # 先预览以填充 marker_maps 并获取 operation_id
        preview = service.preview_paragraphs("main.tex", workspace_id=ws_id)
        operation_id = preview["operation_id"]

        # 构造一个 changes：把 "paragrafh" 改成 "paragraph"
        # 但是由于 plain_text 包含 marker，这里直接使用原始文本作简化测试
        paragraphs = pm.get_paragraphs("main.tex")
        for para in paragraphs:
            if "paragrafh" in para.text:
                new_text = para.text.replace("paragrafh", "paragraph")
                result = service.apply_changes(
                    "main.tex",
                    [{"paragraph_index": para.index, "new_text": new_text}],
                    workspace_id=ws_id,
                    operation_id=operation_id,
                )
                assert result["applied"] == 1
                break

        # 验证写回
        content = (temp_project / "main.tex").read_text(encoding="utf-8")
        assert "paragrafh" not in content
        assert "paragraph" in content

    def test_apply_multiple_changes(self, temp_project, service):
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))

        preview = service.preview_paragraphs("main.tex", workspace_id=ws_id)
        operation_id = preview["operation_id"]

        paragraphs = pm.get_paragraphs("main.tex")
        # 两个错字在同一段内：需要合并为一个 change
        changes = []
        for para in paragraphs:
            if "paragrafh" in para.text and "speling" in para.text:
                new_text = para.text.replace("paragrafh", "paragraph")
                new_text = new_text.replace("speling", "spelling")
                changes.append(
                    {
                        "paragraph_index": para.index,
                        "new_text": new_text,
                    }
                )
                break

        result = service.apply_changes(
            "main.tex", changes, workspace_id=ws_id, operation_id=operation_id
        )
        assert result["applied"] == 1
        assert result["total"] == 1

        content = (temp_project / "main.tex").read_text(encoding="utf-8")
        assert "paragrafh" not in content
        assert "speling" not in content

    def test_apply_skips_when_marker_map_missing(self, temp_project, service):
        """marker_map 丢失时应跳过写入并返回 errors，而非静默写入字面 marker。"""
        from backend.core.workspace_manager import get_project_manager_for_workspace

        ws_id = "test-ws"
        pm = get_project_manager_for_workspace(ws_id)
        pm.open(str(temp_project))

        # 不做 preview，直接用一个不存在的 operation_id 调 apply
        fake_operation_id = "nonexistent-op-id"
        result = service.apply_changes(
            "main.tex",
            [{"paragraph_index": 0, "new_text": r"__CMD_0000__ literal marker"}],
            workspace_id=ws_id,
            operation_id=fake_operation_id,
        )

        # 应用数为 0，errors 中有记录
        assert result["applied"] == 0
        assert result["total"] == 1
        assert len(result["errors"]) == 1
        assert result["errors"][0]["reason"] == "Marker map missing"
        assert result["errors"][0]["paragraph_index"] == 0

        # 文件内容未被修改：字面 marker 不应出现在文件中
        content = (temp_project / "main.tex").read_text(encoding="utf-8")
        assert "__CMD_0000__" not in content


class TestEmptyParagraphHandling:
    """空段落边界处理。"""

    @pytest.mark.asyncio
    async def test_empty_result(self, service):
        result = service._make_fallback(0, "", "original tex", "main.tex")
        assert result["paragraph_index"] == 0
        assert result["original"] == ""
        assert result["corrected"] == ""
        assert result["changes"] == []
        assert result["text"] == "original tex"


# ── marker pattern tests ───────────────────────────────────────────────────


class TestMarkerPattern:
    """marker 正则表达式测试。"""

    def test_match_cmd_marker(self):
        markers = MARKER_PATTERN.findall("text __CMD_0000__ more")
        assert markers == ["__CMD_0000__"]

    def test_match_math_marker(self):
        markers = MARKER_PATTERN.findall("text __MATH_0001__ more")
        assert markers == ["__MATH_0001__"]

    def test_match_env_marker(self):
        markers = MARKER_PATTERN.findall("text __ENV_0015__ more")
        assert markers == ["__ENV_0015__"]

    def test_match_verb_marker(self):
        markers = MARKER_PATTERN.findall("text __VERB_0002__ more")
        assert markers == ["__VERB_0002__"]

    def test_match_multiple(self):
        text = "__CMD_0000__ hello __MATH_0001__ world __CMD_0002__"
        markers = MARKER_PATTERN.findall(text)
        assert markers == ["__CMD_0000__", "__MATH_0001__", "__CMD_0002__"]

    def test_ignore_partial_marker(self):
        markers = MARKER_PATTERN.findall("__CMD_001__ and __CMD_00001__")
        # 3 位数不匹配，5 位数匹配
        assert markers == ["__CMD_00001__"]

    def test_match_five_digit_marker(self):
        """超过 9999 的 marker（5 位）仍能匹配。"""
        markers = MARKER_PATTERN.findall("text __CMD_10000__ end")
        assert markers == ["__CMD_10000__"]

    def test_marker_extraction_empty_text(self):
        markers = MARKER_PATTERN.findall("")
        assert markers == []
