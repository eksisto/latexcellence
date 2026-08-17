"""typo_check_eval 报告生成逻辑的单元测试。

纯 HTML 字符串生成，不依赖 LLM 调用或文件系统工作区：直接构造合成结果列表，
验证状态汇总、期望错字命中/漏检/误报标记、按难度分层分组与卡片展示逻辑。
"""

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
for _p in (str(PROJECT_ROOT), str(TOOLS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from typo_check_eval.run_eval import _card_html, render_report


def _result(
    id_: str = "typo_01",
    *,
    tier: int = 1,
    tier_name: str = "简单错字",
    status: str = "issues",
    has_typo: bool = True,
    expected_issues: list[dict] | None = None,
    latex: str = "The model recieve the signal.",
    corrected_latex: str = "The model receive the signal.",
    changes: list[dict] | None = None,
    matched: list[int] | None = None,
    missed: list[int] | None = None,
    false_positive_changes: list[int] | None = None,
    error: str | None = None,
) -> dict:
    expected_issues = (
        expected_issues
        if expected_issues is not None
        else [{"original": "recieve", "corrected": "receive"}]
    )
    changes = (
        changes
        if changes is not None
        else [
            {
                "type": "typo",
                "original": "recieve",
                "corrected": "receive",
                "explanation": "spelling",
            }
        ]
    )
    return {
        "id": id_,
        "tier": tier,
        "tier_name": tier_name,
        "title": "",
        "has_typo": has_typo,
        "expected_issues": expected_issues,
        "expected_latex": latex,
        "corrected_latex": corrected_latex,
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
        # 不用 tmp_path：报告渲染与 pytest 会话级 basetemp 解耦，避免在
        # 工作区 .pytest_tmp/session-* 下堆积大目录；这里走系统临时目录。
        # Windows 沙箱 ACL 排查见 docs/windows-temp-permissions.md。
        with tempfile.TemporaryDirectory(prefix="typo_check_eval_report_") as tmp:
            report_path = Path(tmp) / "report.html"
            render_report(report_path, results)
            return report_path.read_text(encoding="utf-8")

    def test_summarizes_statuses(self):
        results = [
            _result(id_="typo_01", status="issues"),
            _result(id_="typo_02", status="clean", changes=[], corrected_latex=""),
            _result(id_="typo_03", status="error", error="boom"),
        ]
        html_text = self._render(results)
        assert "发现问题 1" in html_text
        assert "未发现问题 1" in html_text
        assert "失败 1" in html_text

    def test_summarizes_hits_and_false_positives(self):
        results = [
            _result(id_="typo_01", has_typo=True, status="issues"),
            _result(id_="typo_02", has_typo=True, status="clean", changes=[]),
            _result(
                id_="typo_03",
                has_typo=False,
                status="issues",
                expected_issues=[],
                matched=[],
                missed=[],
                false_positive_changes=[0],
            ),
        ]
        html_text = self._render(results)
        assert "期望含错字语料 2 条：发现错字 1 条，漏检 1 条" in html_text
        assert "干净语料 1 条：误报 1 条（误报修改条目 1 个）" in html_text

    def test_groups_by_tier(self):
        results = [
            _result(id_="typo_01", tier=1, tier_name="简单错字"),
            _result(id_="typo_08", tier=2, tier_name="中等错字"),
        ]
        html_text = self._render(results)
        assert "简单错字（1 条）" in html_text
        assert "中等错字（1 条）" in html_text


class TestCardHtml:
    def test_shows_matched_and_missed_marks(self):
        item = _result(
            expected_issues=[
                {"original": "recieve", "corrected": "receive"},
                {"original": "seperates", "corrected": "separates"},
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
                {"type": "typo", "original": "recieve", "corrected": "receive"},
                {"type": "typo", "original": "signal", "corrected": "singal"},
            ],
            false_positive_changes=[1],
        )
        html_text = _card_html(item)
        assert "误报 1 条" in html_text
        assert "[误报]" in html_text

    def test_clean_expected_item_shows_miss_hint(self):
        item = _result(
            status="clean", changes=[], corrected_latex="", matched=[], missed=[0]
        )
        assert "未发现期望错字" in _card_html(item)

    def test_error_card_shows_error(self):
        item = _result(status="error", error="workspace failed", changes=[])
        html_text = _card_html(item)
        assert "失败" in html_text
        assert "workspace failed" in html_text

    def test_shows_corrected_block_when_text_changed(self):
        item = _result(
            latex="The model recieve the signal.",
            corrected_latex="The model receive the signal.",
        )
        html_text = _card_html(item)
        assert "修正后" in html_text
        assert "The model receive the signal." in html_text

    def test_clean_item_has_no_changes_table(self):
        item = _result(
            status="clean",
            has_typo=False,
            expected_issues=[],
            changes=[],
            corrected_latex="",
            matched=[],
            missed=[],
        )
        html_text = _card_html(item)
        assert "检测到的修改" not in html_text
        assert "干净" in html_text
