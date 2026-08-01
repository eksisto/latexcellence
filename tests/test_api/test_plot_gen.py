"""plot_gen 上传与 Agent mock 测试。"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.config import get_config, load_config
from backend.modules.plot_gen.service import PlotGenService


def _make_tool_call(name, arguments, cid="tc-1"):
    return SimpleNamespace(
        id=cid,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments, ensure_ascii=False),
        ),
    )


def _make_response(content=None, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
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


async def _collect(generator):
    return [event async for event in generator]


@pytest.fixture
def plot_cfg(tmp_path, monkeypatch):
    load_config("config.json")
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
    llm.chat_with_tools = AsyncMock(
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
    llm.chat_with_tools = AsyncMock(
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
    llm.chat_with_tools = AsyncMock(
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
