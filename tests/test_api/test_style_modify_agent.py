"""style_modify Agent 会话、候选 overlay 与批量事务测试。"""

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.config import get_config
from backend.core.project_manager import FileConflictError, LatexProject
from backend.modules.style_modify.agent import get_style_modify_agent
from backend.modules.style_modify.models import CompileSummary
from backend.modules.style_modify.session import (
    StyleModifySessionStore,
    get_session_store,
)
from backend.modules.style_modify.service import StyleModifyService
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

    def test_service_exposes_session_after_proposal(
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

        data = StyleModifyService().get_agent_session(session_id, workspace_id)

        assert data["proposal"]["revision"] == 1
        assert data["files"][0]["path"] == "mystyle.sty"

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
    """shadow 复制：应用运行时目录仅根层级忽略，嵌套同名目录保留。"""

    def test_nested_plot_and_git_dirs(self, workdir):
        from backend.modules.style_modify.session import _copy_project

        root = workdir / "proj"
        root.mkdir()
        (root / "main.tex").write_text(
            r"\documentclass{article}",
            encoding="utf-8",
        )
        (root / "plots").mkdir()
        (root / "plots" / "root.tex").write_text("x", encoding="utf-8")
        (root / "thesis").mkdir()
        (root / "thesis" / "plots").mkdir(parents=True)
        (root / "thesis" / "plots" / "nested.tex").write_text(
            "y",
            encoding="utf-8",
        )
        (root / "thesis" / ".git").mkdir()
        (root / "thesis" / ".git" / "obj").write_text("z", encoding="utf-8")

        shadow = root / "_shadow"
        _copy_project(root, shadow)

        assert (shadow / "main.tex").exists()
        assert not (shadow / "plots").exists()
        assert (shadow / "thesis" / "plots" / "nested.tex").exists()
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
    def __init__(self, tool_calls):
        self.content = ""
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
    def __init__(self, tool_calls):
        self.choices = [SimpleNamespace(message=_FakeMessage(tool_calls))]


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
            async def chat_with_tools(self, **kwargs):
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
                async def chat_with_tools(self, **kwargs):
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
