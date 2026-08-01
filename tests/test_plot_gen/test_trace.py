"""plot_gen 运行追踪测试。"""

import asyncio

from backend.modules.plot_gen.trace import append_trace, read_trace


def test_read_trace_missing_returns_empty(tmp_path):
    assert read_trace(tmp_path / "nonexistent") == []


def test_append_and_read_roundtrip(tmp_path):
    sandbox = tmp_path / "session"
    sandbox.mkdir()
    events = [
        {
            "ts": "2026-01-01T00:00:00.000",
            "run_id": "abc",
            "type": "run_start",
            "data": {"x": 1},
        },
        {
            "ts": "2026-01-01T00:00:00.001",
            "run_id": "abc",
            "type": "llm",
            "data": {"content": "hi"},
        },
    ]
    asyncio.run(append_trace(sandbox, events[0]))
    asyncio.run(append_trace(sandbox, events[1]))

    result = read_trace(sandbox)
    assert result == events
    assert (sandbox / "trace.jsonl").exists()


def test_append_is_append_only(tmp_path):
    sandbox = tmp_path / "session"
    sandbox.mkdir()
    asyncio.run(append_trace(sandbox, {"type": "a"}))
    asyncio.run(append_trace(sandbox, {"type": "b"}))
    types = [e["type"] for e in read_trace(sandbox)]
    assert types == ["a", "b"]


def test_read_trace_skips_corrupt_lines(tmp_path):
    sandbox = tmp_path / "session"
    sandbox.mkdir()
    (sandbox / "trace.jsonl").write_text(
        '{"type": "ok"}\nnot-json\n{"type": "ok2"}\n', encoding="utf-8"
    )
    types = [e["type"] for e in read_trace(sandbox)]
    assert types == ["ok", "ok2"]
