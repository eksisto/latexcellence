"""table_ocr_eval 报告生成逻辑的单元测试。

纯 HTML 字符串生成，不依赖 LLM 调用或 TeX 渲染：直接构造合成结果列表，
验证状态汇总、低置信度统计、按难度分层分组与卡片展示逻辑。
"""

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
for _p in (str(PROJECT_ROOT), str(TOOLS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from table_ocr_eval.run_eval import _card_html, render_report


def _result(
    id_: str = "table_01",
    *,
    tier: int = 1,
    tier_name: str = "简单表格",
    status: str = "ok",
    confidence: float = 0.95,
    latex: str = r"\begin{table}[htbp]\centering A & B \end{table}",
    low_confidence: bool = False,
) -> dict:
    return {
        "id": id_,
        "tier": tier,
        "tier_name": tier_name,
        "title": "",
        "expected_latex": latex,
        "status": status,
        "latex": latex,
        "confidence": confidence,
        "low_confidence": low_confidence,
    }


class TestRenderReport:
    @staticmethod
    def _render(results: list[dict]) -> str:
        # 不用 tmp_path：pytest.ini 把 basetemp 固定在项目根 .pytest_tmp，
        # 残留目录被锁时 tmp_path 会直接报错；这里走系统临时目录。
        with tempfile.TemporaryDirectory(prefix="table_ocr_eval_report_") as tmp:
            report_path = Path(tmp) / "report.html"
            render_report(report_path, results)
            return report_path.read_text(encoding="utf-8")

    def test_summarizes_statuses(self):
        results = [
            _result(id_="table_01", status="ok"),
            _result(id_="table_02", status="empty", latex="", confidence=0.0),
            _result(id_="table_03", status="error", latex=""),
        ]
        html_text = self._render(results)
        assert "识别成功 1" in html_text
        assert "空结果 1" in html_text
        assert "失败 1" in html_text

    def test_counts_low_confidence(self):
        results = [
            _result(id_="table_01", low_confidence=True),
            _result(id_="table_02", low_confidence=False),
        ]
        html_text = self._render(results)
        assert "低置信度（&lt; 0.7）：1 条" in html_text

    def test_groups_by_tier(self):
        results = [
            _result(id_="table_01", tier=1, tier_name="简单表格"),
            _result(id_="table_08", tier=2, tier_name="中等表格"),
        ]
        html_text = self._render(results)
        assert "简单表格（1 条）" in html_text
        assert "中等表格（1 条）" in html_text


class TestCardHtml:
    def test_shows_confidence_and_badge(self):
        item = _result(confidence=0.92)
        html_text = _card_html(item)
        assert "识别成功" in html_text
        assert "置信度 0.92" in html_text

    def test_highlights_low_confidence(self):
        item = _result(low_confidence=True)
        assert "低置信度" in _card_html(item)

    def test_render_error_keeps_latex(self):
        item = _result(status="render_error", latex=r"\begin{table}\end{table}")
        item["error"] = "结果表格渲染失败：boom"
        html_text = _card_html(item)
        assert "结果表格渲染失败" in html_text
        assert r"\begin{table}\end{table}" in html_text

    def test_empty_shows_no_result_image(self):
        item = _result(status="empty", latex="", confidence=0.0)
        assert "识别结果为空" in _card_html(item)
