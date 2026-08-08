"""paragraph_translate_eval 报告生成逻辑的单元测试。

纯 HTML 字符串生成，不依赖 LLM 调用或文件系统工作区：直接构造合成结果列表，
验证状态汇总、期望短语命中/漏检标记、未覆盖修改提示、按难度分层分组与
卡片展示逻辑。
"""

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
for _p in (str(PROJECT_ROOT), str(TOOLS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from paragraph_translate_eval.run_eval import _card_html, render_report


def _result(
    id_: str = "trans_01",
    *,
    tier: int = 1,
    tier_name: str = "简单直译",
    status: str = "translated",
    has_translation: bool = True,
    source_language: str = "English",
    target_language: str = "Chinese",
    expected_phrases: list[dict] | None = None,
    expected_translation: str = "图神经网络已经成为建模关系型数据的流行工具。",
    latex: str = "Graph neural networks have become a popular tool.",
    translated_latex: str = "图神经网络已经成为建模关系型数据的流行工具。",
    changes: list[dict] | None = None,
    matched: list[int] | None = None,
    missed: list[int] | None = None,
    unmatched_changes: list[int] | None = None,
    error: str | None = None,
) -> dict:
    expected_phrases = (
        expected_phrases
        if expected_phrases is not None
        else [{"original": "Graph neural networks", "translated": "图神经网络"}]
    )
    changes = (
        changes
        if changes is not None
        else [
            {
                "type": "translation",
                "original": "Graph neural networks",
                "translated": "图神经网络",
                "explanation": "",
            }
        ]
    )
    return {
        "id": id_,
        "tier": tier,
        "tier_name": tier_name,
        "title": "",
        "source_language": source_language,
        "target_language": target_language,
        "has_translation": has_translation,
        "expected_phrases": expected_phrases,
        "expected_translation": expected_translation,
        "expected_latex": latex,
        "translated_latex": translated_latex,
        "changes": changes,
        "status": status,
        "error": error,
        "matched": matched if matched is not None else [0],
        "missed": missed if missed is not None else [],
        "unmatched_changes": (
            unmatched_changes if unmatched_changes is not None else []
        ),
    }


class TestRenderReport:
    @staticmethod
    def _render(results: list[dict]) -> str:
        # 不用 tmp_path：pytest.ini 把 basetemp 固定在项目根 .pytest_tmp，
        # 残留目录被锁时 tmp_path 会直接报错；这里走系统临时目录。
        with tempfile.TemporaryDirectory(
            prefix="paragraph_translate_eval_report_"
        ) as tmp:
            report_path = Path(tmp) / "report.html"
            render_report(report_path, results)
            return report_path.read_text(encoding="utf-8")

    def test_summarizes_statuses(self):
        results = [
            _result(id_="trans_01", status="translated"),
            _result(
                id_="trans_02",
                status="unchanged",
                translated_latex="Graph neural networks have become a popular tool.",
                changes=[],
                matched=[],
                missed=[0],
            ),
            _result(id_="trans_03", status="error", error="boom"),
        ]
        html_text = self._render(results)
        assert "共 3 条：已翻译 1，译文与原文相同 1，失败 1" in html_text

    def test_summarizes_phrase_hits_and_unmatched_changes(self):
        results = [
            _result(
                id_="trans_01",
                expected_phrases=[
                    {"original": "Graph neural networks", "translated": "图神经网络"},
                    {"original": "node classification", "translated": "节点分类"},
                ],
                matched=[0],
                missed=[1],
            ),
            _result(
                id_="trans_02",
                status="unchanged",
                translated_latex="Graph neural networks have become a popular tool.",
                expected_phrases=[
                    {"original": "Graph neural networks", "translated": "图神经网络"}
                ],
                changes=[],
                matched=[],
                missed=[0],
            ),
            _result(
                id_="trans_03",
                expected_phrases=[],
                changes=[
                    {"type": "translation", "original": "tool", "translated": "工具"}
                ],
                matched=[],
                missed=[],
                unmatched_changes=[0],
            ),
        ]
        html_text = self._render(results)
        assert "期望短语共 3 个：命中 1 个，漏检 2 个" in html_text
        assert "未被期望短语覆盖的修改条目 1 个（参考信息，非误报）" in html_text

    def test_summarizes_same_language_redundant_changes(self):
        results = [
            _result(id_="trans_01", status="translated"),
            _result(
                id_="trans_20",
                status="translated",
                has_translation=False,
                source_language="English",
                target_language="English",
                expected_phrases=[],
                expected_translation=(
                    "In this section, we summarize our contributions."
                ),
                translated_latex="在本节中，我们总结了贡献。",
                matched=[],
                missed=[],
                unmatched_changes=[0],
            ),
        ]
        html_text = self._render(results)
        assert "期望含翻译语料 1 条：产生译文 1 条，未翻译 0 条" in html_text
        assert "已同语种语料 1 条：多余改动 1 条" in html_text

    def test_groups_by_tier(self):
        results = [
            _result(id_="trans_01", tier=1, tier_name="简单直译"),
            _result(id_="trans_17", tier=5, tier_name="压力测试"),
        ]
        html_text = self._render(results)
        assert "简单直译（1 条）" in html_text
        assert "压力测试（1 条）" in html_text


class TestCardHtml:
    def test_shows_matched_and_missed_marks(self):
        item = _result(
            expected_phrases=[
                {"original": "Graph neural networks", "translated": "图神经网络"},
                {"original": "node classification", "translated": "节点分类"},
            ],
            matched=[0],
            missed=[1],
        )
        html_text = _card_html(item)
        assert "期望短语 2 命中 1 漏检 1" in html_text
        assert "✓" in html_text
        assert "✗" in html_text

    def test_flags_unmatched_changes(self):
        item = _result(
            changes=[
                {
                    "type": "translation",
                    "original": "Graph neural networks",
                    "translated": "图神经网络",
                },
                {"type": "translation", "original": "tool", "translated": "工具"},
            ],
            unmatched_changes=[1],
        )
        html_text = _card_html(item)
        assert "[未覆盖]" in html_text
        assert "检测到的翻译修改" in html_text

    def test_unchanged_hint_when_translation_expected(self):
        item = _result(
            status="unchanged",
            translated_latex="Graph neural networks have become a popular tool.",
            changes=[],
            matched=[],
            missed=[0],
        )
        html_text = _card_html(item)
        assert "译文与原文相同：可能未翻译" in html_text

    def test_redundant_hint_when_already_target_language(self):
        item = _result(
            status="translated",
            has_translation=False,
            source_language="English",
            target_language="English",
            expected_phrases=[],
            expected_translation="In this section, we summarize our contributions.",
            translated_latex="在本节中，我们总结了贡献。",
            matched=[],
            missed=[],
            unmatched_changes=[0],
        )
        html_text = _card_html(item)
        assert "原文已在目标语言，但模型仍修改了文本（多余改动）" in html_text
        assert "已同语种" in html_text

    def test_error_card_shows_error(self):
        item = _result(status="error", error="workspace failed", changes=[])
        html_text = _card_html(item)
        assert "失败" in html_text
        assert "workspace failed" in html_text

    def test_shows_reference_and_model_translation(self):
        item = _result()
        html_text = _card_html(item)
        assert "参考译文" in html_text
        assert "模型译文" in html_text
        assert "图神经网络已经成为建模关系型数据的流行工具。" in html_text
        assert "原文" in html_text

    def test_no_changes_table_when_empty(self):
        item = _result(
            status="unchanged",
            translated_latex="Graph neural networks have become a popular tool.",
            changes=[],
            matched=[],
            missed=[0],
        )
        html_text = _card_html(item)
        assert "检测到的翻译修改" not in html_text

    def test_shows_language_pair(self):
        item = _result()
        assert "English → Chinese" in _card_html(item)
