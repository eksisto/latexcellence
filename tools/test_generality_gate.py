# -*- coding: utf-8 -*-
"""generality_gate.py 的单元测试（不依赖 LLM 端点与 git 仓库状态）。"""

import sys
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_ROOT))

from generality_gate import (  # noqa: E402
    check_hardcoding,
    compare_prompt_size,
    parse_git_diff,
    prompt_size_metrics,
    regression_verdict,
    resolve_scenarios,
)

DIFF = """\
diff --git a/backend/core/prompts.py b/backend/core/prompts.py
--- a/backend/core/prompts.py
+++ b/backend/core/prompts.py
@@ -123,6 +123,8 @@ def foo():
 old line
+new line one
+new line two
@@ -500,0 +502,1 @@ def bar():
+another new line
"""


def test_parse_git_diff() -> None:
    rows = parse_git_diff(DIFF)
    assert rows == [
        {"file": "backend/core/prompts.py", "line": 124, "text": "new line one"},
        {"file": "backend/core/prompts.py", "line": 125, "text": "new line two"},
        {"file": "backend/core/prompts.py", "line": 502, "text": "another new line"},
    ]


def test_strong_pattern_detected(tmp_path: Path) -> None:
    src = tmp_path / "prompt.py"
    src.write_text("# 复刻 tjuthesis 用 C:\\opt\\texlive 的字体\n", encoding="utf-8")
    report = check_hardcoding(full=True, paths=[str(src)], project_root=tmp_path)
    assert report["passed"] is False
    names = {h["pattern"] for h in report["failures"]}
    assert "学校/模板名" in names
    assert "OS 路径" in names


def test_example_font_is_warning_not_failure(tmp_path: Path) -> None:
    src = tmp_path / "prompt.py"
    src.write_text("use a portable CJK font (e.g. SimSun for body)\n", encoding="utf-8")
    report = check_hardcoding(full=True, paths=[str(src)], project_root=tmp_path)
    assert report["passed"] is True
    assert {h["kind"] for h in report["warnings"]} == {"example"}


def test_os_env_assertion_is_failure(tmp_path: Path) -> None:
    src = tmp_path / "prompt.py"
    src.write_text(
        "use a Windows-safe CJK font (e.g. SimSun for body)\n", encoding="utf-8"
    )
    report = check_hardcoding(full=True, paths=[str(src)], project_root=tmp_path)
    assert report["passed"] is False
    names = {h["pattern"] for h in report["failures"]}
    assert "OS 环境断言" in names


def test_tju_prefix_macro_is_failure(tmp_path: Path) -> None:
    src = tmp_path / "prompt.py"
    src.write_text(
        "define NEW names (e.g. `\\mktjtitlepage`, `\\tj@...`).\n", encoding="utf-8"
    )
    report = check_hardcoding(full=True, paths=[str(src)], project_root=tmp_path)
    assert report["passed"] is False
    names = {h["pattern"] for h in report["failures"]}
    assert "TJU 前缀私有宏残留" in names


def test_suspicious_font_flag(tmp_path: Path) -> None:
    src = tmp_path / "prompt.py"
    src.write_text("matplotlib font chain: Arial, Noto Sans CJK SC\n", encoding="utf-8")
    report = check_hardcoding(full=True, paths=[str(src)], project_root=tmp_path)
    assert report["passed"] is True  # 弱特征不自动失败
    assert any(h["kind"] == "suspicious" for h in report["warnings"])


def test_prompt_size_metrics() -> None:
    metrics = prompt_size_metrics()
    for mod in ("template_gen", "style_modify", "plot_gen"):
        assert metrics[f"{mod}_chars"] > 0
        assert metrics[f"{mod}_entries"] > 0
    assert metrics["lines"] > 100


def test_compare_prompt_size_warns_on_big_delta() -> None:
    baseline = {"template_gen_chars": 1000}
    current = {"template_gen_chars": 7000}
    messages = compare_prompt_size(baseline, current, warn_chars=5000, warn_ratio=0.15)
    assert messages
    assert "template_gen_chars" in messages[0]


def test_resolve_scenarios_default_skips_holdout() -> None:
    keys, messages = resolve_scenarios(["a", "b", "c"], {"c"})
    assert keys == ["a", "b"]
    assert any("holdout" in m for m in messages)


def test_resolve_scenarios_explicit_holdout_warns_but_runs() -> None:
    keys, messages = resolve_scenarios(["a", "b", "c"], {"c"}, requested=["c"])
    assert keys == ["c"]
    assert any("不得用于调优决策" in m for m in messages)


def test_resolve_scenarios_include_holdout() -> None:
    keys, messages = resolve_scenarios(["a", "b", "c"], {"c"}, include_holdout=True)
    assert keys == ["a", "b", "c"]
    assert not messages


def test_regression_verdict_control_regression_fails() -> None:
    a = {"s1": {"quality": {"score": 80}}, "s2": {"quality": {"score": 70}}}
    b = {"s1": {"quality": {"score": 95}}, "s2": {"quality": {"score": 60}}}
    verdict = regression_verdict(
        a,
        b,
        score_of=lambda m: m["quality"]["score"],
        target_keys=["s1"],
        tolerance=5,
    )
    assert verdict["failed"] is True
    assert any("s2" in issue for issue in verdict["issues"])


def test_regression_verdict_target_improves_all_green() -> None:
    a = {"s1": {"score": 80}, "s2": {"score": 70}}
    b = {"s1": {"score": 95}, "s2": {"score": 72}}
    verdict = regression_verdict(
        a, b, score_of=lambda m: m["score"], target_keys=["s1"]
    )
    assert verdict["failed"] is False


def test_regression_verdict_visual_regression() -> None:
    a = {"s1": {"score": 80}}
    b = {"s1": {"score": 82}}
    verdict = regression_verdict(
        a,
        b,
        score_of=lambda m: m["score"],
        visual_a={"s1": 8.0},
        visual_b={"s1": 6.0},
        target_keys=["s1"],
    )
    assert verdict["failed"] is True
    assert any("视觉回退" in issue for issue in verdict["issues"])
