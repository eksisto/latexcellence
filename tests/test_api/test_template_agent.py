"""
模板生成自主 Agent 端到端测试（Mock LLM）。
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.config import load_config, get_config
from backend.modules.template_gen.agent import (
    TemplateAgentService,
    _ToolExecutor,
    _extract_errors,
    _format_error_analysis,
    _trim_log,
)


def _make_tool_call(name, arguments, cid="tc-1"):
    return SimpleNamespace(
        id=cid,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments, ensure_ascii=False),
        ),
    )


def _make_response(content=None, tool_calls=None):
    msg = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
    )
    msg.model_dump = lambda: {
        "role": "assistant",
        "content": content,
        "tool_calls": (
            [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in (tool_calls or [])
            ]
            if tool_calls
            else None
        ),
    }
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _collect(service, description, llm):
    import asyncio

    async def run():
        events = []
        async for ev in service.generate(description, "zh", llm=llm):
            events.append(ev)
        return events

    return asyncio.run(run())


class TestAgentLoop:
    """Agent 循环端到端（Mock chat_with_tools）。"""

    @pytest.fixture(autouse=True)
    def _load_cfg(self, tmp_path, monkeypatch):
        load_config("config.json")
        cfg = get_config()
        # 重定向输出目录到临时目录，避免污染真实 templates/
        monkeypatch.setattr(
            cfg.features.template_gen,
            "output_dir",
            str(tmp_path / "templates"),
        )
        monkeypatch.setattr(cfg.features.template_gen, "agent_max_rounds", 8)
        monkeypatch.setattr(cfg.features.template_gen, "agent_max_files", 10)
        monkeypatch.setattr(cfg.features.template_gen, "agent_max_file_size_kb", 64)
        monkeypatch.setattr(cfg.features.template_gen, "agent_compile_enabled", True)
        monkeypatch.setattr(cfg.features.template_gen, "agent_compile_timeout", 30)

    def test_write_and_finish(self, tmp_path):
        svc = TemplateAgentService()
        llm = MagicMock()
        responses = [
            _make_response(
                content="Write the main file first",
                tool_calls=[
                    _make_tool_call(
                        "write_file",
                        {
                            "file_path": "main.tex",
                            "content": r"\documentclass{article}\begin{document}hi\end{document}",
                        },
                        "t1",
                    )
                ],
            ),
            _make_response(
                content="Finish",
                tool_calls=[
                    _make_tool_call(
                        "finish",
                        {
                            "summary": "Completed",
                            "main_file": "main.tex",
                        },
                        "t2",
                    )
                ],
            ),
        ]
        llm.chat_with_tools = AsyncMock(side_effect=responses)

        events = _collect(svc, "a simple article", llm)

        types = [e["type"] for e in events]
        assert "tool_call" in types
        assert "tool_result" in types
        assert events[-1]["type"] == "done"
        done = events[-1]
        assert any(f["path"] == "main.tex" for f in done["files"])
        # 沙箱内确实写入了文件
        sandbox = Path(done["sandbox"])
        assert (sandbox / "main.tex").exists()
        assert (
            (sandbox / "main.tex")
            .read_text(encoding="utf-8")
            .startswith(r"\documentclass")
        )

    def test_path_traversal_rejected(self, tmp_path):
        svc = TemplateAgentService()
        llm = MagicMock()
        responses = [
            _make_response(
                tool_calls=[
                    _make_tool_call(
                        "write_file",
                        {
                            "file_path": "../escape.tex",
                            "content": "x",
                        },
                        "e1",
                    ),
                    _make_tool_call(
                        "write_file",
                        {
                            "file_path": "okay.tex",
                            "content": "ok",
                        },
                        "e2",
                    ),
                    _make_tool_call("finish", {"summary": "ok"}, "e3"),
                ]
            ),
        ]
        llm.chat_with_tools = AsyncMock(side_effect=responses)
        # 一次响应里三个 tool_call，循环结束后 finish 触发跳出
        events = _collect(svc, "traversal test", llm)
        results = [e for e in events if e["type"] == "tool_result"]
        # 第一个 write 被拒，第二个成功
        assert results[0]["ok"] is False
        assert results[1]["ok"] is True

    def test_finish_terminates_early(self, tmp_path):
        svc = TemplateAgentService()
        llm = MagicMock()
        responses = [
            _make_response(
                tool_calls=[
                    _make_tool_call("finish", {"summary": "stop early"}, "f1"),
                ]
            ),
        ]
        llm.chat_with_tools = AsyncMock(side_effect=responses)
        events = _collect(svc, "early stop", llm)
        # 只调用一次 LLM（finish 后立即终止），不消耗 max_rounds
        assert llm.chat_with_tools.call_count == 1
        assert events[-1]["type"] == "done"

    def test_agent_disabled_yields_error(self, tmp_path, monkeypatch):
        cfg = get_config()
        monkeypatch.setattr(cfg.features.template_gen, "agent_enabled", False)
        svc = TemplateAgentService()
        llm = MagicMock()
        events = _collect(svc, "x", llm)
        assert events[0]["type"] == "error"
        assert "disabled" in events[0]["message"].lower()


class TestSandboxPath:
    """沙箱路径校验。"""

    def test_rejects_absolute(self, tmp_path):
        with pytest.raises(ValueError):
            TemplateAgentService._resolve_sandbox_path(tmp_path, "/etc/passwd")

    def test_rejects_parent(self, tmp_path):
        with pytest.raises(ValueError):
            TemplateAgentService._resolve_sandbox_path(tmp_path, "../x.tex")

    def test_rejects_bad_suffix(self, tmp_path):
        with pytest.raises(ValueError):
            TemplateAgentService._resolve_sandbox_path(tmp_path, "a.exe")

    def test_accepts_subdir(self, tmp_path):
        resolved = TemplateAgentService._resolve_sandbox_path(
            tmp_path, "chapters/intro.tex"
        )
        assert resolved.is_relative_to(tmp_path.resolve())


class TestTrimLog:
    def test_success_keeps_tail(self):
        log = "x\n" * 3000 + "FINAL LINE"
        out = _trim_log(log, True)
        assert out.startswith("Compilation succeeded.")
        assert out.endswith("FINAL LINE")

    def test_failure_finds_error(self):
        log = "noise\n!" + " Something bad" + "\nmore\n" * 100
        out = _trim_log(log, False)
        assert out.startswith("Compilation FAILED.")
        assert "Something bad" in out


class TestToolExecutorCompile:
    """编译工具在 latexmk 不可用时优雅降级。"""

    def test_main_file_missing(self, tmp_path):
        from types import SimpleNamespace

        cfg = get_config()
        cfg.features.template_gen.output_dir = str(tmp_path)
        executor = _ToolExecutor(tmp_path, cfg, MagicMock())
        import asyncio

        text, ev = asyncio.run(executor._compile_latex({"main_file": "nope.tex"}))
        assert ev["ok"] is False
        assert "not found" in ev["summary"].lower()


class TestErrorAnalysis:
    """编译日志结构化错误解析。"""

    def test_extract_undefined_control_sequence(self):
        log = (
            "Latexmk: applying rule...\n"
            "(./chapters/intro.tex\n"
            "! Undefined control sequence.\n"
            "l.45 \\unknowncommand\n"
            "                        \n"
            ")\n"
        )
        errors = _extract_errors(log)
        assert len(errors) == 1
        assert errors[0]["type"] == "Undefined control sequence."
        assert errors[0]["file"] == "chapters/intro.tex"
        assert errors[0]["line"] == 45
        assert errors[0]["detail"] == "\\unknowncommand"

    def test_extract_multiple_errors(self):
        log = (
            "(./main.tex\n"
            "! Undefined control sequence.\n"
            "l.10 \\bad\n"
            "! Missing $ inserted.\n"
            "<inserted text>\n"
            "$\n"
            "l.20 ...text\n"
            "! LaTeX Error: File 'nope.sty' not found.\n"
            ")\n"
        )
        errors = _extract_errors(log)
        assert len(errors) == 3
        assert errors[0]["line"] == 10
        assert errors[2]["file"] == "main.tex"

    def test_extract_no_errors(self):
        log = "Output written on main.pdf\n"
        assert _extract_errors(log) == []

    def test_format_analysis_lists_first_error(self):
        errors = [
            {
                "type": "Undefined control sequence.",
                "file": "main.tex",
                "line": 45,
                "detail": "\\bad",
            },
            {"type": "Other.", "file": None, "line": None, "detail": ""},
        ]
        text = _format_error_analysis(errors)
        assert "Detected 2 LaTeX error(s)" in text
        assert "FIRST error" in text
        assert "Error 1:" in text
        assert "main.tex" in text
        assert "l.45..." not in text  # line 用数字，不是 l. 前缀
        assert "45" in text
        assert "\\bad" in text

    def test_format_analysis_no_errors_hint(self):
        text = _format_error_analysis([])
        assert "No '! '" in text


class TestCompileFailureWarning:
    """编译失败但 Agent 仍调用 finish 时，应推送 warning 事件。"""

    def test_warning_emitted_when_finish_after_failed_compile(
        self, tmp_path, monkeypatch
    ):
        """LLM：write → compile(失败) → finish，应 emit compile(success=False)
        和 warning 事件，仍以 done 收尾。"""
        load_config("config.json")
        cfg = get_config()
        monkeypatch.setattr(
            cfg.features.template_gen, "output_dir", str(tmp_path / "templates")
        )
        monkeypatch.setattr(cfg.features.template_gen, "agent_max_rounds", 8)
        monkeypatch.setattr(cfg.features.template_gen, "agent_max_files", 10)
        monkeypatch.setattr(cfg.features.template_gen, "agent_max_file_size_kb", 64)
        monkeypatch.setattr(cfg.features.template_gen, "agent_compile_enabled", True)
        monkeypatch.setattr(cfg.features.template_gen, "agent_compile_timeout", 30)

        svc = TemplateAgentService()
        llm = MagicMock()
        responses = [
            _make_response(
                tool_calls=[
                    _make_tool_call(
                        "write_file",
                        {
                            "file_path": "main.tex",
                            "content": r"\documentclass{article}\begin{doc}\bad\end{doc}",
                        },
                        "w1",
                    ),
                ]
            ),
            _make_response(
                tool_calls=[
                    _make_tool_call("compile_latex", {"main_file": "main.tex"}, "c1"),
                ]
            ),
            _make_response(
                tool_calls=[
                    _make_tool_call(
                        "finish", {"summary": "done", "main_file": "main.tex"}, "f1"
                    ),
                ]
            ),
        ]
        llm.chat_with_tools = AsyncMock(side_effect=responses)

        # 直接桩掉 _compile_latex，避免依赖真实 latexmk，确保稳定且快速
        async def _fake_compile(self_exec, args):
            errors = [
                {
                    "type": "Undefined control sequence.",
                    "file": "main.tex",
                    "line": 1,
                    "detail": "\\bad",
                },
            ]
            analysis = _format_error_analysis(errors)
            llm_text = analysis + "\n\n--- Raw log ---\nCompilation FAILED."
            event = {
                "tool": "compile_latex",
                "ok": True,
                "success": False,
                "log": "Compilation FAILED.",
                "errors": errors,
                "summary": "Compilation failed (1 error)",
            }
            return llm_text, event

        import backend.modules.template_gen.agent as agent_mod

        monkeypatch.setattr(_ToolExecutor, "_compile_latex", _fake_compile)

        events = _collect(svc, "test warning", llm)
        types = [e["type"] for e in events]
        # 应出现 compile 失败事件 + warning 事件
        compile_ev = next(e for e in events if e["type"] == "compile")
        assert compile_ev["success"] is False
        assert compile_ev.get("errors")  # 结构化错误列表
        assert "warning" in types
        warning_ev = next(e for e in events if e["type"] == "warning")
        assert "compilation" in warning_ev["message"].lower()
        # 最后仍是 done（warning 不阻断 finish）
        assert events[-1]["type"] == "done"
