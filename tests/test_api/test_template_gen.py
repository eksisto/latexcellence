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
    _attach_source_context,
    _check_body_style_commands,
    _check_cjk_support,
    _check_class_option_order,
    _check_provides_date,
    _check_setter_accessors,
    _compact_messages,
    _extract_errors,
    _format_error_analysis,
    _trim_log,
)
from backend.modules.template_gen.agent import (
    executor as agent_executor_mod,
    service as agent_service_mod,
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


def _sandbox_from_done(done, tmp_path):
    """done 事件不再携带 sandbox 绝对路径（多用户路径约束），
    测试按 output_dir + session_id 重建沙箱目录。"""
    return Path(tmp_path) / "templates" / done["session_id"]


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
        sandbox = output_dir / session_id
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
        # 重定向输出目录到临时目录，避免污染真实 data/templates/
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
        sandbox = _sandbox_from_done(done, tmp_path)
        assert (sandbox / "main.tex").exists()
        assert (
            (sandbox / "main.tex")
            .read_text(encoding="utf-8")
            .startswith(r"\documentclass")
        )

    def test_phase_system_injected_at_phase_transitions(self, tmp_path, monkeypatch):
        """阶段 3：planning 在开始时注入，compile/validation 阶段块在首次
        编译后注入，且不会在编译前出现在上下文中。"""
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

        svc = TemplateAgentService()
        llm = MagicMock()
        responses = [
            _make_response(
                tool_calls=[
                    _make_tool_call(
                        "write_file",
                        {
                            "file_path": "main.tex",
                            "content": r"\documentclass{article}\begin{document}hi\end{document}",
                        },
                        "w1",
                    )
                ]
            ),
            _make_response(
                tool_calls=[
                    _make_tool_call("compile_latex", {"main_file": "main.tex"}, "c1"),
                    _make_tool_call(
                        "finish",
                        {"summary": "premature", "main_file": "main.tex"},
                        "f0",
                    ),
                ]
            ),
            _make_response(
                tool_calls=[
                    _make_tool_call(
                        "finish",
                        {"summary": "ok", "main_file": "main.tex"},
                        "f1",
                    )
                ]
            ),
        ]
        captured: list[list[dict]] = []

        async def _chat(messages, **kwargs):
            # chat_with_tools 的 messages 参数是同一可变列表，后续阶段注入会
            # 原地修改；这里在每次调用时深拷贝快照，才能断言阶段块注入时机。
            captured.append([dict(m) for m in messages])
            return responses[len(captured) - 1]

        llm.chat_with_tools = AsyncMock(side_effect=_chat)

        async def _fake_compile(self_exec, args):
            return "Compilation succeeded.", {
                "tool": "compile_latex",
                "ok": True,
                "success": True,
                "log": "no errors",
                "errors": [],
                "summary": "Compilation succeeded",
            }

        monkeypatch.setattr(_ToolExecutor, "_compile_latex", _fake_compile)

        _collect(svc, "a simple article", llm)

        assert len(captured) == 3
        first_msgs = captured[0]
        assert first_msgs[0]["role"] == "system"
        assert "WORKFLOW" in first_msgs[0]["content"]
        assert first_msgs[1]["role"] == "user"
        assert first_msgs[2]["role"] == "system"
        assert "<planning_guidance>" in first_msgs[2]["content"]
        # 编译前不注入编译/验证阶段块
        second_msgs = captured[1]
        assert not any(
            "COMPILATION ERROR RECOVERY PROTOCOL" in (m.get("content") or "")
            for m in second_msgs
        )
        # 首次编译成功后注入编译协议与质量门禁
        third_msgs = captured[2]
        assert any(
            "COMPILATION ERROR RECOVERY PROTOCOL" in (m.get("content") or "")
            for m in third_msgs
        )
        assert any(
            "VISUAL QUALITY GATE" in (m.get("content") or "") for m in third_msgs
        )
        assert any(
            m.get("role") == "tool" and "Finish deferred" in (m.get("content") or "")
            for m in third_msgs
        )

    def test_planning_prompt_injects_configured_font_whitelist(self, monkeypatch):
        """planning 阶段块按配置注入字体白名单；空列表回退默认清单。"""
        from backend.core.config import FontWhitelistEntry, default_font_whitelist

        load_config("config.json")
        cfg = get_config()

        svc = TemplateAgentService()
        llm = MagicMock()
        captured: list[list[dict]] = []

        async def _chat(messages, **kwargs):
            captured.append([dict(m) for m in messages])
            return _make_response(
                tool_calls=[_make_tool_call("finish", {"summary": "ok"}, "f1")]
            )

        llm.chat_with_tools = AsyncMock(side_effect=_chat)

        monkeypatch.setattr(
            cfg.features.template_gen,
            "font_whitelist",
            [
                FontWhitelistEntry(
                    name="CampusSerif",
                    aliases=["校园宋体"],
                    note="campus licensed font",
                )
            ],
        )

        _collect(svc, "whitelist test", llm)

        planning = captured[0][2]["content"]
        assert "FONT WHITELIST" in planning
        assert "CampusSerif" in planning
        assert "校园宋体" in planning
        assert "campus licensed font" in planning

        # 空列表 → 服务层回退默认清单
        monkeypatch.setattr(cfg.features.template_gen, "font_whitelist", [])
        captured.clear()
        _collect(svc, "whitelist fallback test", llm)
        planning_default = captured[0][2]["content"]
        assert "FandolSong" in planning_default
        assert default_font_whitelist()

    def test_context_compaction_keeps_recent_rounds(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "req"},
            {"role": "system", "content": "planning"},
            {"role": "system", "content": "validation"},
        ]
        for i in range(30):
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"t{i}",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": '{"content":"' + "x" * 5000 + '"}',
                            },
                        }
                    ],
                }
            )
            messages.append({"role": "tool", "tool_call_id": f"t{i}", "content": "ok"})

        compacted = _compact_messages(messages)

        assert compacted[0] == messages[0]
        assert compacted[1] == messages[1]
        # 阶段 3 注入的 system 阶段块在压缩后仍然保留
        assert any(
            m.get("role") == "system" and m.get("content") == "planning"
            for m in compacted
        )
        assert any(
            m.get("role") == "system" and m.get("content") == "validation"
            for m in compacted
        )
        assert any(
            m.get("role") == "user" and "compacted" in m.get("content", "")
            for m in compacted
        )

    def test_context_compaction_does_not_duplicate_recent_phase_blocks(self):
        messages = [
            {"role": "system", "content": "core"},
            {"role": "user", "content": "request"},
        ]
        for i in range(15):
            messages.extend(
                [
                    {"role": "assistant", "content": f"round {i}"},
                    {"role": "tool", "content": f"result {i}"},
                ]
            )
            if i == 10:
                messages.append({"role": "system", "content": "validation"})

        compacted = _compact_messages(messages, keep_rounds=5)

        assert [m for m in compacted if m.get("content") == "validation"] == [
            {"role": "system", "content": "validation"}
        ]
        assert compacted[-1] == messages[-1]
        assert len(compacted) < len(messages)

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

    def test_no_tool_call_is_nudged_not_terminated(self, tmp_path):
        """LLM 首轮只输出规划文本、不调用工具时，循环应引导其继续行动，
        而不是直接当作结束（规划瘫痪防护）。"""
        svc = TemplateAgentService()
        llm = MagicMock()
        responses = [
            _make_response(
                content="Let me think about the file structure first",
                tool_calls=None,
            ),
            _make_response(
                content="Now I will write the main file",
                tool_calls=[
                    _make_tool_call(
                        "write_file",
                        {
                            "file_path": "main.tex",
                            "content": r"\documentclass{article}\begin{document}hi\end{document}",
                        },
                        "w1",
                    )
                ],
            ),
            _make_response(
                content="Finished",
                tool_calls=[
                    _make_tool_call(
                        "finish",
                        {"summary": "ok", "main_file": "main.tex"},
                        "f1",
                    )
                ],
            ),
        ]
        llm.chat_with_tools = AsyncMock(side_effect=responses)

        events = _collect(svc, "nudge test", llm)
        # 3 次 LLM 调用：规划(无工具) → 写入 → finish
        assert llm.chat_with_tools.call_count == 3
        # 引导文案注入第二轮请求的 LLM 历史（作为 user 消息）
        calls = llm.chat_with_tools.await_args_list
        second_msgs = calls[1].kwargs["messages"]
        assert any(
            m["role"] == "user" and "stalled round" in m["content"] for m in second_msgs
        )
        # 引导文案不推送到前端：plan 事件流中不出现该文本
        plans = " ".join(e.get("content", "") for e in events if e["type"] == "plan")
        assert "stalled round" not in plans
        done = events[-1]
        assert done["type"] == "done"
        assert any(f["path"] == "main.tex" for f in done["files"])

    def test_repeated_file_rejection_injects_simplify_guidance(self, tmp_path):
        """同一文件连续被拒 5 次（即使内容哈希近似）应注入简化重写干预。"""
        svc = TemplateAgentService()
        llm = MagicMock()
        bad_content = r"\newcommand{\cvsection}[1]{\textbf{hello"  # 缺右花括号
        responses = []
        for i in range(5):
            responses.append(
                _make_response(
                    tool_calls=[
                        _make_tool_call(
                            "write_file",
                            {
                                "file_path": "resume.cls",
                                "content": bad_content,
                            },
                            f"w{i}",
                        )
                    ]
                )
            )
        responses.append(
            _make_response(
                tool_calls=[
                    _make_tool_call("finish", {"summary": "done"}, "f1"),
                ]
            )
        )
        llm.chat_with_tools = AsyncMock(side_effect=responses)

        events = _collect(svc, "resume test", llm)
        done = events[-1]
        assert done["type"] == "done"
        trace_text = (_sandbox_from_done(done, tmp_path) / "trace.jsonl").read_text(
            encoding="utf-8"
        )
        assert "repeated_file_rejection" in trace_text
        assert "MUCH SIMPLER" in trace_text


