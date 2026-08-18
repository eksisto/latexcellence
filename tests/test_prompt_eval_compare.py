"""prompt_comprehension_eval 工具函数单元测试。

覆盖：wilson_ci 计算、compare_reports 回归检测、_prompt_hash 确定性、
build_report 元数据与通过率。
"""

from __future__ import annotations

import pytest

from tools.prompt_comprehension_eval.run_eval import (
    build_report,
    compare_reports,
    wilson_ci,
    _prompt_hash,
)

# ── wilson_ci ──────────────────────────────────────────────────────────────


class TestWilsonCI:
    def test_known_values(self) -> None:
        low, high = wilson_ci(50, 100)
        assert 0.4 < low < 0.5
        assert 0.5 < high < 0.6

    def test_all_pass(self) -> None:
        low, high = wilson_ci(10, 10)
        assert low > 0.7
        assert high == 1.0

    def test_all_fail(self) -> None:
        low, high = wilson_ci(0, 10)
        assert low == 0.0
        assert high < 0.3

    def test_n_zero(self) -> None:
        low, high = wilson_ci(0, 0)
        assert low == 0.0
        assert high == 1.0

    def test_n_one_pass(self) -> None:
        low, high = wilson_ci(1, 1)
        assert low >= 0.0
        assert high == 1.0

    def test_n_one_fail(self) -> None:
        low, high = wilson_ci(0, 1)
        assert low == 0.0
        assert high < 1.0


# ── compare_reports ────────────────────────────────────────────────────────


def _make_report(cases: list[dict]) -> dict:
    return {
        "metadata": {"model": "test-model", "timestamp": "2026-01-01T00:00:00"},
        "summary": {
            "total": len(cases),
            "PASS": sum(1 for c in cases if c["status"] == "PASS"),
        },
        "cases": cases,
    }


class TestCompareReports:
    def test_no_regression(self) -> None:
        cases = [
            {"name": "a", "status": "PASS", "detail": "ok"},
            {"name": "b", "status": "PASS", "detail": "ok"},
        ]
        result = compare_reports(_make_report(cases), _make_report(cases))
        assert result["verdict"] == "PASS"
        assert result["summary"]["regressions"] == 0
        assert result["summary"]["unchanged"] == 2

    def test_with_regression(self) -> None:
        baseline = _make_report(
            [
                {"name": "a", "status": "PASS", "detail": "ok"},
                {"name": "b", "status": "PASS", "detail": "ok"},
            ]
        )
        current = _make_report(
            [
                {"name": "a", "status": "PASS", "detail": "ok"},
                {"name": "b", "status": "FAIL", "detail": "broken"},
            ]
        )
        result = compare_reports(baseline, current)
        assert result["verdict"] == "REGRESSION"
        assert result["summary"]["regressions"] == 1
        assert result["regressions"][0]["name"] == "b"

    def test_with_improvement(self) -> None:
        baseline = _make_report(
            [
                {"name": "a", "status": "FAIL", "detail": "broken"},
            ]
        )
        current = _make_report(
            [
                {"name": "a", "status": "PASS", "detail": "ok"},
            ]
        )
        result = compare_reports(baseline, current)
        assert result["verdict"] == "IMPROVED"
        assert result["summary"]["improvements"] == 1

    def test_new_case(self) -> None:
        baseline = _make_report(
            [
                {"name": "a", "status": "PASS", "detail": "ok"},
            ]
        )
        current = _make_report(
            [
                {"name": "a", "status": "PASS", "detail": "ok"},
                {"name": "b", "status": "PASS", "detail": "ok"},
            ]
        )
        result = compare_reports(baseline, current)
        assert result["verdict"] == "PASS"
        assert result["summary"]["new_cases"] == 1

    def test_removed_case(self) -> None:
        baseline = _make_report(
            [
                {"name": "a", "status": "PASS", "detail": "ok"},
                {"name": "b", "status": "PASS", "detail": "ok"},
            ]
        )
        current = _make_report(
            [
                {"name": "a", "status": "PASS", "detail": "ok"},
            ]
        )
        result = compare_reports(baseline, current)
        assert result["verdict"] == "PASS"
        assert result["summary"]["removed_cases"] == 1

    def test_minor_regression(self) -> None:
        baseline = _make_report(
            [
                {"name": "a", "status": "PASS", "detail": "ok"},
            ]
        )
        current = _make_report(
            [
                {"name": "a", "status": "PARTIAL", "detail": "partial"},
            ]
        )
        result = compare_reports(baseline, current)
        assert result["verdict"] == "REGRESSION"
        assert result["summary"]["regressions"] == 1

    def test_fail_to_worse_fail(self) -> None:
        baseline = _make_report(
            [
                {"name": "a", "status": "FAIL", "detail": "broken"},
            ]
        )
        current = _make_report(
            [
                {"name": "a", "status": "FAIL", "detail": "still broken"},
            ]
        )
        result = compare_reports(baseline, current)
        assert result["verdict"] == "PASS"
        assert result["summary"]["unchanged"] == 1


# ── _prompt_hash ──────────────────────────────────────────────────────────


class TestPromptHash:
    def test_deterministic(self) -> None:
        cases = [
            {"system": "sys1", "user": "usr1"},
            {"system": "sys2", "user": "usr2"},
        ]
        h1 = _prompt_hash(cases)
        h2 = _prompt_hash(cases)
        assert h1 == h2
        assert len(h1) == 8

    def test_differs_on_change(self) -> None:
        cases_a = [{"system": "sys", "user": "a"}]
        cases_b = [{"system": "sys", "user": "b"}]
        assert _prompt_hash(cases_a) != _prompt_hash(cases_b)


# ── build_report ──────────────────────────────────────────────────────────


class TestBuildReport:
    def test_metadata_fields(self) -> None:
        results = [
            {
                "name": "a",
                "status": "PASS",
                "detail": "ok",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "latency_ms": 500,
            },
        ]
        report = build_report(
            results,
            model="test-model",
            commit_sha="abc1234",
            commit_msg="test commit",
            prompt_hash="deadbeef",
            repeat=1,
            duration_seconds=10.5,
        )
        meta = report["metadata"]
        assert meta["model"] == "test-model"
        assert meta["commit_sha"] == "abc1234"
        assert meta["prompt_hash"] == "deadbeef"
        assert meta["repeat"] == 1
        assert meta["total_prompt_tokens"] == 100
        assert meta["total_completion_tokens"] == 50
        assert meta["total_tokens"] == 150

    def test_pass_rate(self) -> None:
        results = [
            {"name": "a", "status": "PASS", "detail": "ok"},
            {"name": "b", "status": "PASS", "detail": "ok"},
            {"name": "c", "status": "FAIL", "detail": "bad"},
        ]
        report = build_report(results)
        assert report["summary"]["pass_rate"] == pytest.approx(2 / 3, abs=0.01)

    def test_repeat_1_no_ci(self) -> None:
        results = [{"name": "a", "status": "PASS", "detail": "ok"}]
        report = build_report(results, repeat=1)
        assert "pass_rate_ci95" not in report["summary"]

    def test_repeat_gt1_has_ci(self) -> None:
        results = [
            {"name": "a", "status": "PASS", "detail": "ok"},
            {"name": "b", "status": "FAIL", "detail": "bad"},
        ]
        report = build_report(results, repeat=3)
        assert "pass_rate_ci95" in report["summary"]
        ci = report["summary"]["pass_rate_ci95"]
        assert ci[0] < ci[1]
