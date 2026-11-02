"""style_modify Agent 会话、候选 overlay 与批量事务测试。"""

import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.core.config import get_config
from backend.core.project_manager import FileConflictError, LatexProject
from backend.modules.style_modify.agent import (
    StyleModifyAgent,
    _tool_failure_text,
    get_style_modify_agent,
)
from backend.modules.style_modify.models import CompileSummary, SessionMeta
from backend.modules.style_modify.service import StyleModifyService
from backend.modules.style_modify.session import (
    StyleModifySessionStore,
    get_session_store,
)
from backend.utils.file_ops import compute_md5


@pytest.fixture
def workdir():
    path = Path(tempfile.mkdtemp(prefix="latexcellence_style_modify_"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def latex_project(workdir):
    root = workdir / "latex-project"
    root.mkdir()
    (root / "main.tex").write_text(
        r"\documentclass{article}" + "\n"
        r"\usepackage{mystyle}" + "\n"
        r"\begin{document}" + "\n"
        r"\section{Introduction}" + "\n"
        r"\end{document}" + "\n",
        encoding="utf-8",
    )
    (root / "mystyle.sty").write_text(
        r"\NeedsTeXFormat{LaTeX2e}" + "\n"
        r"\ProvidesPackage{mystyle}" + "\n"
        r"\definecolor{themecolor}{HTML}{333333}" + "\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def isolated_sessions(workdir):
    cfg = get_config()
    old = cfg.features.style_modify.agent.sessions_dir
    cfg.features.style_modify.agent.sessions_dir = str(workdir / "sessions")
    yield
    cfg.features.style_modify.agent.sessions_dir = old


@pytest.fixture
def open_project(workdir, latex_project):
    from backend.core.workspace_manager import (
        get_project_manager_for_workspace,
    )

    workspace_id = f"ws-{workdir.name}"
    project = get_project_manager_for_workspace(workspace_id)
    project.open(str(latex_project))
    return workspace_id, project


class TestLatexProjectWholeFileBatch:
    def test_apply_whole_files_success(self, latex_project):
        project = LatexProject()
        project.open(str(latex_project))
        old_md5 = project.get_file_md5("main.tex")
        new_content = (
            r"\documentclass{article}" + "\n"
            r"\begin{document}" + "\n"
            r"\section{New}" + "\n"
            r"\end{document}" + "\n"
        )

        result = project.apply_whole_files(
            {"main.tex": new_content},
            expected_md5s={"main.tex": old_md5},
        )

        assert (latex_project / "main.tex").read_text(encoding="utf-8") == new_content
        assert result["main.tex"] == compute_md5(latex_project / "main.tex")

    def test_apply_whole_files_rejects_conflict(self, latex_project):
        project = LatexProject()
        project.open(str(latex_project))
        old_md5 = project.get_file_md5("main.tex")
        (latex_project / "main.tex").write_text("changed", encoding="utf-8")

        with pytest.raises(FileConflictError):
            project.apply_whole_files(
                {"main.tex": "new"},
                expected_md5s={"main.tex": old_md5},
            )

        assert (latex_project / "main.tex").read_text(encoding="utf-8") == "changed"


class TestStyleModifySessionStore:
    @pytest.mark.asyncio
    async def test_agent_read_uses_line_range(
        self,
        isolated_sessions,
        open_project,
    ):
        from unittest.mock import MagicMock

        from backend.modules.style_modify.agent import _ToolExecutor

        workspace_id, project = open_project
        store = get_session_store()
        session_id = store.create_session(
            workspace_id,
            project.root_path,
            "read a range",
        )
        executor = _ToolExecutor(session_id, MagicMock())

        text, event = await executor.execute(
            "read_project_file",
            {"file_path": "main.tex", "line_range": [2, 3]},
        )

        assert text.endswith(r"\usepackage{mystyle}" + "\n" + r"\begin{document}")
        assert event["line_range"] == [2, 3]
        assert "start_line" not in event
        assert "end_line" not in event

    def test_stage_proposal_apply_and_undo(
        self,
        isolated_sessions,
        open_project,
        latex_project,
    ):
        workspace_id, _ = open_project
        store = StyleModifySessionStore()
        session_id = store.create_session(
            workspace_id,
            str(latex_project),
            "Make headings blue",
        )

        original = (latex_project / "mystyle.sty").read_text(encoding="utf-8")
        candidate = original.replace("333333", "0077CC")
        staged = store.stage_replace(session_id, "mystyle.sty", candidate)

        assert staged.changed
        assert (latex_project / "mystyle.sty").read_text(encoding="utf-8") == original
        assert "0077CC" in staged.diff

        proposal = store.make_proposal(
            session_id,
            "Changed theme color",
            CompileSummary(status="not_run"),
        )
        assert proposal.revision == 1
        assert proposal.files[0].path == "mystyle.sty"

        operation = store.apply_proposal(session_id, workspace_id, 1)
        assert operation.status == "applied"
        assert (latex_project / "mystyle.sty").read_text(encoding="utf-8") == candidate

        undone = store.undo_operation(operation.operation_id, workspace_id)
        assert undone.status == "undone"
        assert (latex_project / "mystyle.sty").read_text(encoding="utf-8") == original

    def test_apply_refuses_when_real_file_changed(
        self,
        isolated_sessions,
        open_project,
        latex_project,
    ):
        workspace_id, _ = open_project
        store = StyleModifySessionStore()
        session_id = store.create_session(
            workspace_id,
            str(latex_project),
            "Change color",
        )
        original = (latex_project / "mystyle.sty").read_text(encoding="utf-8")
        store.stage_replace(
            session_id,
            "mystyle.sty",
            original.replace("333333", "0077CC"),
        )
        store.make_proposal(
            session_id,
            "Change color",
            CompileSummary(status="not_run"),
        )
        (latex_project / "mystyle.sty").write_text("edited later", encoding="utf-8")

        with pytest.raises(ValueError, match="modified since"):
            store.apply_proposal(session_id, workspace_id, 1)

        assert (latex_project / "mystyle.sty").read_text(encoding="utf-8") == (
            "edited later"
        )

    def test_workspace_isolation(
        self,
        isolated_sessions,
        open_project,
        latex_project,
    ):
        workspace_id, _ = open_project
        store = StyleModifySessionStore()
        session_id = store.create_session(
            workspace_id,
            str(latex_project),
            "test",
        )
        with pytest.raises(ValueError, match="different workspace"):
            store.get_session(session_id, "other-workspace")

    def test_path_guard_rejects_traversal(
        self,
        isolated_sessions,
        open_project,
        latex_project,
    ):
        workspace_id, _ = open_project
        store = StyleModifySessionStore()
        session_id = store.create_session(
            workspace_id,
            str(latex_project),
            "test",
        )
        with pytest.raises(ValueError, match="must not contain"):
            store.stage_replace(session_id, "../outside.tex", "bad")

    @pytest.mark.asyncio
    async def test_service_exposes_session_after_proposal(
        self,
        isolated_sessions,
        open_project,
        latex_project,
    ):
        workspace_id, _ = open_project
        store = get_session_store()
        session_id = store.create_session(
            workspace_id,
            str(latex_project),
            "Change color",
        )
        original = (latex_project / "mystyle.sty").read_text(encoding="utf-8")
        store.stage_replace(
            session_id,
            "mystyle.sty",
            original.replace("333333", "0077CC"),
        )
        store.make_proposal(
            session_id,
            "Change color",
            CompileSummary(status="not_run"),
        )

        service = StyleModifyService()
        data = service.get_agent_session(session_id, workspace_id)

        assert data["proposal"]["revision"] == 1
        assert data["files"][0]["path"] == "mystyle.sty"

        # 对外响应（会话查询 / 应用 / 撤销）不得携带服务器上的项目绝对路径
        assert "project_root" not in data
        applied = await service.apply_agent_proposal(session_id, workspace_id, 1)
        assert "project_root" not in applied
        undone = await service.undo_agent_operation(
            applied["operation_id"], workspace_id
        )
        assert "project_root" not in undone

    def test_unchanged_stage_excluded_from_proposal(
        self,
        isolated_sessions,
        open_project,
        latex_project,
    ):
        workspace_id, _ = open_project
        store = get_session_store()
        session_id = store.create_session(
            workspace_id,
            str(latex_project),
            "Change color",
        )
        original = (latex_project / "mystyle.sty").read_text(encoding="utf-8")
        store.stage_replace(session_id, "mystyle.sty", original)

        proposal = store.make_proposal(
            session_id,
            "No real change",
            CompileSummary(status="not_run"),
        )

        assert proposal.files == []
        assert proposal.blocked_reasons == ["No staged changes"]


class TestShadowCopyIgnore:
    """shadow 复制：应用运行时 data/ 容器仅根层级忽略，嵌套同名目录保留。"""

    def test_nested_data_and_git_dirs(self, workdir):
        from backend.modules.style_modify.session import _copy_project

        root = workdir / "proj"
        root.mkdir()
        (root / "main.tex").write_text(
            r"\documentclass{article}",
            encoding="utf-8",
        )
        (root / "data").mkdir()
        (root / "data" / "root.tex").write_text("x", encoding="utf-8")
        # 根层级非应用运行时目录（如旧版遗留的 plots/）视为普通项目源码保留
        (root / "plots").mkdir()
        (root / "plots" / "root.tex").write_text("x", encoding="utf-8")
        (root / "thesis").mkdir()
        (root / "thesis" / "data").mkdir(parents=True)
        (root / "thesis" / "data" / "nested.tex").write_text(
            "y",
            encoding="utf-8",
        )
        (root / "thesis" / ".git").mkdir()
        (root / "thesis" / ".git" / "obj").write_text("z", encoding="utf-8")

        shadow = root / "_shadow"
        _copy_project(root, shadow)

        assert (shadow / "main.tex").exists()
        assert not (shadow / "data").exists()
        assert (shadow / "plots" / "root.tex").exists()
        assert (shadow / "thesis" / "data" / "nested.tex").exists()
        assert not (shadow / "thesis" / ".git").exists()


class TestStyleModifyAgentRouter:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from main import app

        return TestClient(app)

    def test_agent_run_streams_session_and_done(self, client):
        from unittest.mock import patch

        from backend.modules.style_modify.service import StyleModifyService

        async def fake_run(
            self,
            workspace_id,
            description,
            language="zh",
            *,
            llm=None,
        ):
            yield {"type": "session", "session_id": "sess123"}
            yield {"type": "done", "session_id": "sess123", "revision": 1}

        with patch.object(
            StyleModifyService,
            "agent_run",
            new=fake_run,
        ):
            response = client.post(
                "/api/style-modify/agent/run",
                json={
                    "description": "make headings blue",
                    "workspace_id": "ws-1",
                },
            )

        assert response.status_code == 200
        assert "sess123" in response.text
        assert '"type": "done"' in response.text

    def test_get_agent_session(self, client):
        from unittest.mock import patch

        from backend.modules.style_modify.service import StyleModifyService

        with patch.object(
            StyleModifyService,
            "get_agent_session",
            return_value={"session_id": "sess123", "status": "ready"},
        ):
            response = client.get(
                "/api/style-modify/agent/sessions/sess123",
                params={"workspace_id": "ws-1"},
            )

        assert response.status_code == 200
        assert response.json()["session_id"] == "sess123"


class _FakeToolCall:
    def __init__(self, name: str, arguments: str):
        self.id = "call_x"
        self.type = "function"
        self.function = SimpleNamespace(name=name, arguments=arguments)


class _FakeMessage:
    def __init__(self, tool_calls, content=""):
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self):
        return {
            "role": "assistant",
            "content": self.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in (self.tool_calls or [])
            ],
        }


class _FakeResponse:
    def __init__(self, tool_calls, content=""):
        self.choices = [SimpleNamespace(message=_FakeMessage(tool_calls, content))]

    def __aiter__(self):
        async def _gen():
            from backend.core.llm_client import StreamChunk

            msg = self.choices[0].message
            if msg.content:
                yield StreamChunk(content=msg.content, usage=None)
            if msg.tool_calls:
                yield StreamChunk(content=None, usage=None, tool_calls=msg.tool_calls)

        return _gen()


class TestStyleModifyAgentLifecycle:
    """Agent 循环异常/上限路径回归测试（全部 mock LLM，不访问网络）。"""

    @pytest.mark.asyncio
    async def test_llm_error_leaves_session_deletable(
        self,
        isolated_sessions,
        open_project,
        monkeypatch,
    ):
        workspace_id, project = open_project
        store = get_session_store()
        session_id = store.create_session(
            workspace_id,
            project.root_path,
            "test",
        )

        from backend.modules.style_modify import agent as agent_mod

        async def noop_trace(session_dir, event):
            pass

        monkeypatch.setattr(agent_mod, "append_trace", noop_trace)

        class BoomLLM:
            def chat_stream_with_tools(self, **kwargs):
                raise RuntimeError("boom")

        events = [
            ev
            async for ev in get_style_modify_agent().run(
                session_id,
                workspace_id,
                "test",
                "zh",
                llm=BoomLLM(),
            )
        ]

        assert any(ev["type"] == "error" for ev in events)
        # LLM 异常后会话必须恢复为可管理终态，而不是永久卡在 running
        assert store.load_meta(session_id).status == "ready"
        assert store.delete_session(session_id, workspace_id) is True

    @pytest.mark.asyncio
    async def test_compile_limit_refuses_before_execution(
        self,
        isolated_sessions,
        open_project,
        monkeypatch,
    ):
        workspace_id, project = open_project
        cfg = get_config()
        old_limit = cfg.features.style_modify.agent.max_compile_runs
        cfg.features.style_modify.agent.max_compile_runs = 2
        try:
            store = get_session_store()
            session_id = store.create_session(
                workspace_id,
                project.root_path,
                "test",
            )

            from backend.modules.style_modify import agent as agent_mod
            from backend.modules.style_modify.session import (
                StyleModifySessionStore,
            )

            monkeypatch.setattr(agent_mod, "_tex_toolchain_available", lambda: True)

            async def noop_trace(session_dir, event):
                pass

            monkeypatch.setattr(agent_mod, "append_trace", noop_trace)

            executions = {"n": 0}

            async def fake_compile(self, session_id, main_file=None):
                executions["n"] += 1
                return CompileSummary(
                    status="passed",
                    main_file="main.tex",
                    log="ok",
                )

            monkeypatch.setattr(
                StyleModifySessionStore,
                "compile_staged_project",
                fake_compile,
            )

            class CompileLLM:
                def chat_stream_with_tools(self, **kwargs):
                    return _FakeResponse(
                        [
                            _FakeToolCall(
                                "compile_staged_project",
                                '{"main_file": "main.tex"}',
                            )
                        ]
                    )

            events = [
                ev
                async for ev in get_style_modify_agent().run(
                    session_id,
                    workspace_id,
                    "test",
                    "zh",
                    llm=CompileLLM(),
                )
            ]

            # 上限是硬限制：第 3 次起拒绝执行，而不是先跑再警告
            assert executions["n"] == 2
            assert any(ev["type"] == "warning" for ev in events)
        finally:
            cfg.features.style_modify.agent.max_compile_runs = old_limit

    @pytest.mark.asyncio
    async def test_compile_unavailable_short_circuits_without_failed_warning(
        self,
        isolated_sessions,
        open_project,
        monkeypatch,
    ):
        """工具链缺失时不再真正执行编译，且 not_run 不得误报为编译失败。"""
        workspace_id, project = open_project
        store = get_session_store()
        session_id = store.create_session(
            workspace_id,
            project.root_path,
            "test",
        )
        original = (Path(project.root_path) / "mystyle.sty").read_text(encoding="utf-8")
        store.stage_replace(
            session_id,
            "mystyle.sty",
            original.replace("333333", "1F4E79"),
        )

        from backend.modules.style_modify import agent as agent_mod
        from backend.modules.style_modify.session import (
            StyleModifySessionStore,
        )

        monkeypatch.setattr(agent_mod, "_tex_toolchain_available", lambda: False)

        async def noop_trace(session_dir, event):
            pass

        monkeypatch.setattr(agent_mod, "append_trace", noop_trace)
        executions = {"n": 0}

        async def fake_compile(self, session_id, main_file=None):
            executions["n"] += 1
            return CompileSummary(status="passed")

        monkeypatch.setattr(
            StyleModifySessionStore,
            "compile_staged_project",
            fake_compile,
        )

        class SequenceLLM:
            def __init__(self):
                self.calls = [
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "compile_staged_project",
                                '{"main_file": "main.tex"}',
                            )
                        ]
                    ),
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "finish",
                                '{"summary": "done"}',
                            )
                        ]
                    ),
                ]

            def chat_stream_with_tools(self, **kwargs):
                return self.calls.pop(0)

        events = [
            ev
            async for ev in get_style_modify_agent().run(
                session_id,
                workspace_id,
                "test",
                "zh",
                llm=SequenceLLM(),
            )
        ]

        assert executions["n"] == 0
        assert not any(
            ev["type"] == "warning"
            and "last shadow compilation failed" in ev.get("message", "")
            for ev in events
        )
        assert any(ev["type"] == "proposal" for ev in events)

    @pytest.mark.asyncio
    async def test_finish_refused_when_staged_changed_after_last_compile(
        self,
        isolated_sessions,
        open_project,
        monkeypatch,
    ):
        """最后一次成功编译后又 stage_replace，finish 必须先重新编译。"""
        workspace_id, project = open_project
        store = get_session_store()
        session_id = store.create_session(
            workspace_id,
            project.root_path,
            "test",
        )
        original = (Path(project.root_path) / "mystyle.sty").read_text(encoding="utf-8")

        from backend.modules.style_modify import agent as agent_mod
        from backend.modules.style_modify.session import (
            StyleModifySessionStore,
        )

        monkeypatch.setattr(agent_mod, "_tex_toolchain_available", lambda: True)

        async def noop_trace(session_dir, event):
            pass

        monkeypatch.setattr(agent_mod, "append_trace", noop_trace)
        executions = {"n": 0}

        async def fake_compile(self, session_id, main_file=None):
            executions["n"] += 1
            return CompileSummary(status="passed")

        monkeypatch.setattr(
            StyleModifySessionStore,
            "compile_staged_project",
            fake_compile,
        )

        class SequenceLLM:
            def __init__(self):
                self.calls = [
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "compile_staged_project",
                                '{"main_file": "main.tex"}',
                            )
                        ]
                    ),
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "stage_replace",
                                json.dumps(
                                    {
                                        "file_path": "mystyle.sty",
                                        "content": original.replace("333333", "00539F"),
                                    }
                                ),
                            )
                        ]
                    ),
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "finish",
                                '{"summary": "should be refused"}',
                            )
                        ]
                    ),
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "compile_staged_project",
                                '{"main_file": "main.tex"}',
                            )
                        ]
                    ),
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "finish",
                                '{"summary": "verified after recompile"}',
                            )
                        ]
                    ),
                ]

            def chat_stream_with_tools(self, **kwargs):
                return self.calls.pop(0)

        events = [
            ev
            async for ev in get_style_modify_agent().run(
                session_id,
                workspace_id,
                "test",
                "zh",
                llm=SequenceLLM(),
            )
        ]

        refusals = [
            ev
            for ev in events
            if ev["type"] == "tool_result"
            and ev.get("tool") == "finish"
            and ev.get("ok") is False
        ]
        assert refusals, "finish should be refused when overlay is stale"
        assert any("Finish refused" in ev["summary"] for ev in refusals)
        assert any(ev["type"] == "proposal" for ev in events)
        assert executions["n"] == 2
        staged = store.list_staged_files(session_id)
        assert staged
        assert store.load_meta(session_id).last_verified_md5s == {
            staged[0].path: staged[0].candidate_md5
        }


