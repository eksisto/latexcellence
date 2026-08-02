"""
模板生成自主 Agent 端到端测试（Mock LLM）。
"""

import asyncio
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
from backend.modules.template_gen.trace import append_trace, read_trace


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
    import uuid

    from backend.core.config import get_config

    async def run():
        cfg = get_config()
        output_dir = Path(cfg.features.template_gen.output_dir)
        if not output_dir.is_absolute():
            output_dir = Path.cwd() / output_dir
        session_id = uuid.uuid4().hex[:12]
        sandbox = output_dir / "agent" / session_id
        sandbox.mkdir(parents=True, exist_ok=True)
        events = []
        async for ev in service.generate(description, "zh", sandbox=sandbox, llm=llm):
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


class TestTraceRecording:
    """Agent 运行追踪（trace.jsonl 记录与读取）。"""

    @pytest.fixture(autouse=True)
    def _load_cfg(self, tmp_path, monkeypatch):
        load_config("config.json")
        cfg = get_config()
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

    def test_agent_writes_trace_events(self, tmp_path):
        """生成结束后沙箱内应有 trace.jsonl，含 run_start / llm / tool_result / run_end。"""
        svc = TemplateAgentService()
        llm = MagicMock()
        responses = [
            _make_response(
                content="Write the main file",
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
                        {"summary": "Completed", "main_file": "main.tex"},
                        "t2",
                    )
                ],
            ),
        ]
        llm.chat_with_tools = AsyncMock(side_effect=responses)

        events = _collect(svc, "a simple article", llm)
        done = events[-1]
        sandbox = Path(done["sandbox"])

        trace = read_trace(sandbox)
        types = [e["type"] for e in trace]
        assert "run_start" in types
        assert "llm" in types
        assert "tool_result" in types
        assert "run_end" in types
        # run_start 携带描述信息
        start = next(e for e in trace if e["type"] == "run_start")
        assert start["data"]["description"] == "a simple article"
        # tool_result 记录完整参数（含 write_file 内容）
        tr = next(e for e in trace if e["type"] == "tool_result")
        assert tr["data"]["tool"] == "write_file"
        assert "main.tex" in tr["data"]["args"]["file_path"]
        assert tr["data"]["ok"] is True

    def test_service_get_session_trace(self, tmp_path):
        """get_session_trace 返回会话追踪数据（meta + events）。"""
        from backend.modules.template_gen.service import TemplateGenService

        svc = TemplateGenService()
        llm = MagicMock()
        responses = [
            _make_response(
                content="Start",
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
                        "finish", {"summary": "ok", "main_file": "main.tex"}, "t2"
                    )
                ],
            ),
        ]
        llm.chat_with_tools = AsyncMock(side_effect=responses)

        async def run():
            events = []
            async for ev in svc.generate("meta test", "zh", llm=llm):
                events.append(ev)
            return events

        events = asyncio.run(run())
        session_id = events[-1]["session_id"]

        data = svc.get_session_trace(session_id)
        assert data["session_id"] == session_id
        assert data["meta"]["description"] == "meta test"
        assert data["meta"]["max_rounds"] == 8
        assert any(e["type"] == "run_start" for e in data["events"])


class TestTraceHelpers:
    """trace.py 基础读写。"""

    def test_append_and_read_roundtrip(self, tmp_path):
        sandbox = tmp_path / "session"
        sandbox.mkdir()
        ev1 = {
            "ts": "2026-01-01T00:00:00.000",
            "run_id": "r1",
            "type": "run_start",
            "data": {"x": 1},
        }
        ev2 = {
            "ts": "2026-01-01T00:00:00.001",
            "run_id": "r1",
            "type": "llm",
            "data": {"content": "hi"},
        }
        asyncio.run(append_trace(sandbox, ev1))
        asyncio.run(append_trace(sandbox, ev2))
        assert read_trace(sandbox) == [ev1, ev2]

    def test_read_trace_missing_returns_empty(self, tmp_path):
        assert read_trace(tmp_path / "nonexistent") == []

    def test_read_trace_skips_corrupt_lines(self, tmp_path):
        sandbox = tmp_path / "session"
        sandbox.mkdir()
        (sandbox / "trace.jsonl").write_text(
            '{"type": "ok"}\nnot-json\n{"type": "ok2"}\n', encoding="utf-8"
        )
        types = [e["type"] for e in read_trace(sandbox)]
        assert types == ["ok", "ok2"]


class TestGenServiceOrchestration:
    """TemplateGenService（service.py）编排：创建沙箱并驱动 Agent。"""

    @pytest.mark.asyncio
    async def test_generate_creates_sandbox_and_yields_done(
        self, tmp_path, monkeypatch
    ):
        from backend.core.config import get_config, load_config
        from backend.modules.template_gen.service import TemplateGenService

        load_config("config.json")
        cfg = get_config()
        monkeypatch.setattr(
            cfg.features.template_gen, "output_dir", str(tmp_path / "templates")
        )

        svc = TemplateGenService()
        llm = MagicMock()
        llm.chat_with_tools = AsyncMock(
            side_effect=[
                _make_response(
                    content="Start",
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
                    tool_calls=[
                        _make_tool_call(
                            "finish",
                            {"summary": "ok", "main_file": "main.tex"},
                            "t2",
                        )
                    ]
                ),
            ]
        )

        events = []
        async for ev in svc.generate("a simple article", "zh", llm=llm):
            events.append(ev)

        assert events[-1]["type"] == "done"
        done = events[-1]
        sandbox = Path(done["sandbox"])
        # 沙箱应创建在 output_dir/agent/<session_id>/ 下
        assert sandbox.is_relative_to(tmp_path / "templates" / "agent")
        assert (sandbox / "main.tex").exists()
        assert done["session_id"] == sandbox.name
