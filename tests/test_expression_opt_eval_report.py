"""expression_opt_eval 报告生成逻辑的单元测试。

纯 HTML 字符串生成，不依赖 LLM 调用或文件系统工作区：直接构造合成结果列表，
验证状态汇总、期望优化命中/漏检/误报标记、按难度分层分组与卡片展示逻辑。
"""

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
for _p in (str(PROJECT_ROOT), str(TOOLS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from expression_opt_eval.run_eval import _card_html, render_report


def _result(
    id_: str = "expr_01",
    *,
    tier: int = 1,
    tier_name: str = "简单冗余",
    status: str = "issues",
    has_improvement: bool = True,
    expected_improvements: list[dict] | None = None,
    latex: str = "The model gets better results.",
    optimized_latex: str = "The model outperforms.",
    changes: list[dict] | None = None,
    matched: list[int] | None = None,
    missed: list[int] | None = None,
    false_positive_changes: list[int] | None = None,
    error: str | None = None,
) -> dict:
    expected_improvements = (
        expected_improvements
        if expected_improvements is not None
        else [{"original": "gets better results", "optimized": "outperforms"}]
    )
    changes = (
        changes
        if changes is not None
        else [
            {
                "type": "rewrite",
                "original": "gets better results",
                "optimized": "outperforms",
                "reason": "more concise",
            }
        ]
    )
    return {
        "id": id_,
        "tier": tier,
        "tier_name": tier_name,
        "title": "",
        "has_improvement": has_improvement,
        "expected_improvements": expected_improvements,
        "expected_latex": latex,
        "optimized_latex": optimized_latex,
        "changes": changes,
        "unlisted_changes": False,
        "status": status,
        "error": error,
        "matched": matched if matched is not None else [0],
        "missed": missed if missed is not None else [],
        "false_positive_changes": (
            false_positive_changes if false_positive_changes is not None else []
        ),
    }


class TestRenderReport:
    @staticmethod
    def _render(results: list[dict]) -> str:
        # 不用 tmp_path：pytest.ini 把 basetemp 固定在项目根 .pytest_tmp，
        # 残留目录被锁时 tmp_path 会直接报错；这里走系统临时目录。
        with tempfile.TemporaryDirectory(prefix="expression_opt_eval_report_") as tmp:
            report_path = Path(tmp) / "report.html"
            render_report(report_path, results)
            return report_path.read_text(encoding="utf-8")

    def test_summarizes_statuses(self):
        results = [
            _result(id_="expr_01", status="issues"),
            _result(id_="expr_02", status="clean", changes=[], optimized_latex=""),
            _result(id_="expr_03", status="error", error="boom"),
        ]
        html_text = self._render(results)
        assert "有优化建议 1" in html_text
        assert "无优化建议 1" in html_text
        assert "失败 1" in html_text

    def test_summarizes_hits_and_false_positives(self):
        results = [
            _result(id_="expr_01", has_improvement=True, status="issues"),
            _result(id_="expr_02", has_improvement=True, status="clean", changes=[]),
            _result(
                id_="expr_03",
                has_improvement=False,
                status="issues",
                expected_improvements=[],
                matched=[],
                missed=[],
                false_positive_changes=[0],
            ),
        ]
        html_text = self._render(results)
        assert "期望含优化语料 2 条：发现优化 1 条，漏检 1 条" in html_text
        assert "干净语料 1 条：误报 1 条（误报修改条目 1 个）" in html_text

    def test_groups_by_tier(self):
        results = [
            _result(id_="expr_01", tier=1, tier_name="简单冗余"),
            _result(id_="expr_06", tier=2, tier_name="名词化与弱动词"),
        ]
        html_text = self._render(results)
        assert "简单冗余（1 条）" in html_text
        assert "名词化与弱动词（1 条）" in html_text


class TestCardHtml:
    def test_shows_matched_and_missed_marks(self):
        item = _result(
            expected_improvements=[
                {"original": "a large number of", "optimized": "many"},
                {"original": "in order to", "optimized": "to"},
            ],
            matched=[0],
            missed=[1],
        )
        html_text = _card_html(item)
        assert "期望 2 命中 1 漏检 1" in html_text
        assert "✓" in html_text
        assert "✗" in html_text

    def test_highlights_false_positive_changes(self):
        item = _result(
            changes=[
                {
                    "type": "rewrite",
                    "original": "gets better results",
                    "optimized": "outperforms",
                },
                {"type": "rewrite", "original": "collected", "optimized": "gathered"},
            ],
            false_positive_changes=[1],
        )
        html_text = _card_html(item)
        assert "误报 1 条" in html_text
        assert "[误报]" in html_text

    def test_clean_expected_item_shows_miss_hint(self):
        item = _result(
            status="clean", changes=[], optimized_latex="", matched=[], missed=[0]
        )
        assert "未发现期望优化" in _card_html(item)

    def test_error_card_shows_error(self):
        item = _result(status="error", error="workspace failed", changes=[])
        html_text = _card_html(item)
        assert "失败" in html_text
        assert "workspace failed" in html_text

    def test_shows_optimized_block_when_text_changed(self):
        item = _result(
            latex="The model gets better results.",
            optimized_latex="The model outperforms.",
        )
        html_text = _card_html(item)
        assert "优化后" in html_text
        assert "The model outperforms." in html_text

    def test_clean_item_has_no_changes_table(self):
        item = _result(
            status="clean",
            has_improvement=False,
            expected_improvements=[],
            changes=[],
            optimized_latex="",
            matched=[],
            missed=[],
        )
        html_text = _card_html(item)
        assert "检测到的优化" not in html_text
        assert "干净" in html_text