class TestFontWhitelistConfig:
    """字体白名单配置模型：默认值、覆盖与回退。"""

    def test_default_whitelist_matches_tex_image(self):
        from backend.core.config import default_font_whitelist

        names = {e.name for e in default_font_whitelist()}
        assert {
            "Latin Modern",
            "TeX Gyre Termes",
            "TeX Gyre Heros",
            "FandolSong",
            "FandolHei",
            "Noto Sans CJK SC",
            "Noto Serif CJK SC",
        } <= names
        termes = next(
            e for e in default_font_whitelist() if e.name == "TeX Gyre Termes"
        )
        assert "Times New Roman" in termes.aliases

    def test_config_override_from_dict(self):
        from backend.core.config import TemplateGenConfig

        cfg = TemplateGenConfig.model_validate(
            {
                "font_whitelist": [
                    {
                        "name": "CampusSerif",
                        "aliases": ["Times New Roman"],
                        "note": "licensed",
                    },
                    {"name": "LocalHei"},
                ]
            }
        )
        assert [e.name for e in cfg.font_whitelist] == ["CampusSerif", "LocalHei"]
        assert cfg.font_whitelist[1].aliases == []
        assert cfg.font_whitelist[1].note == ""

    def test_appconfig_defaults_nonempty(self):
        from backend.core.config import AppConfig

        assert AppConfig().features.template_gen.font_whitelist


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

    def test_read_allows_generated_log(self, tmp_path):
        """read_file 允许读取编译生成文件（如 main.log），写入仍拒绝。"""
        resolved = TemplateAgentService._resolve_sandbox_path(
            tmp_path, "main.log", for_read=True
        )
        assert resolved.name == "main.log"
        with pytest.raises(ValueError):
            TemplateAgentService._resolve_sandbox_path(tmp_path, "main.log")


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

    def test_fallback_extracts_errors_from_main_log(self, tmp_path, monkeypatch):
        """latexmk 输出中没有 `!` 行时，应兜底从 <main>.log 提取结构化错误。"""
        from types import SimpleNamespace

        cfg = get_config()
        cfg.features.template_gen.output_dir = str(tmp_path)
        cfg.features.template_gen.agent_compile_timeout = 30
        (tmp_path / "main.tex").write_text(
            r"\documentclass{article}\begin{document}\bad\end{document}",
            encoding="utf-8",
        )
        (tmp_path / "main.log").write_text(
            "(./main.tex\n! Undefined control sequence.\n" "l.1 \\bad\n)\n",
            encoding="utf-8",
        )

        fake = SimpleNamespace(
            success=False,
            raw_output=("Latexmk: Errors, so I did not complete making targets"),
            filtered_output="",
            timed_out=False,
            used_texfot=False,
        )

        async def _fake_run(*args, **kwargs):
            return fake

        monkeypatch.setattr(agent_executor_mod, "run_tex_compile", _fake_run)
        executor = _ToolExecutor(tmp_path, cfg, MagicMock())

        text, ev = asyncio.run(executor._compile_latex({"main_file": "main.tex"}))
        assert ev["ok"] is True
        assert ev["success"] is False
        assert ev["errors"][0]["type"] == "Undefined control sequence."
        assert "main.log" in text

    def test_fallback_appends_blg_tail_for_bibliography_failure(
        self, tmp_path, monkeypatch
    ):
        """编译输出与 .log 都无 `!` 行、但 bibtex 失败时，应追加 .blg 尾部。"""
        from types import SimpleNamespace

        cfg = get_config()
        cfg.features.template_gen.output_dir = str(tmp_path)
        cfg.features.template_gen.agent_compile_timeout = 30
        (tmp_path / "main.tex").write_text(
            r"\documentclass{article}\begin{document}x\end{document}",
            encoding="utf-8",
        )
        (tmp_path / "main.log").write_text(
            "Latexmk: Missing bbl file 'main.bbl'.\n", encoding="utf-8"
        )
        (tmp_path / "main.blg").write_text(
            "The style file: gbt7714-2005-numerical.bst\n"
            "`booktitle' is a missing field for entry lowe2004sift\n",
            encoding="utf-8",
        )

        fake = SimpleNamespace(
            success=False,
            raw_output="Latexmk: Errors, so I did not complete making targets",
            filtered_output="",
            timed_out=False,
            used_texfot=False,
        )

        async def _fake_run(*args, **kwargs):
            return fake

        monkeypatch.setattr(agent_executor_mod, "run_tex_compile", _fake_run)
        executor = _ToolExecutor(tmp_path, cfg, MagicMock())

        text, ev = asyncio.run(executor._compile_latex({"main_file": "main.tex"}))
        assert ev["success"] is False
        assert "main.blg" in text
        assert "missing field" in text
        assert "\\bibliographystyle" in text

    def test_quality_gate_uses_final_log_not_stale_raw_output(
        self, tmp_path, monkeypatch
    ):
        """后续 TeX pass 已修复的首轮引用 warning 不应阻断成功。"""
        cfg = get_config()
        cfg.features.template_gen.output_dir = str(tmp_path)
        (tmp_path / "main.tex").write_text(
            r"\documentclass{article}\begin{document}x\end{document}",
            encoding="utf-8",
        )
        # latexmk 的 stdout 可能保留早期 pass 的 undefined citation；主
        # .log 是最后一次 pass，已无该 warning，但仍可包含非致命排版提示。
        (tmp_path / "main.log").write_text(
            "This is the final TeX pass.\n"
            "Output written on main.xdv (1 page).\n"
            "Overfull \\hbox (2.0pt too wide) in paragraph at lines 1--2\n",
            encoding="utf-8",
        )
        fake = SimpleNamespace(
            success=True,
            raw_output=(
                "Package natbib Warning: There were undefined citations.\n"
                "LaTeX Warning: Reference `old' undefined on input line 1.\n"
                "main.xdv -> main.pdf"
            ),
            filtered_output="",
            timed_out=False,
            used_texfot=False,
        )

        async def _fake_run(*args, **kwargs):
            return fake

        monkeypatch.setattr(agent_executor_mod, "run_tex_compile", _fake_run)
        executor = _ToolExecutor(tmp_path, cfg, MagicMock())

        text, event = asyncio.run(executor._compile_latex({"main_file": "main.tex"}))

        assert event["success"] is True
        assert "undefined" not in text.lower()
        assert event["quality_warnings"]