def test_finish_summary_rejects_unimplemented_style_requirement():
    """不应接受用默认值或未来承诺掩盖未完成的样式需求。"""
    assert StyleModifyAgent._incomplete_finish_reason(
        {"summary": "纸张使用 A4，无需额外指定"}
    )
    assert StyleModifyAgent._incomplete_finish_reason(
        {"summary": "如需设置 margin，可后续添加"}
    )
    assert not StyleModifyAgent._incomplete_finish_reason(
        {"summary": "正文和 label 未变动，编译通过"}
    )

    @pytest.mark.asyncio
    async def test_finish_refused_with_persisted_snapshot_across_runs(
        self,
        isolated_sessions,
        open_project,
        monkeypatch,
    ):
        """上一轮成功编译的快照持久化后，本轮改文件直接 finish 仍须拒绝。"""
        workspace_id, project = open_project
        store = get_session_store()
        session_id = store.create_session(
            workspace_id,
            project.root_path,
            "test",
        )
        original = (Path(project.root_path) / "mystyle.sty").read_text(encoding="utf-8")

        from backend.modules.style_modify import agent as agent_mod
        from backend.modules.style_modify.session import (
            StyleModifySessionStore,
        )

        # 模拟上一轮成功编译了空 overlay。
        meta = store.load_meta(session_id)
        meta.last_verified_md5s = {}
        store.save_meta(session_id, meta)

        monkeypatch.setattr(agent_mod, "_tex_toolchain_available", lambda: True)

        async def noop_trace(session_dir, event):
            pass

        monkeypatch.setattr(agent_mod, "append_trace", noop_trace)
        executions = {"n": 0}

        async def fake_compile(self, session_id, main_file=None):
            executions["n"] += 1
            return CompileSummary(status="passed")

        monkeypatch.setattr(
            StyleModifySessionStore,
            "compile_staged_project",
            fake_compile,
        )

        class SequenceLLM:
            def __init__(self):
                self.calls = [
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "stage_replace",
                                json.dumps(
                                    {
                                        "file_path": "mystyle.sty",
                                        "content": original.replace("333333", "00539F"),
                                    }
                                ),
                            )
                        ]
                    ),
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "finish",
                                '{"summary": "should be refused"}',
                            )
                        ]
                    ),
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "compile_staged_project",
                                '{"main_file": "main.tex"}',
                            )
                        ]
                    ),
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "finish",
                                '{"summary": "verified after recompile"}',
                            )
                        ]
                    ),
                ]

            def chat_stream_with_tools(self, **kwargs):
                return self.calls.pop(0)

        events = [
            ev
            async for ev in get_style_modify_agent().run(
                session_id,
                workspace_id,
                "test",
                "zh",
                llm=SequenceLLM(),
            )
        ]

        refusals = [
            ev
            for ev in events
            if ev["type"] == "tool_result"
            and ev.get("tool") == "finish"
            and ev.get("ok") is False
        ]
        assert refusals, "finish should be refused when overlay is stale across runs"
        assert any("Finish refused" in ev["summary"] for ev in refusals)
        assert any(ev["type"] == "proposal" for ev in events)
        assert executions["n"] == 1
        staged = store.list_staged_files(session_id)
        assert staged
        assert store.load_meta(session_id).last_verified_md5s == {
            staged[0].path: staged[0].candidate_md5
        }

    @pytest.mark.asyncio
    async def test_finish_refused_when_staged_changed_after_failed_compile(
        self,
        isolated_sessions,
        open_project,
        monkeypatch,
    ):
        """编译失败后再次 stage_replace，finish 同样必须重新编译。"""
        workspace_id, project = open_project
        store = get_session_store()
        session_id = store.create_session(
            workspace_id,
            project.root_path,
            "test",
        )
        original = (Path(project.root_path) / "mystyle.sty").read_text(encoding="utf-8")

        from backend.modules.style_modify import agent as agent_mod
        from backend.modules.style_modify.session import (
            StyleModifySessionStore,
        )

        monkeypatch.setattr(agent_mod, "_tex_toolchain_available", lambda: True)

        async def noop_trace(session_dir, event):
            pass

        monkeypatch.setattr(agent_mod, "append_trace", noop_trace)
        executions = {"n": 0}

        async def fake_compile(self, session_id, main_file=None):
            executions["n"] += 1
            if executions["n"] == 2:
                return CompileSummary(status="failed", log="boom")
            return CompileSummary(status="passed")

        monkeypatch.setattr(
            StyleModifySessionStore,
            "compile_staged_project",
            fake_compile,
        )

        class SequenceLLM:
            def __init__(self):
                self.calls = [
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "compile_staged_project",
                                '{"main_file": "main.tex"}',
                            )
                        ]
                    ),
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "stage_replace",
                                json.dumps(
                                    {
                                        "file_path": "mystyle.sty",
                                        "content": original.replace("333333", "00539F"),
                                    }
                                ),
                            )
                        ]
                    ),
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "compile_staged_project",
                                '{"main_file": "main.tex"}',
                            )
                        ]
                    ),
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "stage_replace",
                                json.dumps(
                                    {
                                        "file_path": "mystyle.sty",
                                        "content": original.replace("333333", "0077CC"),
                                    }
                                ),
                            )
                        ]
                    ),
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "finish",
                                '{"summary": "should be refused"}',
                            )
                        ]
                    ),
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "compile_staged_project",
                                '{"main_file": "main.tex"}',
                            )
                        ]
                    ),
                    _FakeResponse(
                        [
                            _FakeToolCall(
                                "finish",
                                '{"summary": "verified after recompile"}',
                            )
                        ]
                    ),
                ]

            def chat_stream_with_tools(self, **kwargs):
                return self.calls.pop(0)

        events = [
            ev
            async for ev in get_style_modify_agent().run(
                session_id,
                workspace_id,
                "test",
                "zh",
                llm=SequenceLLM(),
            )
        ]

        refusals = [
            ev
            for ev in events
            if ev["type"] == "tool_result"
            and ev.get("tool") == "finish"
            and ev.get("ok") is False
        ]
        assert (
            refusals
        ), "finish should be refused when overlay changed after failed compile"
        assert any("Finish refused" in ev["summary"] for ev in refusals)
        assert any(ev["type"] == "proposal" for ev in events)
        assert executions["n"] == 3
        staged = store.list_staged_files(session_id)
        assert staged
        assert store.load_meta(session_id).last_verified_md5s == {
            staged[0].path: staged[0].candidate_md5
        }


