"""
tex_compile 单元测试。

覆盖内容：
    - TexCompileResult Pydantic 模型校验
    - run_tex_compile 成功/失败/超时场景
    - texfot 不可用时降级为正则过滤
    - detect_engine_from_file 引擎自动检测
    - inject_pdfx_for_pdfa PDF/A 包注入
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.core.config import get_config
from backend.utils.tex_compile import (
    TexCompileResult,
    _check_texfot_available,
    _filter_with_regex,
    _prepare_pdfa_workspace,
    _strip_tex_comments,
    detect_engine_from_file,
    inject_pdfx_for_pdfa,
    run_tex_compile,
    sanitize_output_paths,
)


async def _fake_wait_for(awaitable, timeout):
    """替身 asyncio.wait_for：真实 await 传入的协程，避免“协程从未等待”警告。"""
    return await awaitable


async def _fake_wait_for_timeout(awaitable, timeout):
    """替身 asyncio.wait_for（超时路径）：先消费协程再抛 TimeoutError。"""
    await awaitable
    raise asyncio.TimeoutError()


class TestStripTexComments:
    """_strip_tex_comments 的注释剥离语义（与 latex_json_guard 一致）。"""

    def test_escaped_percent_kept(self):
        assert _strip_tex_comments(r"a \% b") == r"a \% b"

    def test_plain_comment_stripped(self):
        assert _strip_tex_comments("text % comment") == "text"

    def test_double_backslash_percent_starts_comment(self):
        # \\% 中 \\ 是换行命令，其后的 % 开始注释（偶数反斜杠语义），
        # 整段注释（含 \\ 命令）随匹配一起剥离
        assert _strip_tex_comments(r"line \\% \usepackage{fontspec}") == "line"

    def test_comment_removed_before_engine_detection(self, tmp_path):
        tex_file = tmp_path / "main.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\\% \usepackage{fontspec}"  # 注释内出现 fontspec 不应触发 xelatex
            "\n"
            r"\begin{document}Hello\end{document}",
            encoding="utf-8",
        )
        assert detect_engine_from_file(tex_file) == "pdflatex"


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


class TestSanitizeOutputPaths:
    """编译输出绝对路径脱敏（多用户模式不向用户暴露服务器路径）。"""

    def test_file_paths_relativized(self, tmp_path):
        log = f"Latexmk: Log file is {tmp_path / 'main.log'}"
        result = sanitize_output_paths(log, tmp_path)
        assert str(tmp_path) not in result
        assert "./main.log" in result

    def test_sibling_prefix_not_corrupted(self, tmp_path):
        # /a/b 与 /a/bc 前缀相近时，不得误伤兄弟目录
        sibling = tmp_path.parent / (tmp_path.name + "x")
        log = f"see {sibling / 'other.tex'} and {tmp_path / 'main.log'}"
        result = sanitize_output_paths(log, tmp_path)
        assert str(sibling / "other.tex") in result
        assert "./main.log" in result

    def test_no_match_unchanged(self):
        assert sanitize_output_paths("nothing here", Path("/nonexistent-dir")) == (
            "nothing here"
        )

    def test_dotdot_and_symlink_cwd_resolved(self, tmp_path):
        """cwd 含 .. 段或经符号链接进入时，先 resolve 再脱敏（R7 加固）。

        多用户项目目录常经符号链接访问；未 resolve 的 base 与日志中出现的
        真实物理路径不一致会导致漏脱敏。
        """
        real = tmp_path / "real_proj"
        real.mkdir()
        link = tmp_path / "link_proj"
        link.symlink_to(real, target_is_directory=True)
        # 含 .. 段且经过符号链接的工作目录：resolve 后应与 real 等价
        tricky_cwd = link / ".." / "link_proj" / "sub" / ".."
        log = f"Latexmk: Log file is {real / 'main.log'}\ncd {real}\n"
        result = sanitize_output_paths(log, tricky_cwd)
        assert str(real) not in result
        assert str(link) not in result
        assert "./main.log" in result
        assert "cd .\n" in result

    def test_bare_cwd_directory_itself_rewritten(self, tmp_path):
        """日志单独打印目录本身（裸 cwd 后跟行尾/引号/空白）→ 改写为 "."。"""
        log = (
            f"Output directory: {tmp_path}\n"
            f'chdir into "{tmp_path}" done\n'
            f"working dir is {tmp_path} now\n"
        )
        result = sanitize_output_paths(log, tmp_path)
        assert str(tmp_path) not in result
        assert "Output directory: .\n" in result
        assert '"."' in result
        assert "working dir is . now" in result

    def test_bare_cwd_as_name_prefix_not_corrupted(self, tmp_path):
        """裸边界替换只认界定符：与 cwd 同名的兄弟目录名（无分隔符）不得误改写。"""
        longer = tmp_path.parent / f"{tmp_path.name}_extra"
        log = f"scanning {longer} done"
        result = sanitize_output_paths(log, tmp_path)
        assert str(longer) in result


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
        # 必须给一个不存在的真实 PID：MagicMock 缺省 pid 的 __index__ 为 1，
        # 超时路径里的 killpg(getpgid(1)) 会误杀当前进程组（pytest 自身）
        process.pid = 2147483000

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
                with patch("asyncio.wait_for", new=_fake_wait_for):
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
                with patch("asyncio.wait_for", new=_fake_wait_for):
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
                with patch("asyncio.wait_for", new=_fake_wait_for_timeout):
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
                with patch("asyncio.wait_for", new=_fake_wait_for):
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
                with patch("asyncio.wait_for", new=_fake_wait_for):
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
                with patch("asyncio.wait_for", new=_fake_wait_for):
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
                with patch("asyncio.wait_for", new=_fake_wait_for):
                    result = await run_tex_compile("main.tex", "/tmp", 60)

        assert "stderr info" in result.raw_output
        assert "stdout" in result.raw_output

    @pytest.mark.asyncio
    async def test_multiuser_output_paths_sanitized(self, tmp_path, monkeypatch):
        """多用户模式：编译输出中的工作目录绝对路径脱敏为 ./ 相对形式。"""
        monkeypatch.setattr(get_config().multi_user, "enabled", True)
        log_line = f"Latexmk: Log file is {tmp_path / 'main.log'}\n"
        process = self._make_process(0, log_line.encode())
        with patch("shutil.which", return_value=None):
            with patch("asyncio.create_subprocess_exec", return_value=process):
                with patch("asyncio.wait_for", new=_fake_wait_for):
                    result = await run_tex_compile("main.tex", str(tmp_path), 60)

        assert result.success is True
        assert str(tmp_path) not in result.raw_output
        assert "./main.log" in result.raw_output

    @pytest.mark.asyncio
    async def test_single_user_output_keeps_absolute_paths(self, tmp_path, monkeypatch):
        """单用户模式：编译输出保留原始绝对路径（维持现状）。"""
        monkeypatch.setattr(get_config().multi_user, "enabled", False)
        log_line = f"Latexmk: Log file is {tmp_path / 'main.log'}\n"
        process = self._make_process(0, log_line.encode())
        with patch("shutil.which", return_value=None):
            with patch("asyncio.create_subprocess_exec", return_value=process):
                with patch("asyncio.wait_for", new=_fake_wait_for):
                    result = await run_tex_compile("main.tex", str(tmp_path), 60)

        assert result.success is True
        assert str(tmp_path / "main.log") in result.raw_output


# ── detect_engine_from_file 测试 ──────────────────────────────────────────


class TestDetectEngineFromFile:
    """引擎自动检测测试。"""

    def test_detect_xelatex_fontspec(self, tmp_path):
        """检测 fontspec 宏包 → xelatex。"""
        tex_file = tmp_path / "test.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\usepackage{fontspec}"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )
        assert detect_engine_from_file(tex_file) == "xelatex"

    def test_detect_xelatex_from_local_style_dependency(self, tmp_path):
        """主文件只加载本地 .sty 时，也应沿依赖检测 fontspec。"""
        tex_file = tmp_path / "main.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\usepackage{local-theme}"
            "\n"
            r"\begin{document}Hello\end{document}",
            encoding="utf-8",
        )
        (tmp_path / "local-theme.sty").write_text(
            r"\ProvidesPackage{local-theme}" "\n" r"\RequirePackage{fontspec}",
            encoding="utf-8",
        )

        assert detect_engine_from_file(tex_file) == "xelatex"

    def test_detect_xelatex_from_local_input_unicode(self, tmp_path):
        r"""主文件通过本地 \input 引入中文时，也应排除 pdfLaTeX。"""
        tex_file = tmp_path / "main.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\input{sections/body}"
            "\n"
            r"\begin{document}\end{document}",
            encoding="utf-8",
        )
        (tmp_path / "sections").mkdir()
        (tmp_path / "sections" / "body.tex").write_text("中文内容", encoding="utf-8")

        assert detect_engine_from_file(tex_file) == "xelatex"

    def test_ignore_unloaded_local_style_dependency(self, tmp_path):
        """未被主文件加载的本地样式不能污染引擎检测。"""
        tex_file = tmp_path / "main.tex"
        tex_file.write_text(
            r"\documentclass{article}\begin{document}Hello\end{document}",
            encoding="utf-8",
        )
        (tmp_path / "unused.sty").write_text(
            r"\RequirePackage{fontspec}", encoding="utf-8"
        )

        assert detect_engine_from_file(tex_file) == "pdflatex"

    def test_detect_xelatex_xecjk(self, tmp_path):
        """检测 xeCJK 宏包 → xelatex。"""
        tex_file = tmp_path / "test.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\usepackage{xeCJK}"
            "\n"
            r"\begin{document}"
            "\n"
            r"中文"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )
        assert detect_engine_from_file(tex_file) == "xelatex"

    def test_detect_xelatex_polyglossia(self, tmp_path):
        """检测 polyglossia 宏包 → xelatex。"""
        tex_file = tmp_path / "test.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\usepackage{polyglossia}"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )
        assert detect_engine_from_file(tex_file) == "xelatex"

    def test_detect_lualatex_luacode(self, tmp_path):
        """检测 luacode 宏包 → lualatex。"""
        tex_file = tmp_path / "test.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\usepackage{luacode}"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )
        assert detect_engine_from_file(tex_file) == "lualatex"

    def test_detect_lualatex_directlua(self, tmp_path):
        r"""检测 \directlua 命令 → lualatex。"""
        tex_file = tmp_path / "test.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\directlua{print('hello')}"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )
        assert detect_engine_from_file(tex_file) == "lualatex"

    def test_detect_pdflatex_default(self, tmp_path):
        """无特殊宏包 → pdflatex。"""
        tex_file = tmp_path / "test.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\usepackage{amsmath}"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )
        assert detect_engine_from_file(tex_file) == "pdflatex"

    def test_ignore_comments(self, tmp_path):
        """忽略注释行中的宏包声明。"""
        tex_file = tmp_path / "test.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"% \usepackage{fontspec}"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )
        assert detect_engine_from_file(tex_file) == "pdflatex"

    def test_ignore_inline_comments(self, tmp_path):
        """忽略行内注释中的宏包声明。"""
        tex_file = tmp_path / "test.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\usepackage{amsmath} % \usepackage{fontspec}"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )
        assert detect_engine_from_file(tex_file) == "pdflatex"

    def test_file_not_found(self, tmp_path):
        """文件不存在 → 默认 pdflatex。"""
        tex_file = tmp_path / "nonexistent.tex"
        assert detect_engine_from_file(tex_file) == "pdflatex"

    def test_non_ascii_chinese_default_xelatex(self, tmp_path):
        """包含中文字符且无特定宏包 → xelatex（非 ASCII 排除 pdfLaTeX）。"""
        tex_file = tmp_path / "test.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\usepackage{amsmath}"
            "\n"
            r"\begin{document}"
            "\n"
            "这是一个中文文档"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )
        assert detect_engine_from_file(tex_file) == "xelatex"

    def test_non_ascii_japanese_default_xelatex(self, tmp_path):
        """包含日文字符且无特定宏包 → xelatex。"""
        tex_file = tmp_path / "test.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\begin{document}"
            "\n"
            "日本語の文書"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )
        assert detect_engine_from_file(tex_file) == "xelatex"

    def test_non_ascii_with_lualatex_pattern(self, tmp_path):
        """包含非 ASCII 字符 + LuaLaTeX 宏包 → lualatex。"""
        tex_file = tmp_path / "test.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\usepackage{luacode}"
            "\n"
            r"\begin{document}"
            "\n"
            "中文内容"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )
        assert detect_engine_from_file(tex_file) == "lualatex"

    def test_non_ascii_in_comment_ignored(self, tmp_path):
        """非 ASCII 字符仅在注释中 → pdflatex。"""
        tex_file = tmp_path / "test.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"% 这是注释中的中文"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )
        assert detect_engine_from_file(tex_file) == "pdflatex"


# ── inject_pdfx_for_pdfa 测试 ──────────────────────────────────────────────


class TestInjectPdfxForPdfa:
    """PDF/A 包注入测试。"""

    def test_inject_after_documentclass(self, tmp_path):
        """在 \\documentclass 之后注入 pdfx 包。"""
        src = tmp_path / "src.tex"
        dst = tmp_path / "dst.tex"
        src.write_text(
            r"\documentclass{article}"
            "\n"
            r"\usepackage{amsmath}"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )

        result = inject_pdfx_for_pdfa(src, dst)
        assert result is True

        content = dst.read_text(encoding="utf-8")
        assert r"\usepackage[a-2b]{pdfx}" in content

        # 验证注入位置在 \documentclass 之后
        lines = content.split("\n")
        docclass_idx = next(i for i, ln in enumerate(lines) if "\\documentclass" in ln)
        pdfx_idx = next(
            i for i, ln in enumerate(lines) if "\\usepackage[a-2b]{pdfx}" in ln
        )
        assert pdfx_idx == docclass_idx + 1

    def test_skip_if_pdfx_already_present(self, tmp_path):
        """已存在 pdfx 包时跳过注入。"""
        src = tmp_path / "src.tex"
        dst = tmp_path / "dst.tex"
        src.write_text(
            r"\documentclass{article}"
            "\n"
            r"\usepackage{pdfx}"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )

        result = inject_pdfx_for_pdfa(src, dst)
        assert result is True

        content = dst.read_text(encoding="utf-8")
        # 应该只有一处 pdfx
        assert content.count("pdfx") == 1

    def test_skip_if_pdfmanagement_present(self, tmp_path):
        """已存在 pdfmanagement-testphase 包时跳过注入。"""
        src = tmp_path / "src.tex"
        dst = tmp_path / "dst.tex"
        src.write_text(
            r"\documentclass{article}"
            "\n"
            r"\usepackage{pdfmanagement-testphase}"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )

        result = inject_pdfx_for_pdfa(src, dst)
        assert result is True

        content = dst.read_text(encoding="utf-8")
        assert "pdfmanagement-testphase" in content
        assert "pdfx" not in content

    def test_source_file_not_found(self, tmp_path):
        """源文件不存在 → 返回 False。"""
        src = tmp_path / "nonexistent.tex"
        dst = tmp_path / "dst.tex"
        result = inject_pdfx_for_pdfa(src, dst)
        assert result is False

    def test_no_documentclass(self, tmp_path):
        """无 \\documentclass → 返回 False，但仍复制文件。"""
        src = tmp_path / "src.tex"
        dst = tmp_path / "dst.tex"
        src.write_text(
            r"\usepackage{amsmath}"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )

        result = inject_pdfx_for_pdfa(src, dst)
        assert result is False
        assert dst.exists()


# ── run_tex_compile 引擎和 PDF/A 参数测试 ─────────────────────────────────


class TestRunTexCompileWithEngineAndPdfa:
    """run_tex_compile 引擎和 PDF/A 参数测试。"""

    def _make_process(self, returncode: int, stdout: bytes, stderr: bytes = b""):
        process = MagicMock()
        process.returncode = returncode
        # 同上：避免 MagicMock 缺省 pid=1 触发 killpg(1) 自杀
        process.pid = 2147483000

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
    async def test_engine_pdflatex(self, tmp_path):
        """指定 engine=pdflatex → 使用 -pdf 参数。"""
        tex_file = tmp_path / "main.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )
        process = self._make_process(0, b"ok")
        with patch("shutil.which", return_value="/usr/bin/texfot"):
            with patch(
                "asyncio.create_subprocess_exec", return_value=process
            ) as mock_exec:
                with patch("asyncio.wait_for", new=_fake_wait_for):
                    await run_tex_compile("main.tex", tmp_path, 60, engine="pdflatex")

        cmd = mock_exec.call_args[0]
        assert "-pdf" in cmd
        assert "-xelatex" not in cmd

    @pytest.mark.asyncio
    async def test_engine_xelatex(self, tmp_path):
        """指定 engine=xelatex → 使用 -xelatex 参数。"""
        tex_file = tmp_path / "main.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )
        process = self._make_process(0, b"ok")
        with patch("shutil.which", return_value="/usr/bin/texfot"):
            with patch(
                "asyncio.create_subprocess_exec", return_value=process
            ) as mock_exec:
                with patch("asyncio.wait_for", new=_fake_wait_for):
                    await run_tex_compile("main.tex", tmp_path, 60, engine="xelatex")

        cmd = mock_exec.call_args[0]
        assert "-xelatex" in cmd

    @pytest.mark.asyncio
    async def test_pdfa_subdir_output_copied_to_subdir(self, tmp_path):
        """PDF/A 模式下子目录主文件的 PDF 应回拷到原目录，而不是项目根目录。"""
        chapters = tmp_path / "chapters"
        chapters.mkdir()
        tex_file = chapters / "main.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )

        # run_tex_compile 内部会创建临时目录并在 finally 中清理；这里提前
        # 创建好该目录并放入模拟的 PDF/A 产物，最后断言被回拷到 chapters/。
        pdfa_tmp = Path(tempfile.mkdtemp(prefix="latexcellence_test_pdfa_"))
        (pdfa_tmp / "main_pdfa.pdf").write_bytes(b"%PDF-1.4 test")

        process = self._make_process(0, b"ok")
        try:
            with patch("tempfile.mkdtemp", return_value=str(pdfa_tmp)):
                with patch("shutil.which", return_value="/usr/bin/texfot"):
                    with patch("asyncio.create_subprocess_exec", return_value=process):
                        with patch("asyncio.wait_for", new=_fake_wait_for):
                            await run_tex_compile(
                                "chapters/main.tex",
                                tmp_path,
                                60,
                                engine="pdflatex",
                                pdfa=True,
                            )
        finally:
            # 正常情况下 run_tex_compile 的 finally 已清理临时目录；
            # 此处兜底，避免测试失败时残留。
            import shutil

            shutil.rmtree(pdfa_tmp, ignore_errors=True)

        assert (tmp_path / "chapters" / "main.pdf").exists()
        assert not (tmp_path / "main.pdf").exists()

    @pytest.mark.asyncio
    async def test_engine_lualatex(self, tmp_path):
        """指定 engine=lualatex → 使用 -lualatex 参数。"""
        tex_file = tmp_path / "main.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )
        process = self._make_process(0, b"ok")
        with patch("shutil.which", return_value="/usr/bin/texfot"):
            with patch(
                "asyncio.create_subprocess_exec", return_value=process
            ) as mock_exec:
                with patch("asyncio.wait_for", new=_fake_wait_for):
                    await run_tex_compile("main.tex", tmp_path, 60, engine="lualatex")

        cmd = mock_exec.call_args[0]
        assert "-lualatex" in cmd

    @pytest.mark.asyncio
    async def test_engine_auto_detects_xelatex(self, tmp_path):
        """engine=auto 且文件含 fontspec → 自动选择 xelatex。"""
        tex_file = tmp_path / "main.tex"
        tex_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\usepackage{fontspec}"
            "\n"
            r"\begin{document}"
            "\n"
            r"Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )
        process = self._make_process(0, b"ok")
        with patch("shutil.which", return_value="/usr/bin/texfot"):
            with patch(
                "asyncio.create_subprocess_exec", return_value=process
            ) as mock_exec:
                with patch("asyncio.wait_for", new=_fake_wait_for):
                    await run_tex_compile("main.tex", tmp_path, 60, engine="auto")

        cmd = mock_exec.call_args[0]
        assert "-xelatex" in cmd


class TestPreparePdfaWorkspace:
    """PDF/A 工作区准备：复制面黑名单与注入失败回退。"""

    def test_copies_sources_but_skips_git_and_build_artifacts(self, tmp_path):
        import shutil

        (tmp_path / "main.tex").write_text(r"\documentclass{article}", encoding="utf-8")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("x", encoding="utf-8")
        (tmp_path / "main.aux").write_text("stale", encoding="utf-8")
        (tmp_path / "main.synctex.gz").write_bytes(b"x")
        (tmp_path / "img").mkdir()
        (tmp_path / "img" / "fig.png").write_bytes(b"x")

        temp_dir, work_cwd, actual_main = _prepare_pdfa_workspace(tmp_path, "main.tex")
        try:
            assert work_cwd == temp_dir
            assert actual_main == "main_pdfa.tex"
            assert (temp_dir / "main_pdfa.tex").exists()
            assert (temp_dir / "img" / "fig.png").exists()
            assert not (temp_dir / ".git").exists()
            assert not (temp_dir / "main.aux").exists()
            assert not (temp_dir / "main.synctex.gz").exists()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_injection_failure_falls_back_to_normal_compile(self, tmp_path):
        (tmp_path / "doc.txt").write_text("no documentclass here", encoding="utf-8")

        temp_dir, work_cwd, actual_main = _prepare_pdfa_workspace(tmp_path, "doc.txt")

        assert temp_dir is None
        assert work_cwd == tmp_path
        assert actual_main == "doc.txt"