class TestToolExecutorWriteFile:
    """write_file 写盘前的 LaTeX 内容校验。"""

    def _make_executor(self, tmp_path):
        cfg = get_config()
        cfg.features.template_gen.output_dir = str(tmp_path)
        return _ToolExecutor(tmp_path, cfg, MagicMock())

    def test_rejects_corrupt_content(self, tmp_path):
        """\\rho 被 JSON 转义损坏成回车 + ho → 拒绝写盘。"""
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {"file_path": "main.tex", "content": "R^" + "\r" + "ho"}
            )
        )

        assert ev["ok"] is False
        assert "rejected" in text.lower()
        assert not (tmp_path / "main.tex").exists()

    def test_accepts_escaped_latex(self, tmp_path):
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "main.tex",
                    "content": (
                        r"\documentclass{article}" r"\begin{document}hi\end{document}"
                    ),
                }
            )
        )

        assert ev["ok"] is True
        content = (tmp_path / "main.tex").read_text(encoding="utf-8")
        assert r"\begin{document}" in content

    def test_rejects_invalid_provides_date(self, tmp_path):
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "cv.cls",
                    "content": (
                        r"\NeedsTeXFormat{LaTeX2e}"
                        r"\ProvidesClass{cv}[2024/01 Master Thesis Template]"
                        r"\LoadClass{article}"
                    ),
                }
            )
        )

        assert ev["ok"] is False
        assert "YYYY/MM/DD" in text
        assert "Missing = inserted" in text
        assert not (tmp_path / "cv.cls").exists()

    def test_accepts_full_provides_date(self, tmp_path):
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "cv.cls",
                    "content": (
                        r"\NeedsTeXFormat{LaTeX2e}"
                        r"\ProvidesClass{cv}[2024/01/01 Master Thesis Template]"
                        r"\LoadClass{article}"
                    ),
                }
            )
        )

        assert ev["ok"] is True
        assert (tmp_path / "cv.cls").exists()

    def test_provides_date_helper_accepts_no_optional_arg(self):
        assert _check_provides_date(r"\ProvidesPackage{theme}") is None

    def test_accepts_private_def_in_public_setter(self, tmp_path):
        """.cls/.sty 读取时 `@` 是字母，setter 内定义私有名是合法 LaTeX。"""
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "thesis.cls",
                    "content": (
                        r"\NeedsTeXFormat{LaTeX2e}"
                        r"\ProvidesClass{thesis}[2024/01/01 Thesis]"
                        r"\newcommand{\university}[1]{\def\thesis@university{#1}}"
                    ),
                }
            )
        )

        assert ev["ok"] is True
        assert (tmp_path / "thesis.cls").exists()

    def test_accepts_public_setter_without_at_assignment(self, tmp_path):
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "thesis.cls",
                    "content": (
                        r"\NeedsTeXFormat{LaTeX2e}"
                        r"\ProvidesClass{thesis}[2024/01/01 Thesis]"
                        r"\newcommand{\universityvalue}{}"
                        r"\newcommand{\university}[1]"
                        r"{\renewcommand{\universityvalue}{#1}}"
                    ),
                }
            )
        )

        assert ev["ok"] is True
        assert (tmp_path / "thesis.cls").exists()

    def test_rejects_style_definition_in_body_file(self, tmp_path):
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "main.tex",
                    "content": (
                        r"\documentclass{article}"
                        r"\renewcommand{\maketitlepage}{hello}"
                        r"\begin{document}hi\end{document}"
                    ),
                }
            )
        )

        assert ev["ok"] is False
        assert "style command" in text
        assert not (tmp_path / "main.tex").exists()

    def test_rejects_cjk_without_support(self, tmp_path):
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "main.tex",
                    "content": (
                        r"\documentclass{beamer}"
                        r"\begin{document}中文内容\end{document}"
                    ),
                }
            )
        )

        assert ev["ok"] is False
        assert "ctex" in text
        assert not (tmp_path / "main.tex").exists()

    def test_accepts_cjk_with_ctex(self, tmp_path):
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "main.tex",
                    "content": (
                        r"\documentclass{ctexart}"
                        r"\begin{document}中文内容\end{document}"
                    ),
                }
            )
        )

        assert ev["ok"] is True
        assert (tmp_path / "main.tex").exists()

    def test_accepts_article_cjk_without_inline_package(self, tmp_path):
        """article 的 CJK 支持可能来自本地 .sty，正文文件不应被误伤。"""
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "main.tex",
                    "content": (
                        r"\documentclass{article}"
                        r"\usepackage{conference-paper}"
                        r"\begin{document}中文内容\end{document}"
                    ),
                }
            )
        )

        assert ev["ok"] is True

    def test_accepts_beamer_cjk_with_local_style_support(self, tmp_path):
        """beamer 的 CJK 支持可能由本地样式提供，正文文件不应被误伤。"""
        executor = self._make_executor(tmp_path)
        (tmp_path / "mytheme.sty").write_text(
            r"\RequirePackage{ctex}", encoding="utf-8"
        )

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "main.tex",
                    "content": (
                        r"\documentclass{beamer}"
                        r"\usepackage{mytheme}"
                        r"\begin{document}中文内容\end{document}"
                    ),
                }
            )
        )

        assert ev["ok"] is True
        assert (tmp_path / "main.tex").exists()

    def test_rejects_beamer_cjk_with_non_cjk_local_style(self, tmp_path):
        """本地样式确实存在但没有加载 ctex/xeCJK 时仍应拒绝，避免漏判。"""
        executor = self._make_executor(tmp_path)
        (tmp_path / "mytheme.sty").write_text(
            r"\ProvidesPackage{mytheme}", encoding="utf-8"
        )

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "main.tex",
                    "content": (
                        r"\documentclass{beamer}"
                        r"\usepackage{mytheme}"
                        r"\begin{document}中文内容\end{document}"
                    ),
                }
            )
        )

        assert ev["ok"] is False
        assert "ctex" in text
        assert not (tmp_path / "main.tex").exists()

    def test_accepts_beamer_cjk_in_comment_only(self, tmp_path):
        """中文只出现在注释里时不触发缺字误报。"""
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "main.tex",
                    "content": (
                        r"\documentclass{beamer}"
                        "\n"
                        "% 中文注释，不是正文内容"
                        "\n"
                        r"\begin{document}English\end{document}"
                    ),
                }
            )
        )

        assert ev["ok"] is True
        assert (tmp_path / "main.tex").exists()

    def test_rejects_class_options_after_process_options(self, tmp_path):
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "thesis.cls",
                    "content": (
                        r"\NeedsTeXFormat{LaTeX2e}"
                        r"\ProvidesClass{thesis}[2024/01/01 Thesis]"
                        r"\ProcessOptions\relax"
                        r"\DeclareOption{print}{\def\print{1}}"
                    ),
                }
            )
        )

        assert ev["ok"] is False
        assert "ProcessOptions" in text
        assert not (tmp_path / "thesis.cls").exists()

    def test_accepts_class_options_before_process_options(self, tmp_path):
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "thesis.cls",
                    "content": (
                        r"\NeedsTeXFormat{LaTeX2e}"
                        r"\ProvidesClass{thesis}[2024/01/01 Thesis]"
                        r"\DeclareOption{print}{\def\print{1}}"
                        r"\ProcessOptions\relax"
                        r"\LoadClass{article}"
                    ),
                }
            )
        )

        assert ev["ok"] is True
        assert (tmp_path / "thesis.cls").exists()

    def test_rejects_setter_without_accessor_default(self, tmp_path):
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "thesis.cls",
                    "content": (
                        r"\NeedsTeXFormat{LaTeX2e}"
                        r"\ProvidesClass{thesis}[2024/01/01 Thesis]"
                        r"\newcommand{\university}[1]"
                        r"{\renewcommand{\universityvalue}{#1}}"
                    ),
                }
            )
        )

        assert ev["ok"] is False
        assert "universityvalue" in text
        assert not (tmp_path / "thesis.cls").exists()

    def test_accepts_setter_with_accessor_default(self, tmp_path):
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "thesis.cls",
                    "content": (
                        r"\NeedsTeXFormat{LaTeX2e}"
                        r"\ProvidesClass{thesis}[2024/01/01 Thesis]"
                        r"\newcommand{\universityvalue}{}"
                        r"\newcommand{\university}[1]"
                        r"{\renewcommand{\universityvalue}{#1}}"
                    ),
                }
            )
        )

        assert ev["ok"] is True
        assert (tmp_path / "thesis.cls").exists()

    def test_rejects_unbalanced_braces(self, tmp_path):
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file({"file_path": "main.tex", "content": r"\textbf{hello"})
        )

        assert ev["ok"] is False
        assert "unbalanced braces" in text
        assert "not a JSON parsing error" in text
        assert not (tmp_path / "main.tex").exists()

    def test_rejects_private_command_in_body_file(self, tmp_path):
        """正文不得泄漏 .cls/.sty 内部的 `@` 控制序列。"""
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "front/titlepage.tex",
                    "content": (
                        r"\begin{titlepage}"
                        "\n"
                        r"\@thesistitle@value"
                        "\n"
                        r"\end{titlepage}"
                    ),
                }
            )
        )

        assert ev["ok"] is False
        assert "private control sequence" in text
        assert "public API" in text
        assert not (tmp_path / "front" / "titlepage.tex").exists()

    def test_allows_private_command_in_style_file(self, tmp_path):
        """类/样式文件可以使用 `@`，保护只作用于正文文件。"""
        executor = self._make_executor(tmp_path)

        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "template.sty",
                    "content": r"\newcommand{\@internal@title}{}",
                }
            )
        )

        assert ev["ok"] is True
        assert "Wrote template.sty" in text

    def test_allows_split_environment_hooks_in_style_files(self, tmp_path):
        """包文件可在不同模板钩子中分别打开/关闭环境。"""
        executor = self._make_executor(tmp_path)
        text, ev = asyncio.run(
            executor._write_file(
                {
                    "file_path": "theme.sty",
                    "content": (
                        r"\setbeamertemplate{block begin}{\begin{beamercolorbox}}"
                        "\n"
                        r"\setbeamertemplate{block end}{\end{beamercolorbox}}"
                    ),
                }
            )
        )

        assert ev["ok"] is True
        assert "Wrote theme.sty" in text

    def test_budget_counts_unique_files_not_rewrites(self, tmp_path):
        """文件预算按"不同文件数"计算：重写已有文件不消耗预算。"""
        cfg = get_config()
        cfg.features.template_gen.output_dir = str(tmp_path)
        cfg.features.template_gen.agent_max_files = 2
        executor = _ToolExecutor(tmp_path, cfg, MagicMock())

        first = asyncio.run(
            executor._write_file({"file_path": "main.tex", "content": "v1"})
        )
        second = asyncio.run(
            executor._write_file({"file_path": "chapters/intro.tex", "content": "v1"})
        )
        assert first[1]["ok"] is True
        assert second[1]["ok"] is True

        # 第 3 个新文件被拒绝，并明确告知仍可重写已有文件
        third = asyncio.run(
            executor._write_file({"file_path": "chapters/method.tex", "content": "v1"})
        )
        assert third[1]["ok"] is False
        assert "REWRITE" in third[0]

        # 重写已有文件仍然允许
        rewrite = asyncio.run(
            executor._write_file({"file_path": "main.tex", "content": "v2"})
        )
        assert rewrite[1]["ok"] is True
        assert (tmp_path / "main.tex").read_text(encoding="utf-8") == "v2"

    def test_read_log_file(self, tmp_path):
        """read_file 可读取编译生成的 main.log（修复前被白名单拒绝）。"""
        cfg = get_config()
        cfg.features.template_gen.output_dir = str(tmp_path)
        executor = _ToolExecutor(tmp_path, cfg, MagicMock())
        (tmp_path / "main.log").write_text("! Something bad\n", encoding="utf-8")

        text, ev = executor._read_file({"file_path": "main.log"})
        assert ev["ok"] is True
        assert "Something bad" in text

    def test_read_source_line_range(self, tmp_path):
        """read_file 支持按编译错误行号读取局部源码。"""
        cfg = get_config()
        cfg.features.template_gen.output_dir = str(tmp_path)
        executor = _ToolExecutor(tmp_path, cfg, MagicMock())
        (tmp_path / "main.tex").write_text(
            "line 1\nline 2\nline 3\nline 4\n", encoding="utf-8"
        )

        text, ev = executor._read_file({"file_path": "main.tex", "line_range": [2, 3]})
        assert text == "line 2\nline 3\n"
        assert ev["line_range"] == [2, 3]

    def test_read_rejects_invalid_line_range(self, tmp_path):
        cfg = get_config()
        executor = _ToolExecutor(tmp_path, cfg, MagicMock())
        (tmp_path / "main.tex").write_text("line 1\n", encoding="utf-8")

        text, event = executor._read_file(
            {"file_path": "main.tex", "line_range": [2]},
        )

        assert event["ok"] is False
        assert "line_range" in text

    def test_repeated_compile_is_skipped_without_source_change(
        self, tmp_path, monkeypatch
    ):
        """失败后源码未变时不应反复执行相同的 latexmk。"""
        from types import SimpleNamespace

        cfg = get_config()
        cfg.features.template_gen.output_dir = str(tmp_path)
        cfg.features.template_gen.agent_compile_timeout = 30
        (tmp_path / "main.tex").write_text(
            r"\documentclass{article}\begin{document}\bad\end{document}",
            encoding="utf-8",
        )
        fake = SimpleNamespace(
            success=False,
            raw_output="(./main.tex\n! Undefined control sequence.\nl.1 \\bad\n)",
            filtered_output="",
            timed_out=False,
            used_texfot=False,
        )
        calls = 0

        async def _fake_run(*args, **kwargs):
            nonlocal calls
            calls += 1
            return fake

        monkeypatch.setattr(agent_executor_mod, "run_tex_compile", _fake_run)
        executor = _ToolExecutor(tmp_path, cfg, MagicMock())

        _first_text, first_event = asyncio.run(
            executor._compile_latex({"main_file": "main.tex"})
        )
        second_text, second_event = asyncio.run(
            executor._compile_latex({"main_file": "main.tex"})
        )
        assert first_event["success"] is False
        assert second_event["compile_skipped"] is True
        assert "unchanged" in second_text
        assert calls == 1

    def test_compile_failure_uses_full_main_log_for_source_context(
        self, tmp_path, monkeypatch
    ):
        """失败摘要只有外层行号时，结构化错误应优先来自完整 .log。"""
        from types import SimpleNamespace

        cfg = get_config()
        cfg.features.template_gen.output_dir = str(tmp_path)
        (tmp_path / "main.tex").write_text(
            r"\documentclass{article}\begin{document}x\end{document}",
            encoding="utf-8",
        )
        (tmp_path / "chapters").mkdir()
        (tmp_path / "chapters" / "intro.tex").write_text(
            "line 1\nline 2\nline 3\n", encoding="utf-8"
        )
        fake = SimpleNamespace(
            success=False,
            raw_output="(./main.tex\n! Undefined control sequence.\nl.1 \\outer\n)",
            filtered_output="",
            timed_out=False,
            used_texfot=False,
        )

        async def _fake_run(*args, **kwargs):
            return fake

        monkeypatch.setattr(agent_executor_mod, "run_tex_compile", _fake_run)
        monkeypatch.setattr(
            agent_executor_mod,
            "_read_log_text",
            lambda path, max_chars=262144: (
                "(./chapters/intro.tex\n"
                "! Undefined control sequence.\n"
                "l.2 \\inner\n"
                ")"
            ),
        )
        executor = _ToolExecutor(tmp_path, cfg, MagicMock())

        _text, event = asyncio.run(executor._compile_latex({"main_file": "main.tex"}))

        assert event["success"] is False
        assert event["errors"][0]["file"] == "chapters/intro.tex"
        assert event["errors"][0]["line"] == 2
        assert event["errors"][0]["detail"] == r"\inner"


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

    def test_external_stack_file_is_not_reported_as_project_file(self, tmp_path):
        """外部 TeX 包路径不应诱导 Agent 去修改发行版文件。"""
        (tmp_path / "main.tex").write_text(
            "first\nsecond\nthird\nfourth\nfifth\n", encoding="utf-8"
        )
        log = (
            "(./main.tex\n"
            "(/usr/local/texlive/2026/texmf-dist/tex/latex/tools/array.sty\n"
            "! Illegal unit of measure (pt inserted).\n"
            "l.4 \\begin{document}\n"
            ")\n"
        )
        errors = _extract_errors(log, project_root=tmp_path, fallback_file="main.tex")
        assert errors[0]["file"] == "main.tex"
        assert "array.sty" in errors[0]["external_file"]
        formatted = _format_error_analysis(
            _attach_source_context(errors, tmp_path, fallback_file="main.tex")
        )
        assert "do not edit" in formatted
        assert "4: fourth" in formatted

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

    def test_extract_missing_character(self):
        """xelatex 的中文缺字诊断不产生 `!` 行，也应被提取并带字体提示；
        连续刷屏的缺字行只保留一条合并条目，避免挤占错误列表上限。"""
        log = (
            "Missing character: There is no 基 (U+57FA) in font "
            "Fira Sans Regular/OT:script=latn;language=dflt;mapping=tex-text;!\n"
            "Missing character: There is no 于 (U+4E8E) in font "
            "Fira Sans Regular/OT:script=latn;language=dflt;mapping=tex-text;!\n"
        )
        errors = _extract_errors(log)
        assert len(errors) == 1
        assert errors[0]["type"].startswith("Missing character")
        assert "U+57FA" in errors[0]["detail"]
        # 字体名含空格，应完整提取（正则不再只取首个 token）
        assert errors[0]["font"] == "Fira Sans Regular"
        # 合并计数：两条缺字诊断合并为一条
        assert "+1 more missing-character lines" in errors[0]["detail"]
        text = _format_error_analysis(errors)
        assert "CJK" in text or "xeCJK" in text

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

    def test_hints_for_spacefactor_metadata_trap(self):
        r"""\spacefactor 垂直模式错误应给出"正文不用 \@ 内部宏"的通用提示。"""
        errors = [
            {
                "type": "You can't use `\\spacefactor' in vertical mode.",
                "file": "front/titlepage.tex",
                "line": 13,
                "detail": "\\zihao{2}\\bfseries\\heiti\\@",
            }
        ]
        text = _format_error_analysis(errors)
        assert "HINTS" in text
        assert "PUBLIC metadata commands" in text
        assert "body files" in text

    def test_hints_for_missing_font(self):
        """缺字体错误应给出"删除显式 CJK 字体命令、交给 ctex 默认字体集"的提示。"""
        errors = [
            {
                "type": 'Package fontspec Error: The font "X" cannot be found.',
                "file": "thesis.cls",
                "line": 24,
                "detail": "\\IfFontExistsTF",
            }
        ]
        text = _format_error_analysis(errors)
        assert "HINTS" in text
        assert "CJK" in text
        assert "ctex" in text

    def test_hints_for_dimension_and_font_probe_errors(self):
        errors = [
            {
                "type": "Illegal unit of measure (pt inserted).",
                "file": "main.tex",
                "line": 41,
                "detail": "\\begin{document}",
            },
            {
                "type": "Undefined control sequence.",
                "file": "theme.sty",
                "line": 12,
                "detail": "\\IfFontExistsTF",
            },
            {
                "type": "Missing = inserted for \\ifnum.",
                "file": "main.tex",
                "line": 12,
                "detail": "\\usetheme{Custom}",
            },
        ]
        text = _format_error_analysis(errors)
        assert "Dimension parsing issue" in text
        assert "fontspec" in text
        assert "rounded=false" in text

    def test_hints_for_literal_hash(self):
        """字面 `#`（如 HTML 颜色 `#1F4E79`）错误应提示转义与颜色写法。"""
        errors = [
            {
                "type": (
                    "You can't use `macro parameter character #' in "
                    "restricted horizontal mode."
                ),
                "file": "main.tex",
                "line": 5,
                "detail": "#1F4E79",
            }
        ]
        text = _format_error_analysis(errors)
        assert "HINTS" in text
        assert "\\#" in text
        assert "HTML" in text

    def test_hints_for_recursion_and_tikz_node(self):
        """递归展开 / tikz 节点缺标签文本的通用失败签名应给出修复提示。"""
        errors = [
            {
                "type": "! TeX capacity exceeded, sorry [input stack size=10000].",
                "file": "main.tex",
                "line": 15,
                "detail": "\\labelenumi",
            },
            {
                "type": "! Package tikz Error: A node must have a (possibly empty) label text.",
                "file": "theme.tex",
                "line": 3,
                "detail": "\\node[fill=blue] (box);",
            },
        ]
        text = _format_error_analysis(errors)
        assert "recursion" in text.lower() or "Recursive" in text
        assert "(name) {text}" in text

    def test_hints_for_makeatother_in_class(self):
        """.cls 中误用 \\makeatother 导致的 Missing \\begin{document} 应给出提示。"""
        errors = [
            {
                "type": "! LaTeX Error: Missing \\begin{document}.",
                "file": "thesis.cls",
                "line": 128,
                "detail": "\\newcommand{\\mt@university}{}",
            }
        ]
        text = _format_error_analysis(errors)
        assert "HINTS" in text
        assert "\\makeatother" in text
        assert "Missing \\begin{document}" in text

    def test_hints_for_endcsname_and_alignment_and_environment(self):
        r"""\endcsname / Extra alignment tab / Environment undefined 的通用提示。"""
        errors = [
            {
                "type": "! Missing \\endcsname inserted.",
                "file": "thesis.cls",
                "line": 340,
                "detail": "}",
            },
            {
                "type": "! Extra alignment tab has been changed to \\cr.",
                "file": "main.tex",
                "line": 54,
                "detail": "\\makecoverpage",
            },
            {
                "type": "! LaTeX Error: Environment algorithm undefined.",
                "file": "back/appendix.tex",
                "line": 56,
                "detail": "\\begin{algorithm}",
            },
        ]
        text = _format_error_analysis(errors)
        assert "\\zihao{5}" in text
        assert "\\tabularnewline" in text
        assert "algorithm" in text


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
        sandbox = _sandbox_from_done(done, tmp_path)

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

    def test_trace_failure_does_not_abort_generation(self, tmp_path, monkeypatch):
        """append_trace 写入失败时仅记录日志，不中断生成主流程。"""

        async def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(agent_service_mod, "append_trace", _boom)
        svc = TemplateAgentService()
        llm = MagicMock()
        responses = [
            _make_response(
                content="Write main file",
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
                        "finish", {"summary": "done", "main_file": "main.tex"}, "t2"
                    )
                ],
            ),
        ]
        llm.chat_with_tools = AsyncMock(side_effect=responses)

        events = _collect(svc, "a simple article", llm)
        assert events[-1]["type"] == "done"


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
        # done 事件不携带 sandbox 绝对路径（多用户路径约束）
        assert "sandbox" not in done
        sandbox = _sandbox_from_done(done, tmp_path)
        # 沙箱应创建在 output_dir/<session_id>/ 下
        assert sandbox.is_relative_to(tmp_path / "templates")
        assert (sandbox / "main.tex").exists()
        assert done["session_id"] == sandbox.name


