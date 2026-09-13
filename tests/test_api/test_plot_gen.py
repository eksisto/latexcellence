"""plot_gen 上传与 Agent mock 测试。"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.config import get_config
from backend.modules.plot_gen.service import PlotGenService
from tests.conftest import load_repo_config


def _make_tool_call(name, arguments, cid="tc-1"):
    return SimpleNamespace(
        id=cid,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments, ensure_ascii=False),
        ),
    )


class _Response:
    """chat_stream_with_tools 响应桩：response 形状 + 异步可迭代 StreamChunk 序列。"""

    def __init__(self, content=None, tool_calls=None):
        self.choices = [
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
        ]

    def __aiter__(self):
        async def _gen():
            msg = self.choices[0].message
            if msg.content:
                from backend.core.llm_client import StreamChunk

                yield StreamChunk(content=msg.content, usage=None)
            if msg.tool_calls:
                from backend.core.llm_client import StreamChunk

                yield StreamChunk(content=None, usage=None, tool_calls=msg.tool_calls)

        return _gen()


def _make_response(content=None, tool_calls=None):
    return _Response(content=content, tool_calls=tool_calls)


async def _collect(generator):
    return [event async for event in generator]


@pytest.fixture
def plot_cfg(tmp_path, monkeypatch):
    load_repo_config()
    cfg = get_config()
    monkeypatch.setattr(cfg.llm, "api_key", "test-key")
    monkeypatch.setattr(
        cfg.features.plot_gen,
        "output_dir",
        str(tmp_path / "plots"),
    )
    monkeypatch.setattr(cfg.features.plot_gen, "agent_max_rounds", 8)
    monkeypatch.setattr(cfg.features.plot_gen, "agent_max_files", 10)
    monkeypatch.setattr(cfg.features.plot_gen, "agent_max_file_size_kb", 64)
    return cfg


@pytest.mark.asyncio
async def test_upload_creates_session(plot_cfg):
    service = PlotGenService()
    result = await service.create_session_from_upload(
        b"group,value\nA,1\nB,2\n",
        "data.csv",
        description="比较两组",
        style="nature",
    )
    assert result["session_id"]
    assert result["data_profile"]["rows"] == 2
    sandbox = Path(plot_cfg.features.plot_gen.output_dir) / result["session_id"]
    assert (sandbox / "meta.json").exists()


@pytest.mark.asyncio
async def test_generate_with_mock_llm(plot_cfg, monkeypatch):
    service = PlotGenService()
    result = await service.create_session_from_upload(
        b"group,value\nA,1\nB,2\n",
        "data.csv",
        description="比较两组",
    )
    session_id = result["session_id"]

    class FakeExecutor:
        def __init__(self, sandbox, cfg, logger=None, style_key=None):
            pass

        async def execute(self, tool_name, args):
            if tool_name == "write_file":
                return (
                    "wrote",
                    {"tool": "write_file", "ok": True, "summary": "wrote"},
                )
            if tool_name == "python_sandbox":
                return (
                    "execution failed",
                    {
                        "tool": "python_sandbox",
                        "ok": False,
                        "success": False,
                        "summary": "execution failed",
                    },
                )
            if tool_name == "compile_tikz":
                return (
                    "tikz failed",
                    {
                        "tool": "compile_tikz",
                        "ok": False,
                        "success": False,
                        "summary": "tikz failed",
                    },
                )
            if tool_name == "finish":
                return (
                    "finished",
                    {"tool": "finish", "ok": True, "summary": "finished"},
                )
            return ("ok", {"tool": tool_name, "ok": True, "summary": "ok"})

        def list_files(self):
            return []

        def scan_artifacts(self):
            return []

    import backend.modules.plot_gen.agent as agent_mod

    monkeypatch.setattr(agent_mod, "PlotExecutor", FakeExecutor)

    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value={
            "chart_type": "bar",
            "x": "group",
            "y": "value",
            "rationale": "bar",
        }
    )
    llm.chat_stream_with_tools = MagicMock(
        side_effect=[
            _make_response(
                content="Write the script",
                tool_calls=[
                    _make_tool_call(
                        "write_file",
                        {
                            "file_path": "scripts/plot.py",
                            "content": (
                                "from pathlib import Path\n"
                                "Path('artifacts/figure.pdf').write_bytes(b'%PDF')\n"
                                "Path('artifacts/figure.svg').write_text('<svg/>', encoding='utf-8')\n"
                                "Path('artifacts/figure.tex').write_text('\\\\begin{tikzpicture}\\\\end{tikzpicture}', encoding='utf-8')\n"
                            ),
                        },
                        "w1",
                    )
                ],
            ),
            _make_response(
                content="Running and finishing",
                tool_calls=[
                    _make_tool_call(
                        "python_sandbox",
                        {"script_name": "scripts/plot.py"},
                        "r1",
                    ),
                    _make_tool_call(
                        "compile_tikz",
                        {"tikz_file": "artifacts/figure.tex"},
                        "c1",
                    ),
                    _make_tool_call("finish", {"summary": "done"}, "f1"),
                ],
            ),
        ]
    )

    events = await _collect(
        service.generate(
            session_id,
            "比较两组",
            "nature",
            "zh",
            llm=llm,
        )
    )
    types = [e["type"] for e in events]
    assert "chart_plan" in types
    assert "execution" in types
    assert "tikz" in types
    assert events[-1]["type"] == "done"
    # finish 会触发 warning，因为 fake python_sandbox 与 compile_tikz 均失败。
    assert "warning" in types


@pytest.mark.asyncio
async def test_generate_passes_style_and_persists_final_message(plot_cfg, monkeypatch):
    """用户选择的风格应传给 executor；正常收尾（无工具调用）时最终 assistant 消息应持久化。"""
    service = PlotGenService()
    result = await service.create_session_from_upload(
        b"group,value\nA,1\nB,2\n",
        "data.csv",
        description="比较两组",
        style="cell",
    )
    session_id = result["session_id"]

    created_styles = []

    class FakeExecutor:
        def __init__(self, sandbox, cfg, logger=None, style_key=None):
            created_styles.append(style_key)

        async def execute(self, tool_name, args):
            raise AssertionError("executor should not run without tool calls")

        def list_files(self):
            return []

        def scan_artifacts(self):
            return []

    import backend.modules.plot_gen.agent as agent_mod

    monkeypatch.setattr(agent_mod, "PlotExecutor", FakeExecutor)

    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value={
            "chart_type": "bar",
            "x": "group",
            "y": "value",
            "rationale": "bar",
        }
    )
    llm.chat_stream_with_tools = MagicMock(
        return_value=_make_response(content="这是最终回答", tool_calls=None)
    )

    await _collect(service.generate(session_id, "比较两组", "cell", "zh", llm=llm))

    assert created_styles == ["cell"]

    sandbox = Path(plot_cfg.features.plot_gen.output_dir) / session_id
    meta = json.loads((sandbox / "meta.json").read_text(encoding="utf-8"))
    assert meta["messages"][-1]["role"] == "assistant"
    assert meta["messages"][-1]["content"] == "这是最终回答"


@pytest.mark.asyncio
async def test_generate_records_trace(plot_cfg, monkeypatch):
    """Agent loop 的完整交互应写入 trace.jsonl 并可通过服务读取。"""
    service = PlotGenService()
    result = await service.create_session_from_upload(
        b"group,value\nA,1\nB,2\n",
        "data.csv",
        description="比较两组",
    )
    session_id = result["session_id"]
    sandbox = Path(plot_cfg.features.plot_gen.output_dir) / session_id

    class FakeExecutor:
        def __init__(self, sandbox, cfg, logger=None, style_key=None):
            pass

        async def execute(self, tool_name, args):
            if tool_name == "write_file":
                return "wrote", {"tool": "write_file", "ok": True, "summary": "wrote"}
            if tool_name == "python_sandbox":
                return "execution failed", {
                    "tool": "python_sandbox",
                    "ok": False,
                    "success": False,
                    "summary": "execution failed",
                }
            if tool_name == "compile_tikz":
                return "tikz failed", {
                    "tool": "compile_tikz",
                    "ok": False,
                    "success": False,
                    "summary": "tikz failed",
                }
            if tool_name == "finish":
                return "finished", {"tool": "finish", "ok": True, "summary": "finished"}
            return "ok", {"tool": tool_name, "ok": True, "summary": "ok"}

        def list_files(self):
            return []

        def scan_artifacts(self):
            return []

    import backend.modules.plot_gen.agent as agent_mod

    monkeypatch.setattr(agent_mod, "PlotExecutor", FakeExecutor)

    llm = MagicMock()
    llm.chat = AsyncMock(return_value={})
    llm.chat_stream_with_tools = MagicMock(
        side_effect=[
            _make_response(
                content="Write the script",
                tool_calls=[
                    _make_tool_call(
                        "write_file",
                        {
                            "file_path": "scripts/plot.py",
                            "content": "print(1)",
                        },
                        "w1",
                    )
                ],
            ),
            _make_response(
                content="Running and finishing",
                tool_calls=[
                    _make_tool_call(
                        "python_sandbox",
                        {"script_name": "scripts/plot.py"},
                        "r1",
                    ),
                    _make_tool_call("finish", {"summary": "done"}, "f1"),
                ],
            ),
        ]
    )

    await _collect(service.generate(session_id, "比较两组", "nature", "zh", llm=llm))

    trace_file = sandbox / "trace.jsonl"
    assert trace_file.exists()

    data = service.get_session_trace(session_id)
    assert data["session_id"] == session_id
    types = [e["type"] for e in data["events"]]
    assert "run_start" in types
    assert "llm" in types
    assert "tool_result" in types
    assert "run_end" in types

    run_end = next(e for e in data["events"] if e["type"] == "run_end")
    assert run_end["data"]["execution_success"] is False
    assert run_end["data"]["rounds"] >= 2

    run_start = next(e for e in data["events"] if e["type"] == "run_start")
    assert run_start["data"]["run_type"] == "generate"
    assert run_start["data"]["system_prompt"]
    assert run_start["data"]["messages"]

    tool_result = next(e for e in data["events"] if e["type"] == "tool_result")
    assert tool_result["data"]["tool"] == "write_file"
    assert tool_result["data"]["args"]["file_path"] == "scripts/plot.py"

    llm_ev = next(e for e in data["events"] if e["type"] == "llm")
    assert llm_ev["data"]["tool_calls"][0]["function"]["name"] == "write_file"


@pytest.mark.asyncio
async def test_trace_failure_does_not_abort_generation(plot_cfg, monkeypatch):
    """append_trace 写入失败时仅记录日志，不中断生成主流程。"""
    import backend.modules.plot_gen.agent as agent_mod

    async def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(agent_mod, "append_trace", _boom)

    service = PlotGenService()
    result = await service.create_session_from_upload(
        b"group,value\nA,1\nB,2\n",
        "data.csv",
        description="比较两组",
    )
    session_id = result["session_id"]

    class FakeExecutor:
        def __init__(self, sandbox, cfg, logger=None, style_key=None):
            pass

        async def execute(self, tool_name, args):
            if tool_name == "finish":
                return "finished", {"tool": "finish", "ok": True, "summary": "finished"}
            return "ok", {"tool": tool_name, "ok": True, "summary": "ok"}

        def list_files(self):
            return []

        def scan_artifacts(self):
            return []

    monkeypatch.setattr(agent_mod, "PlotExecutor", FakeExecutor)

    llm = MagicMock()
    llm.chat = AsyncMock(return_value={})
    llm.chat_stream_with_tools = MagicMock(
        side_effect=[
            _make_response(
                content="Finish now",
                tool_calls=[_make_tool_call("finish", {"summary": "done"}, "f1")],
            ),
        ]
    )

    events = await _collect(
        service.generate(session_id, "比较两组", "nature", "zh", llm=llm)
    )
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_generate_first_round_text_only_is_nudged(plot_cfg, monkeypatch):
    """首轮只输出文字未调用工具时，注入强约束消息重试，而不是直接结束会话。"""
    service = PlotGenService()
    result = await service.create_session_from_upload(
        b"group,value\nA,1\nB,2\n",
        "data.csv",
        description="比较两组",
    )
    session_id = result["session_id"]

    class FakeExecutor:
        def __init__(self, sandbox, cfg, logger=None, style_key=None):
            pass

        async def execute(self, tool_name, args):
            if tool_name == "finish":
                return "finished", {"tool": "finish", "ok": True, "summary": "finished"}
            return "ok", {"tool": tool_name, "ok": True, "summary": "ok"}

        def list_files(self):
            return []

        def scan_artifacts(self):
            return []

    import backend.modules.plot_gen.agent as agent_mod

    monkeypatch.setattr(agent_mod, "PlotExecutor", FakeExecutor)

    llm = MagicMock()
    llm.chat = AsyncMock(return_value={})
    llm.chat_stream_with_tools = MagicMock(
        side_effect=[
            _make_response(content="让我先分析一下数据结构……", tool_calls=None),
            _make_response(
                content="Finish now",
                tool_calls=[_make_tool_call("finish", {"summary": "done"}, "f1")],
            ),
        ]
    )

    events = await _collect(
        service.generate(session_id, "比较两组", "nature", "zh", llm=llm)
    )

    # 首轮文字回复后没有收尾，而是带着强约束 system 消息发起了第二次调用
    assert llm.chat_stream_with_tools.call_count == 2
    second_messages = llm.chat_stream_with_tools.call_args_list[1].kwargs["messages"]
    assert any(
        m.get("role") == "system" and "called NO tool" in m.get("content", "")
        for m in second_messages
    )
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_generate_blocks_verification_loop_after_success(plot_cfg, monkeypatch):
    """compile_tikz 成功后未修改产物时，read/list/rerun/compile 应被拦截。"""
    import backend.modules.plot_gen.agent as agent_mod

    service = PlotGenService()
    result = await service.create_session_from_upload(
        b"group,value\nA,1\nB,2\n",
        "data.csv",
        description="比较两组",
    )
    session_id = result["session_id"]
    executed: list[str] = []

    class FakeExecutor:
        def __init__(self, sandbox, cfg, logger=None, style_key=None):
            pass

        async def execute(self, tool_name, args):
            executed.append(tool_name)
            if tool_name == "write_file":
                return (
                    "wrote",
                    {
                        "tool": "write_file",
                        "ok": True,
                        "summary": "wrote",
                        "file_path": args.get("file_path", ""),
                    },
                )
            if tool_name == "python_sandbox":
                return (
                    "ok",
                    {
                        "tool": "python_sandbox",
                        "ok": True,
                        "success": True,
                        "summary": "ok",
                    },
                )
            if tool_name == "compile_tikz":
                return (
                    "ok",
                    {
                        "tool": "compile_tikz",
                        "ok": True,
                        "success": True,
                        "summary": "ok",
                    },
                )
            if tool_name == "finish":
                return "finished", {
                    "tool": "finish",
                    "ok": True,
                    "summary": "finished",
                }
            return "ok", {"tool": tool_name, "ok": True, "summary": "ok"}

        def list_files(self):
            return []

        def scan_artifacts(self):
            return []

    monkeypatch.setattr(agent_mod, "PlotExecutor", FakeExecutor)

    llm = MagicMock()
    llm.chat = AsyncMock(return_value={})
    llm.chat_stream_with_tools = MagicMock(
        side_effect=[
            _make_response(
                content="Write script",
                tool_calls=[
                    _make_tool_call(
                        "write_file",
                        {"file_path": "scripts/plot.py", "content": "print(1)"},
                        "w1",
                    )
                ],
            ),
            _make_response(
                content="Run and compile",
                tool_calls=[
                    _make_tool_call(
                        "python_sandbox",
                        {"script_name": "scripts/plot.py"},
                        "r1",
                    ),
                    _make_tool_call(
                        "compile_tikz",
                        {"tikz_file": "artifacts/figure.tex"},
                        "c1",
                    ),
                ],
            ),
            _make_response(
                content="Verify files",
                tool_calls=[
                    _make_tool_call(
                        "read_file",
                        {"file_path": "artifacts/tikz_spec.json"},
                        "v1",
                    )
                ],
            ),
            _make_response(
                content="Finish now",
                tool_calls=[_make_tool_call("finish", {"summary": "done"}, "f1")],
            ),
        ]
    )

    events = await _collect(
        service.generate(session_id, "比较两组", "nature", "zh", llm=llm)
    )

    warnings = [e["message"] for e in events if e["type"] == "warning"]
    assert any("VERIFIED-LOOP BLOCK" in w for w in warnings)
    # read_file 被拦截，未到达 executor
    assert "read_file" not in executed
    assert executed == ["write_file", "python_sandbox", "compile_tikz", "finish"]
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_generate_blocks_stale_finish_after_change(plot_cfg, monkeypatch):
    """最后一次编译后修改了影响产物的文件再 finish，应先被拒绝。"""
    import backend.modules.plot_gen.agent as agent_mod

    service = PlotGenService()
    result = await service.create_session_from_upload(
        b"group,value\nA,1\nB,2\n",
        "data.csv",
        description="比较两组",
    )
    session_id = result["session_id"]
    executed: list[str] = []

    class FakeExecutor:
        def __init__(self, sandbox, cfg, logger=None, style_key=None):
            pass

        async def execute(self, tool_name, args):
            executed.append(tool_name)
            if tool_name == "write_file":
                return (
                    "wrote",
                    {
                        "tool": "write_file",
                        "ok": True,
                        "summary": "wrote",
                        "file_path": args.get("file_path", ""),
                    },
                )
            if tool_name == "python_sandbox":
                return (
                    "ok",
                    {
                        "tool": "python_sandbox",
                        "ok": True,
                        "success": True,
                        "summary": "ok",
                    },
                )
            if tool_name == "compile_tikz":
                return (
                    "ok",
                    {
                        "tool": "compile_tikz",
                        "ok": True,
                        "success": True,
                        "summary": "ok",
                    },
                )
            if tool_name == "finish":
                return "finished", {
                    "tool": "finish",
                    "ok": True,
                    "summary": "finished",
                }
            return "ok", {"tool": tool_name, "ok": True, "summary": "ok"}

        def list_files(self):
            return []

        def scan_artifacts(self):
            return []

    monkeypatch.setattr(agent_mod, "PlotExecutor", FakeExecutor)

    llm = MagicMock()
    llm.chat = AsyncMock(return_value={})
    llm.chat_stream_with_tools = MagicMock(
        side_effect=[
            _make_response(
                content="Write script",
                tool_calls=[
                    _make_tool_call(
                        "write_file",
                        {"file_path": "scripts/plot.py", "content": "print(1)"},
                        "w1",
                    )
                ],
            ),
            _make_response(
                content="Run and compile",
                tool_calls=[
                    _make_tool_call(
                        "python_sandbox",
                        {"script_name": "scripts/plot.py"},
                        "r1",
                    ),
                    _make_tool_call(
                        "compile_tikz",
                        {"tikz_file": "artifacts/figure.tex"},
                        "c1",
                    ),
                ],
            ),
            _make_response(
                content="Modify script after compile",
                tool_calls=[
                    _make_tool_call(
                        "write_file",
                        {"file_path": "scripts/plot.py", "content": "print(2)"},
                        "w2",
                    )
                ],
            ),
            _make_response(
                content="Try to finish stale",
                tool_calls=[_make_tool_call("finish", {"summary": "done"}, "f1")],
            ),
            _make_response(
                content="Finish after block",
                tool_calls=[_make_tool_call("finish", {"summary": "done"}, "f2")],
            ),
        ]
    )

    events = await _collect(
        service.generate(session_id, "比较两组", "nature", "zh", llm=llm)
    )

    warnings = [e["message"] for e in events if e["type"] == "warning"]
    assert any("STALE FINISH BLOCKED" in w for w in warnings)
    assert any("`finish` accepted although" in w for w in warnings)
    # 第一次 finish 被拦截，第二次允许；executor 只真正执行过一次 finish
    assert executed.count("finish") == 1
    assert events[-1]["type"] == "done"


def test_is_figure_affecting_path_coverage():
    """影响产物的文件判定需覆盖手写回退的自定义文件名与路径变体。"""
    from backend.modules.plot_gen.agent import _is_figure_affecting_path

    affecting = [
        "scripts/plot.py",
        "scripts/helpers/load.py",
        "artifacts/tikz_spec.json",
        "artifacts/figure.tex",
        "artifacts/corr.csv",
        "artifacts/plot.tex",
        "artifacts/./plot.tex",
        "artifacts/../artifacts/plot.tex",
        "scripts/../scripts/plot.py",
        "artifacts//data_SeriesA.csv",
    ]
    for p in affecting:
        assert _is_figure_affecting_path(p) is True, p

    not_affecting = [
        "",
        ".",
        "README.txt",
        "data/s4_category.csv",
        "artifacts/figure.png",
        "artifacts/tikz_main.log",
        "artifacts/notes.txt",
    ]
    for p in not_affecting:
        assert _is_figure_affecting_path(p) is False, p


@pytest.mark.asyncio
async def test_generate_blocked_compile_keeps_tikz_state(plot_cfg, monkeypatch):
    """被 VERIFIED-LOOP 拦截的 compile_tikz 不得覆盖 tikz_state 或计入失败统计。"""
    import backend.modules.plot_gen.agent as agent_mod

    service = PlotGenService()
    result = await service.create_session_from_upload(
        b"group,value\nA,1\nB,2\n",
        "data.csv",
        description="比较两组",
    )
    session_id = result["session_id"]
    executed: list[str] = []

    class FakeExecutor:
        def __init__(self, sandbox, cfg, logger=None, style_key=None):
            pass

        async def execute(self, tool_name, args):
            executed.append(tool_name)
            if tool_name == "write_file":
                return (
                    "wrote",
                    {
                        "tool": "write_file",
                        "ok": True,
                        "summary": "wrote",
                        "file_path": args.get("file_path", ""),
                    },
                )
            if tool_name == "python_sandbox":
                return (
                    "ok",
                    {
                        "tool": "python_sandbox",
                        "ok": True,
                        "success": True,
                        "summary": "ok",
                    },
                )
            if tool_name == "compile_tikz":
                return (
                    "ok",
                    {
                        "tool": "compile_tikz",
                        "ok": True,
                        "success": True,
                        "summary": "ok",
                    },
                )
            if tool_name == "finish":
                return "finished", {
                    "tool": "finish",
                    "ok": True,
                    "summary": "finished",
                }
            return "ok", {"tool": tool_name, "ok": True, "summary": "ok"}

        def list_files(self):
            return []

        def scan_artifacts(self):
            return []

    monkeypatch.setattr(agent_mod, "PlotExecutor", FakeExecutor)

    llm = MagicMock()
    llm.chat = AsyncMock(return_value={})
    llm.chat_stream_with_tools = MagicMock(
        side_effect=[
            _make_response(
                content="Write script",
                tool_calls=[
                    _make_tool_call(
                        "write_file",
                        {"file_path": "scripts/plot.py", "content": "print(1)"},
                        "w1",
                    )
                ],
            ),
            _make_response(
                content="Run and compile",
                tool_calls=[
                    _make_tool_call(
                        "python_sandbox",
                        {"script_name": "scripts/plot.py"},
                        "r1",
                    ),
                    _make_tool_call(
                        "compile_tikz",
                        {"tikz_file": "artifacts/figure.tex"},
                        "c1",
                    ),
                ],
            ),
            *[
                _make_response(
                    content=f"Blocked compile {i}",
                    tool_calls=[
                        _make_tool_call(
                            "compile_tikz",
                            {"tikz_file": "artifacts/figure.tex"},
                            f"b{i}",
                        )
                    ],
                )
                for i in range(3)
            ],
            _make_response(
                content="Finish now",
                tool_calls=[_make_tool_call("finish", {"summary": "done"}, "f1")],
            ),
        ]
    )

    events = await _collect(
        service.generate(session_id, "比较两组", "nature", "zh", llm=llm)
    )

    warnings = [e["message"] for e in events if e["type"] == "warning"]
    assert sum("VERIFIED-LOOP BLOCK" in w for w in warnings) == 3
    assert not any("without a successful TikZ compilation" in w for w in warnings)
    assert not any("identical result" in w for w in warnings)
    assert not any("has failed 3 times in a row" in w for w in warnings)
    tikz_events = [e for e in events if e["type"] == "tikz"]
    assert len(tikz_events) == 1
    assert tikz_events[0]["success"] is True
    meta = json.loads(
        (
            Path(plot_cfg.features.plot_gen.output_dir) / session_id / "meta.json"
        ).read_text(encoding="utf-8")
    )
    assert meta["last_tikz_success"] is True
    assert meta["last_execution_success"] is True
    assert executed.count("finish") == 1
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_generate_blocked_python_sandbox_keeps_execution_state(
    plot_cfg, monkeypatch
):
    """被拦截的 python_sandbox 不得把 last_execution_success 覆盖为 None。"""
    import backend.modules.plot_gen.agent as agent_mod

    service = PlotGenService()
    result = await service.create_session_from_upload(
        b"group,value\nA,1\nB,2\n",
        "data.csv",
        description="比较两组",
    )
    session_id = result["session_id"]

    class FakeExecutor:
        def __init__(self, sandbox, cfg, logger=None, style_key=None):
            pass

        async def execute(self, tool_name, args):
            if tool_name == "write_file":
                return (
                    "wrote",
                    {
                        "tool": "write_file",
                        "ok": True,
                        "summary": "wrote",
                        "file_path": args.get("file_path", ""),
                    },
                )
            if tool_name == "python_sandbox":
                return (
                    "ok",
                    {
                        "tool": "python_sandbox",
                        "ok": True,
                        "success": True,
                        "summary": "ok",
                    },
                )
            if tool_name == "compile_tikz":
                return (
                    "ok",
                    {
                        "tool": "compile_tikz",
                        "ok": True,
                        "success": True,
                        "summary": "ok",
                    },
                )
            if tool_name == "finish":
                return "finished", {
                    "tool": "finish",
                    "ok": True,
                    "summary": "finished",
                }
            return "ok", {"tool": tool_name, "ok": True, "summary": "ok"}

        def list_files(self):
            return []

        def scan_artifacts(self):
            return []

    monkeypatch.setattr(agent_mod, "PlotExecutor", FakeExecutor)

    llm = MagicMock()
    llm.chat = AsyncMock(return_value={})
    llm.chat_stream_with_tools = MagicMock(
        side_effect=[
            _make_response(
                content="Write script",
                tool_calls=[
                    _make_tool_call(
                        "write_file",
                        {"file_path": "scripts/plot.py", "content": "print(1)"},
                        "w1",
                    )
                ],
            ),
            _make_response(
                content="Run and compile",
                tool_calls=[
                    _make_tool_call(
                        "python_sandbox",
                        {"script_name": "scripts/plot.py"},
                        "r1",
                    ),
                    _make_tool_call(
                        "compile_tikz",
                        {"tikz_file": "artifacts/figure.tex"},
                        "c1",
                    ),
                ],
            ),
            _make_response(
                content="Rerun script after success",
                tool_calls=[
                    _make_tool_call(
                        "python_sandbox",
                        {"script_name": "scripts/plot.py"},
                        "r2",
                    )
                ],
            ),
            _make_response(
                content="Finish now",
                tool_calls=[_make_tool_call("finish", {"summary": "done"}, "f1")],
            ),
        ]
    )

    events = await _collect(
        service.generate(session_id, "比较两组", "nature", "zh", llm=llm)
    )

    warnings = [e["message"] for e in events if e["type"] == "warning"]
    assert any("VERIFIED-LOOP BLOCK" in w for w in warnings)
    assert not any("without a successful TikZ compilation" in w for w in warnings)
    execution_events = [e for e in events if e["type"] == "execution"]
    assert len(execution_events) == 1
    assert execution_events[0]["success"] is True
    meta = json.loads(
        (
            Path(plot_cfg.features.plot_gen.output_dir) / session_id / "meta.json"
        ).read_text(encoding="utf-8")
    )
    assert meta["last_execution_success"] is True
    assert meta["last_tikz_success"] is True


@pytest.mark.asyncio
async def test_generate_blocks_stale_finish_custom_artifact_name(plot_cfg, monkeypatch):
    """手写回退的自定义 artifacts/*.csv 也应触发 stale finish 拦截。"""
    import backend.modules.plot_gen.agent as agent_mod

    service = PlotGenService()
    result = await service.create_session_from_upload(
        b"group,value\nA,1\nB,2\n",
        "data.csv",
        description="比较两组",
    )
    session_id = result["session_id"]
    executed: list[str] = []

    class FakeExecutor:
        def __init__(self, sandbox, cfg, logger=None, style_key=None):
            pass

        async def execute(self, tool_name, args):
            executed.append(tool_name)
            if tool_name == "write_file":
                return (
                    "wrote",
                    {
                        "tool": "write_file",
                        "ok": True,
                        "summary": "wrote",
                        "file_path": args.get("file_path", ""),
                    },
                )
            if tool_name == "python_sandbox":
                return (
                    "ok",
                    {
                        "tool": "python_sandbox",
                        "ok": True,
                        "success": True,
                        "summary": "ok",
                    },
                )
            if tool_name == "compile_tikz":
                return (
                    "ok",
                    {
                        "tool": "compile_tikz",
                        "ok": True,
                        "success": True,
                        "summary": "ok",
                    },
                )
            if tool_name == "finish":
                return "finished", {
                    "tool": "finish",
                    "ok": True,
                    "summary": "finished",
                }
            return "ok", {"tool": tool_name, "ok": True, "summary": "ok"}

        def list_files(self):
            return []

        def scan_artifacts(self):
            return []

    monkeypatch.setattr(agent_mod, "PlotExecutor", FakeExecutor)

    llm = MagicMock()
    llm.chat = AsyncMock(return_value={})
    llm.chat_stream_with_tools = MagicMock(
        side_effect=[
            _make_response(
                content="Write script",
                tool_calls=[
                    _make_tool_call(
                        "write_file",
                        {"file_path": "scripts/plot.py", "content": "print(1)"},
                        "w1",
                    )
                ],
            ),
            _make_response(
                content="Run and compile",
                tool_calls=[
                    _make_tool_call(
                        "python_sandbox",
                        {"script_name": "scripts/plot.py"},
                        "r1",
                    ),
                    _make_tool_call(
                        "compile_tikz",
                        {"tikz_file": "artifacts/figure.tex"},
                        "c1",
                    ),
                ],
            ),
            _make_response(
                content="Write custom artifact after compile",
                tool_calls=[
                    _make_tool_call(
                        "write_file",
                        {
                            "file_path": "artifacts/corr.csv",
                            "content": "x,y\n1,2",
                        },
                        "w2",
                    )
                ],
            ),
            _make_response(
                content="Try to finish stale",
                tool_calls=[_make_tool_call("finish", {"summary": "done"}, "f1")],
            ),
            _make_response(
                content="Finish after block",
                tool_calls=[_make_tool_call("finish", {"summary": "done"}, "f2")],
            ),
        ]
    )

    events = await _collect(
        service.generate(session_id, "比较两组", "nature", "zh", llm=llm)
    )

    warnings = [e["message"] for e in events if e["type"] == "warning"]
    assert any("STALE FINISH BLOCKED" in w for w in warnings)
    assert executed.count("finish") == 1
    assert events[-1]["type"] == "done"


class TestPlotGenTraceRouter:
    """/trace 与 /trace/download 端点测试。"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from main import app

        return TestClient(app)

    def test_trace_view(self, client):
        data = {
            "session_id": "abc123",
            "meta": {},
            "events": [{"type": "run_start", "data": {"run_type": "generate"}}],
        }
        with patch.object(
            PlotGenService, "get_session_trace", return_value=data
        ) as mock_trace:
            response = client.get("/api/plot-gen/sessions/abc123/trace")
        assert response.status_code == 200
        body = response.json()
        assert body["session_id"] == "abc123"
        assert body["events"][0]["type"] == "run_start"
        mock_trace.assert_called_once_with("abc123")

    def test_trace_download(self, client):
        data = {
            "session_id": "abc123",
            "meta": {},
            "events": [{"type": "llm", "data": {"content": "hi"}}],
        }
        with patch.object(PlotGenService, "get_session_trace", return_value=data):
            response = client.get("/api/plot-gen/sessions/abc123/trace/download")
        assert response.status_code == 200
        assert "trace.json" in response.headers["content-disposition"]
        body = json.loads(response.content.decode("utf-8"))
        assert body["events"][0]["type"] == "llm"

    def test_trace_download_empty_returns_404(self, client):
        data = {"session_id": "abc123", "meta": {}, "events": []}
        with patch.object(PlotGenService, "get_session_trace", return_value=data):
            response = client.get("/api/plot-gen/sessions/abc123/trace/download")
        assert response.status_code == 404