class TestToolFailureTextSanitization:
    """工具异常文本脱敏：多用户收敛为相对路径，单用户保留原文。"""

    @staticmethod
    def _make_meta(project_root: str) -> SessionMeta:
        return SessionMeta(
            session_id="deadbeefcafe",
            workspace_id="ws-1",
            project_root=project_root,
            project_fingerprint="fp",
            status="ready",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )

    @pytest.fixture
    def multi_user_enabled(self):
        cfg = get_config()
        old = cfg.multi_user.enabled
        cfg.multi_user.enabled = True
        yield
        cfg.multi_user.enabled = old

    def test_single_user_keeps_raw_exception(self, latex_project):
        exc = FileNotFoundError(
            2,
            f"No such file or directory: '{latex_project / 'main.tex'}'",
        )
        text = _tool_failure_text(exc, self._make_meta(str(latex_project)))
        assert text == f"Tool execution failed: {exc}"

    def test_multi_user_rewrites_project_root_prefix(
        self,
        latex_project,
        multi_user_enabled,
    ):
        leak_path = latex_project / "sub" / "main.tex"
        exc = FileNotFoundError(2, f"No such file or directory: '{leak_path}'")
        text = _tool_failure_text(exc, self._make_meta(str(latex_project)))

        assert "Tool execution failed" in text
        assert "./sub/main.tex" in text
        assert str(latex_project) not in text

    def test_multi_user_degrades_without_resolvable_roots(self, multi_user_enabled):
        # project_root 为空且 session id 不存在（session_dir 解析失败）：
        # 两个脱敏锚点都不可用时降级为无详情稳定文案
        exc = RuntimeError(f"boom at {Path(tempfile.gettempdir()) / 'secret'}")
        text = _tool_failure_text(exc, self._make_meta(""))

        assert text == "Tool execution failed."