class TestPreviewFile:
    """resolve_preview_file：/preview 端点的产物预览解析。"""

    _SESSION_ID = "a1b2c3d4e5f6"

    @pytest.fixture(autouse=True)
    def _load_cfg(self, tmp_path, monkeypatch):
        load_config("config.json")
        cfg = get_config()
        monkeypatch.setattr(
            cfg.features.template_gen,
            "output_dir",
            str(tmp_path / "templates"),
        )

    def _make_sandbox(self, tmp_path):
        sandbox = Path(tmp_path) / "templates" / self._SESSION_ID
        sandbox.mkdir(parents=True)
        return sandbox

    def test_returns_pdf_media_type(self, tmp_path):
        from backend.modules.template_gen.service import TemplateGenService

        sandbox = self._make_sandbox(tmp_path)
        (sandbox / "main.pdf").write_bytes(b"%PDF-1.4 fake")

        resolved, media_type = TemplateGenService().resolve_preview_file(
            self._SESSION_ID, "main.pdf"
        )

        assert media_type == "application/pdf"
        assert resolved.is_relative_to(sandbox.resolve())
        assert resolved.name == "main.pdf"

    def test_accepts_uppercase_suffix(self, tmp_path):
        """扩展名大小写不敏感：.PDF 同样可预览。"""
        from backend.modules.template_gen.service import TemplateGenService

        sandbox = self._make_sandbox(tmp_path)
        (sandbox / "MAIN.PDF").write_bytes(b"%PDF-1.4 fake")

        resolved, media_type = TemplateGenService().resolve_preview_file(
            self._SESSION_ID, "MAIN.PDF"
        )

        assert media_type == "application/pdf"
        assert resolved.name == "MAIN.PDF"

    def test_rejects_unsupported_suffix(self, tmp_path):
        from backend.modules.template_gen.service import TemplateGenService

        sandbox = self._make_sandbox(tmp_path)
        (sandbox / "main.txt").write_text("x", encoding="utf-8")

        with pytest.raises(ValueError, match="not supported"):
            TemplateGenService().resolve_preview_file(self._SESSION_ID, "main.txt")

    def test_rejects_parent_traversal(self, tmp_path):
        from backend.modules.template_gen.service import TemplateGenService

        self._make_sandbox(tmp_path)
        outside = Path(tmp_path) / "templates" / ".." / "evil.pdf"
        outside.write_bytes(b"%PDF-1.4 fake")

        with pytest.raises(ValueError):
            TemplateGenService().resolve_preview_file(self._SESSION_ID, "../evil.pdf")

    def test_rejects_missing_file(self, tmp_path):
        """扩展名合法但文件不存在时应报错，而非返回 200 空响应。"""
        from backend.modules.template_gen.service import TemplateGenService

        self._make_sandbox(tmp_path)

        with pytest.raises(ValueError, match="not found"):
            TemplateGenService().resolve_preview_file(self._SESSION_ID, "main.pdf")

    def test_session_not_found(self, tmp_path):
        from backend.modules.template_gen.service import TemplateGenService

        with pytest.raises(ValueError, match="Session not found"):
            TemplateGenService().resolve_preview_file("ffff00001111", "main.pdf")
