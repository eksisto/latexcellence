"""formula_ocr_eval 报告生成逻辑的单元测试。

纯 HTML 字符串生成，不依赖 LLM 调用或 TeX 渲染：直接构造合成结果列表，
验证 inline/display mismatch 汇总数字与卡片标红逻辑。
"""

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
for _p in (str(PROJECT_ROOT), str(TOOLS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from formula_ocr_eval.run_eval import _card_html, render_report


def _result(
    id_: str = "formula_01",
    *,
    status: str = "ok",
    result_inline: bool = True,
    expected_inline: bool = True,
    confidence: float = 0.95,
    latex: str = "x^2",
) -> dict:
    return {
        "id": id_,
        "tier": 1,
        "tier_name": "简单公式",
        "title": "",
        "expected_latex": latex,
        "expected_inline": expected_inline,
        "status": status,
        "latex": latex,
        "confidence": confidence,
        "result_inline": result_inline,
        "low_confidence": False,
    }


class TestRenderReport:
    @staticmethod
    def _render(results: list[dict]) -> str:
        # 不用 tmp_path：报告渲染与 pytest 会话级 basetemp 解耦，避免在
        # 工作区 .pytest_tmp/session-* 下堆积大目录；这里走系统临时目录。
        # Windows 沙箱 ACL 排查见 docs/windows-temp-permissions.md。
        with tempfile.TemporaryDirectory(prefix="formula_ocr_eval_report_") as tmp:
            report_path = Path(tmp) / "report.html"
            render_report(report_path, results)
            return report_path.read_text(encoding="utf-8")

    def test_summarizes_inline_mismatch(self):
        results = [
            _result(id_="formula_01", result_inline=True, expected_inline=False),
            _result(id_="formula_02", result_inline=True, expected_inline=True),
        ]
        html_text = self._render(results)
        assert "inline/display 判定与语料不一致：1 条" in html_text

    def test_zero_mismatch(self):
        results = [
            _result(id_="formula_01", result_inline=True, expected_inline=True),
            _result(id_="formula_02", result_inline=False, expected_inline=False),
        ]
        html_text = self._render(results)
        assert "inline/display 判定与语料不一致：0 条" in html_text

    def test_failed_items_not_counted_as_mismatch(self):
        """status 非 ok 的条目无识别结果可比，不参与 mismatch 统计。"""
        results = [
            _result(
                id_="formula_01",
                status="error",
                result_inline=False,
                expected_inline=True,
            ),
            _result(id_="formula_02", result_inline=True, expected_inline=False),
        ]
        html_text = self._render(results)
        assert "inline/display 判定与语料不一致：1 条" in html_text


class TestCardHtml:
    def test_highlights_mismatch(self):
        item = _result(result_inline=True, expected_inline=False)
        html_text = _card_html(item)
        assert "inline 判定不一致" in html_text
        assert "预期: 展示" in html_text

    def test_no_highlight_when_consistent(self):
        item = _result(result_inline=True, expected_inline=True)
        html_text = _card_html(item)
        assert "inline 判定不一致" not in html_text
        assert "预期: 行内" in html_text