class TestStyleModifyAgentErrorPaths:
    """Agent 循环与路由兜底的异常收敛回归（不泄漏异常原文）。"""

    @pytest.mark.asyncio
    async def test_llm_stream_error_message_is_stable(
        self,
        isolated_sessions,
        open_project,
        monkeypatch,
    ):
        workspace_id, project = open_project
        store = get_session_store()
        session_id = store.create_session(workspace_id, project.root_path, "test")

        from backend.modules.style_modify import agent as agent_mod

        async def noop_trace(session_dir, event):
            pass

        monkeypatch.setattr(agent_mod, "append_trace", noop_trace)

        class BoomLLM:
            def chat_stream_with_tools(self, **kwargs):
                raise RuntimeError("provider url http://internal.example/v1 boom")

        events = [
            ev
            async for ev in get_style_modify_agent().run(
                session_id,
                workspace_id,
                "test",
                "zh",
                llm=BoomLLM(),
            )
        ]
        errors = [ev for ev in events if ev["type"] == "error"]
        assert errors
        assert all(ev["message"] == "internal_error" for ev in errors)

    @pytest.mark.asyncio
    async def test_tool_exception_becomes_failure_tool_result(
        self,
        isolated_sessions,
        open_project,
        monkeypatch,
    ):
        """工具执行抛异常时不中断整轮：转为 ok=False 的 tool_result 事件。"""
        workspace_id, project = open_project
        store = get_session_store()
        session_id = store.create_session(workspace_id, project.root_path, "test")

        from backend.modules.style_modify import agent as agent_mod

        async def noop_trace(session_dir, event):
            pass

        monkeypatch.setattr(agent_mod, "append_trace", noop_trace)

        async def raise_oserror(self, tool_name, tool_args):
            raise OSError(
                f"[Errno 2] No such file or directory: '{project.root_path}/main.tex'"
            )

        monkeypatch.setattr(agent_mod._ToolExecutor, "execute", raise_oserror)

        class ToolLLM:
            def chat_stream_with_tools(self, **kwargs):
                return _FakeResponse(
                    [
                        _FakeToolCall(
                            "read_project_file",
                            '{"file_path": "main.tex"}',
                        )
                    ]
                )

        events = [
            ev
            async for ev in get_style_modify_agent().run(
                session_id,
                workspace_id,
                "test",
                "zh",
                llm=ToolLLM(),
            )
        ]
        failures = [
            ev for ev in events if ev["type"] == "tool_result" and ev.get("ok") is False
        ]
        assert failures
        assert "Tool execution failed" in failures[0]["summary"]


