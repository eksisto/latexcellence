"""统一 LaTeX 格式化工具（latex_formatter）单元测试。"""

from unittest.mock import AsyncMock

import pytest

import backend.utils.latex_formatter as formatter_mod


@pytest.fixture(autouse=True)
def _reset_availability_cache():
    """每个用例独立探测 latexindent 可用性，避免跨用例缓存干扰。"""
    formatter_mod.reset_latexindent_cache()
    yield
    formatter_mod.reset_latexindent_cache()


@pytest.mark.asyncio
async def test_empty_source_returned_unchanged():
    assert await formatter_mod.format_latex_source("") == ""
    assert await formatter_mod.format_latex_source("   \n  ") == "   \n  "


@pytest.mark.asyncio
async def test_falls_back_to_builtin_when_latexindent_unavailable(monkeypatch):
    """latexindent 不可用时走内置格式化器（每层 4 个空格）。"""
    monkeypatch.setattr(formatter_mod, "is_latexindent_available", lambda: False)

    called = {"n": 0}

    async def fake_latexindent(source: str) -> str:
        called["n"] += 1
        return source

    monkeypatch.setattr(formatter_mod, "format_with_latexindent", fake_latexindent)

    source = (
        r"\begin{document}" + "\n"
        r"\begin{itemize}" + "\n"
        r"\item x" + "\n"
        r"\end{itemize}" + "\n"
        r"\end{document}"
    )
    result = await formatter_mod.format_latex_source(source)

    assert "    \\begin{itemize}" in result
    assert "        \\item x" in result
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_uses_latexindent_when_available(monkeypatch):
    """latexindent 可用时直接委托给 latexindent 调用封装。"""
    monkeypatch.setattr(formatter_mod, "is_latexindent_available", lambda: True)
    formatted = "formatted-by-latexindent"

    async def fake_latexindent(source: str) -> str:
        assert source == "raw source"
        return formatted

    monkeypatch.setattr(formatter_mod, "format_with_latexindent", fake_latexindent)

    result = await formatter_mod.format_latex_source("raw source")
    assert result == formatted


@pytest.mark.asyncio
async def test_availability_cached_after_first_call(monkeypatch):
    """可用性探测结果进程内缓存，重复调用不再探测。"""
    calls = {"n": 0}

    def detect() -> bool:
        calls["n"] += 1
        return False

    monkeypatch.setattr(formatter_mod, "is_latexindent_available", detect)
    monkeypatch.setattr(
        formatter_mod, "format_with_latexindent", AsyncMock(return_value="x")
    )

    await formatter_mod.format_latex_source("a")
    await formatter_mod.format_latex_source("b")

    assert calls["n"] == 1
