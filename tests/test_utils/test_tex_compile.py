"""
tex_compile 单元测试。

覆盖内容：
    - TexCompileResult Pydantic 模型校验
    - run_tex_compile 成功/失败/超时场景
    - texfot 不可用时降级为正则过滤
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.tex_compile import (
    TexCompileResult,
    _check_texfot_available,
    _filter_with_regex,
    run_tex_compile,
)

# ── TexCompileResult 模型测试 ─────────────────────────────────────────────


class TestTexCompileResultModel:
    """TexCompileResult 模型校验测试。"""

    def test_valid_success(self):
        data = {
            "success": True,
            "filtered_output": "ok",
            "raw_output": "full log",
        }
        result = TexCompileResult.model_validate(data)
        assert result.success is True
        assert result.timed_out is False
        assert result.used_texfot is True

    def test_valid_failure(self):
        data = {
            "success": False,
            "filtered_output": "! Error",
            "raw_output": "full log with error",
        }
        result = TexCompileResult.model_validate(data)
        assert result.success is False

    def test_valid_timeout(self):
        data = {
            "success": False,
            "filtered_output": "",
            "raw_output": "partial",
            "timed_out": True,
        }
        result = TexCompileResult.model_validate(data)
        assert result.timed_out is True

    def test_defaults(self):
        data = {"success": True, "filtered_output": "", "raw_output": ""}
        result = TexCompileResult.model_validate(data)
        assert result.timed_out is False
        assert result.used_texfot is True


# ── _filter_with_regex 测试 ───────────────────────────────────────────────


class TestFilterWithRegex:
    """降级正则过滤测试。"""

    def test_extracts_undefined_control_sequence(self):
        log = (
            "Some preamble\n! Undefined control sequence.\nl.42 \\badcommand\nMore text"
        )
        result = _filter_with_regex(log)
        assert "Undefined control sequence" in result
        assert "\\badcommand" in result

    def test_extracts_emergency_stop(self):
        log = "Text\n! Emergency stop.\nl.10 \\end{document}\n"
        result = _filter_with_regex(log)
        assert "Emergency stop" in result

    def test_no_error_patterns_returns_tail(self):
        log = "a" * 4000
        result = _filter_with_regex(log)
        assert len(result) <= 3000

    def test_empty_log(self):
        result = _filter_with_regex("")
        assert result == ""

    def test_short_log_without_errors(self):
        log = "short log"
        result = _filter_with_regex(log)
        assert result == "short log"


# ── _check_texfot_available 测试 ──────────────────────────────────────────


class TestCheckTexfotAvailable:
    """texfot 可用性检测测试。"""

    def test_returns_bool(self):
        import backend.utils.tex_compile as mod

        mod._texfot_available = None
        result = _check_texfot_available()
        assert isinstance(result, bool)
        mod._texfot_available = None

    def test_caches_result(self):
        import backend.utils.tex_compile as mod

        mod._texfot_available = True
        with patch("shutil.which") as mock_which:
            result = _check_texfot_available()
            assert result is True
            mock_which.assert_not_called()

        mod._texfot_available = None


# ── run_tex_compile 测试 ───────────────────────────────────────────────────


class TestRunTexCompile:
    """run_tex_compile 编译执行测试。"""

    def _make_process(self, returncode: int, stdout: bytes, stderr: bytes = b""):
        process = MagicMock()
        process.returncode = returncode

        async def communicate():
            return (stdout, stderr)

        process.communicate = communicate
        return process

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        """每个测试前重置 texfot 可用性缓存。"""
        import backend.utils.tex_compile as mod

        mod._texfot_available = None
        yield
        mod._texfot_available = None

    @pytest.mark.asyncio
    async def test_texfot_success(self):
        """texfot 可用且编译成功。"""
        process = self._make_process(0, b"Output written on main.pdf\n")
        with patch("shutil.which", return_value="/usr/bin/texfot"):
            with patch("asyncio.create_subprocess_exec", return_value=process):
                with patch(
                    "asyncio.wait_for",
                    new_callable=AsyncMock,
                    return_value=(b"Output written on main.pdf\n", b""),
                ):
                    result = await run_tex_compile("main.tex", "/tmp", 60)

        assert result.success is True
        assert "Output written" in result.filtered_output
        assert result.used_texfot is True

    @pytest.mark.asyncio
    async def test_texfot_failure(self):
        """texfot 可用但编译失败。"""
        error_log = b"! Undefined control sequence.\nl.42 \\badcommand\n"
        process = self._make_process(1, error_log)
        with patch("shutil.which", return_value="/usr/bin/texfot"):
            with patch("asyncio.create_subprocess_exec", return_value=process):
                with patch(
                    "asyncio.wait_for",
                    new_callable=AsyncMock,
                    return_value=(error_log, b""),
                ):
                    result = await run_tex_compile("main.tex", "/tmp", 60)

        assert result.success is False
        assert "Undefined control sequence" in result.filtered_output
        assert result.used_texfot is True

    @pytest.mark.asyncio
    async def test_texfot_timeout(self):
        """编译超时。"""
        process = self._make_process(-1, b"partial")
        process.kill = MagicMock()

        async def communicate_after_kill():
            return (b"partial", b"")

        process.communicate = communicate_after_kill

        with patch("shutil.which", return_value="/usr/bin/texfot"):
            with patch("asyncio.create_subprocess_exec", return_value=process):
                with patch(
                    "asyncio.wait_for",
                    new_callable=AsyncMock,
                    side_effect=asyncio.TimeoutError(),
                ):
                    result = await run_tex_compile("main.tex", "/tmp", 60)

        assert result.success is False
        assert result.timed_out is True

    @pytest.mark.asyncio
    async def test_fallback_when_texfot_unavailable(self):
        """texfot 不可用时降级为 latexmk + 正则。"""
        error_log = b"! Undefined control sequence.\nl.42 \\badcommand\nNormal output\n"
        process = self._make_process(1, error_log)
        with patch("shutil.which", return_value=None):
            with patch("asyncio.create_subprocess_exec", return_value=process):
                with patch(
                    "asyncio.wait_for",
                    new_callable=AsyncMock,
                    return_value=(error_log, b""),
                ):
                    result = await run_tex_compile("main.tex", "/tmp", 60)

        assert result.success is False
        assert result.used_texfot is False
        assert "Undefined control sequence" in result.filtered_output

    @pytest.mark.asyncio
    async def test_fallback_success_no_filtering(self):
        """降级路径编译成功时不做正则过滤。"""
        ok_log = b"Output written on main.pdf\n"
        process = self._make_process(0, ok_log)
        with patch("shutil.which", return_value=None):
            with patch("asyncio.create_subprocess_exec", return_value=process):
                with patch(
                    "asyncio.wait_for",
                    new_callable=AsyncMock,
                    return_value=(ok_log, b""),
                ):
                    result = await run_tex_compile("main.tex", "/tmp", 60)

        assert result.success is True
        assert result.filtered_output == ok_log.decode()
        assert result.used_texfot is False

    @pytest.mark.asyncio
    async def test_extra_args_passed(self):
        """额外参数正确传递给 latexmk。"""
        process = self._make_process(0, b"ok")
        with patch("shutil.which", return_value="/usr/bin/texfot"):
            with patch(
                "asyncio.create_subprocess_exec", return_value=process
            ) as mock_exec:
                with patch(
                    "asyncio.wait_for",
                    new_callable=AsyncMock,
                    return_value=(b"ok", b""),
                ):
                    await run_tex_compile(
                        "main.tex", "/tmp", 60, extra_args=["-halt-on-error"]
                    )

        # call_args[0] 是所有 positional args 的 tuple
        cmd = mock_exec.call_args[0]
        assert "-halt-on-error" in cmd
        assert cmd.index("-halt-on-error") < cmd.index("main.tex")

    @pytest.mark.asyncio
    async def test_raw_output_preserved(self):
        """原始输出始终保留（含 stderr）。"""
        process = self._make_process(0, b"stdout", b"stderr info")
        with patch("shutil.which", return_value="/usr/bin/texfot"):
            with patch("asyncio.create_subprocess_exec", return_value=process):
                with patch(
                    "asyncio.wait_for",
                    new_callable=AsyncMock,
                    return_value=(b"stdout", b"stderr info"),
                ):
                    result = await run_tex_compile("main.tex", "/tmp", 60)

        assert "stderr info" in result.raw_output
        assert "stdout" in result.raw_output