class TestStyleModifyRouterErrorFallbacks:
    """路由 SSE 与 500 兜底：异常原文不回传客户端。"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from main import app

        return TestClient(app)

    def test_agent_run_sse_conceals_unexpected_exception(self, client):
        async def boom(self, workspace_id, description, language="zh", *, llm=None):
            raise RuntimeError("secret /tmp/latexcellence-hidden/main.tex failed")
            yield  # pragma: no cover — 仅为使其成为异步生成器

        with patch.object(StyleModifyService, "agent_run", new=boom):
            response = client.post(
                "/api/style-modify/agent/run",
                json={"description": "make headings blue", "workspace_id": "ws-1"},
            )

        assert response.status_code == 200
        assert "internal_error" in response.text
        assert "latexcellence-hidden" not in response.text

    def test_agent_refine_sse_conceals_unexpected_exception(self, client):
        async def boom(
            self, session_id, workspace_id, message, language="zh", *, llm=None
        ):
            raise RuntimeError("secret /tmp/latexcellence-hidden/main.tex failed")
            yield  # pragma: no cover

        with patch.object(StyleModifyService, "agent_refine", new=boom):
            response = client.post(
                "/api/style-modify/agent/sessions/sess123456/refine",
                json={"workspace_id": "ws-1", "message": "more"},
            )

        assert response.status_code == 200
        assert "internal_error" in response.text
        assert "latexcellence-hidden" not in response.text

    def test_modify_maps_unexpected_exception_to_internal_error(self, client):
        class ExplodingError(Exception):
            pass

        async def boom(self, latex, description):
            raise ExplodingError("secret /tmp/latexcellence-hidden/main.tex")

        with patch.object(StyleModifyService, "modify", new=boom):
            response = client.post(
                "/api/style-modify/modify",
                json={"latex": "\\documentclass{article}", "description": "x"},
            )

        assert response.status_code == 500
        assert response.json()["detail"] == "internal_error"

    def test_apply_maps_runtime_error_to_internal_error(self, client):
        async def boom(self, session_id, workspace_id, revision):
            raise RuntimeError(
                "write failed at /tmp/latexcellence-hidden/overlay/main.tex"
            )

        with patch.object(StyleModifyService, "apply_agent_proposal", new=boom):
            response = client.post(
                "/api/style-modify/agent/sessions/sess123456/apply",
                json={"revision": 1, "workspace_id": "ws-1"},
            )

        assert response.status_code == 500
        assert response.json()["detail"] == "internal_error"

    def test_undo_maps_runtime_error_to_internal_error(self, client):
        async def boom(self, operation_id, workspace_id):
            raise RuntimeError(
                "restore failed at /tmp/latexcellence-hidden/before/main.tex"
            )

        with patch.object(StyleModifyService, "undo_agent_operation", new=boom):
            response = client.post(
                "/api/style-modify/agent/operations/op123456/undo",
                json={"workspace_id": "ws-1"},
            )

        assert response.status_code == 500
        assert response.json()["detail"] == "internal_error"
