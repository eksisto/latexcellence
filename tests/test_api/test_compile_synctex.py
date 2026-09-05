"""
compile 模块 SyncTeX 正反向同步测试（editor v3 阶段一）。

覆盖内容：
    - run_tex_compile：-synctex=1 仅在显式请求且非 PDF/A 时追加
      （既有调用方 synctex 默认 False，编译命令零变化）
    - compile_latex：synctex 透传 / pdfa 组合时忽略（响应如实回传 False）
    - synctex view / edit 输出解析纯函数
    - /api/compile/synctex/forward|backward HTTP 端点（Mock 沙箱执行，
      严禁真实 synctex 二进制 / docker / 网络）：
      成功解析、not_found、file_path 越界拒绝、多用户输出脱敏、
      响应体不含绝对路径
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.modules.compile.service import (
    _relativize_synctex_input,
    compile_latex,
    parse_synctex_edit_result,
    parse_synctex_view_records,
)
from backend.utils.sandbox.models import ExecResult
from backend.utils.tex_compile import TexCompileResult, run_tex_compile

# ── 共享 fixture / 工具 ───────────────────────────────────────────────────


def make_fake_runner(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
    timed_out: bool = False,
    oom_killed: bool = False,
) -> MagicMock:
    """构造带 execute() 的假 SandboxRunner（Mock 沙箱执行的统一接缝）。"""
    fake = MagicMock()
    fake.execute = AsyncMock(
        return_value=ExecResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            oom_killed=oom_killed,
            backend="local",
        )
    )
    return fake


@pytest.fixture
def synctex_project():
    """带 main.tex / main.pdf / chapters/intro.tex 的 mock 项目。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "main.tex").write_text(
            r"\documentclass{article}\begin{document}hi\end{document}",
            encoding="utf-8",
        )
        chapters = root / "chapters"
        chapters.mkdir()
        (chapters / "intro.tex").write_text("intro", encoding="utf-8")
        (root / "main.pdf").write_bytes(b"%PDF-1.4 fake pdf")
        pm = MagicMock()
        pm.is_open = True
        pm.root_path = str(root)
        yield pm, root


@pytest.fixture
def single_user_cfg():
    """multi_user.enabled=False 的确定性配置（service.get_config 接缝）。"""
    cfg = MagicMock()
    cfg.multi_user.enabled = False
    return cfg


# ── run_tex_compile：-synctex=1 追加规则 ─────────────────────────────────


class TestRunTexCompileSynctex:
    """latexmk 基础参数追加规则（与 extra_args 同级）。"""

    def _patch_exec(self, fake):
        return (
            patch(
                "backend.utils.tex_compile.get_sandbox_runner",
                return_value=fake,
            ),
            patch(
                "backend.utils.tex_compile._resolve_use_texfot",
                return_value=False,
            ),
        )

    @pytest.mark.asyncio
    async def test_default_off_no_synctex_flag(self, tmp_path):
        """既有调用方（synctex 默认 False）编译命令零变化。"""
        (tmp_path / "main.tex").write_text("x", encoding="utf-8")
        fake = make_fake_runner(stdout="ok")
        p_runner, p_texfot = self._patch_exec(fake)
        with p_runner, p_texfot:
            await run_tex_compile("main.tex", tmp_path, 10, engine="pdflatex")

        command = fake.execute.await_args.args[0].command
        assert command[0] == "latexmk"
        assert "-synctex=1" not in command

    @pytest.mark.asyncio
    async def test_synctex_true_appends_flag(self, tmp_path):
        """synctex=True 时在基础参数追加 -synctex=1（extra_args 之前）。"""
        (tmp_path / "main.tex").write_text("x", encoding="utf-8")
        fake = make_fake_runner(stdout="ok")
        p_runner, p_texfot = self._patch_exec(fake)
        with p_runner, p_texfot:
            await run_tex_compile(
                "main.tex", tmp_path, 10, engine="pdflatex", synctex=True
            )

        command = fake.execute.await_args.args[0].command
        assert command[:4] == [
            "latexmk",
            "-pdf",
            "-interaction=nonstopmode",
            "-synctex=1",
        ]

    @pytest.mark.asyncio
    async def test_synctex_ignored_with_pdfa(self, tmp_path):
        """PDF/A 在临时目录编译后仅回拷 PDF，-synctex=1 不生效。"""
        (tmp_path / "main.tex").write_text(
            r"\documentclass{article}\begin{document}hi\end{document}",
            encoding="utf-8",
        )
        fake = make_fake_runner(stdout="ok")
        p_runner, p_texfot = self._patch_exec(fake)
        with p_runner, p_texfot:
            await run_tex_compile(
                "main.tex",
                tmp_path,
                10,
                engine="pdflatex",
                pdfa=True,
                synctex=True,
            )

        spec = fake.execute.await_args.args[0]
        assert "-synctex=1" not in spec.command
        # PDF/A 在临时目录编译（.synctex.gz 不会回项目目录）
        assert spec.cwd != str(tmp_path)


# ── compile_latex：synctex 透传 / pdfa 组合忽略 ──────────────────────────


class TestCompileLatexSynctex:
    """service 层 synctex 字段语义（editor v3 决策 Q3）。"""

    @pytest.fixture
    def deterministic_cfg(self):
        cfg = MagicMock()
        cfg.multi_user.enabled = False
        cfg.features.compile.latexmk_timeout = 10
        cfg.features.compile.engine = "pdflatex"
        cfg.features.compile.pdfa = False
        return cfg

    @pytest.mark.asyncio
    async def test_synctex_true_passthrough(self, synctex_project, deterministic_cfg):
        pm, _root = synctex_project
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=pm,
        ):
            with patch(
                "backend.modules.compile.service.get_config",
                return_value=deterministic_cfg,
            ):
                with patch(
                    "backend.modules.compile.service.run_tex_compile",
                    new_callable=AsyncMock,
                    return_value=TexCompileResult(
                        success=True, filtered_output="", raw_output=""
                    ),
                ) as mock_run:
                    result = await compile_latex("main.tex", "ws", synctex=True)

        assert result.synctex is True
        assert mock_run.await_args.kwargs["synctex"] is True

    @pytest.mark.asyncio
    async def test_synctex_ignored_with_pdfa(self, synctex_project, deterministic_cfg):
        pm, _root = synctex_project
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=pm,
        ):
            with patch(
                "backend.modules.compile.service.get_config",
                return_value=deterministic_cfg,
            ):
                with patch(
                    "backend.modules.compile.service.run_tex_compile",
                    new_callable=AsyncMock,
                    return_value=TexCompileResult(
                        success=True, filtered_output="", raw_output=""
                    ),
                ) as mock_run:
                    result = await compile_latex(
                        "main.tex", "ws", synctex=True, pdfa=True
                    )

        assert result.synctex is False
        assert mock_run.await_args.kwargs["synctex"] is False

    @pytest.mark.asyncio
    async def test_default_synctex_false(self, synctex_project, deterministic_cfg):
        """既有调用方（编译页 / git_diff）不传 synctex → False。"""
        pm, _root = synctex_project
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=pm,
        ):
            with patch(
                "backend.modules.compile.service.get_config",
                return_value=deterministic_cfg,
            ):
                with patch(
                    "backend.modules.compile.service.run_tex_compile",
                    new_callable=AsyncMock,
                    return_value=TexCompileResult(
                        success=True, filtered_output="", raw_output=""
                    ),
                ) as mock_run:
                    result = await compile_latex("main.tex", "ws")

        assert result.synctex is False
        assert mock_run.await_args.kwargs["synctex"] is False


# ── 解析纯函数 ───────────────────────────────────────────────────────────


class TestParseSynctexViewRecords:
    """synctex view 输出解析。"""

    VIEW_OUTPUT = (
        "SyncTeX result begin\n"
        "Output:main.pdf\n"
        "Input:./chapters/intro.tex\n"
        "{Page:1\n"
        "x:113.811016\n"
        "y:104.411476\n"
        "w:324.176788\n"
        "h:10.448627\n"
        "}\n"
        "[Page:2\n"
        "x:120\n"
        "y:130.5pt\n"
        "w:200\n"
        "h:11\n"
        "]\n"
        "SyncTeX result end\n"
    )

    def test_parses_hbox_and_vbox_records(self):
        records = parse_synctex_view_records(self.VIEW_OUTPUT)
        assert len(records) == 2
        assert records[0].page == 1
        assert records[0].x == pytest.approx(113.811016)
        assert records[0].y == pytest.approx(104.411476)
        assert records[0].w == pytest.approx(324.176788)
        assert records[0].h == pytest.approx(10.448627)
        # 垂直盒 [Page:N 与 pt 单位后缀同样命中
        assert records[1].page == 2
        assert records[1].y == pytest.approx(130.5)

    def test_incomplete_record_dropped(self):
        # 未闭合（缺 }）与缺 y 坐标的记录整体丢弃
        output = (
            "{Page:1\nx:10\ny:20\nw:5\nh:6\n}\n"
            "{Page:2\nx:10\nw:5\nh:6\n}\n"
            "{Page:3\nx:1\ny:2\nw:3\nh:4\n"
        )
        records = parse_synctex_view_records(output)
        assert len(records) == 1
        assert records[0].page == 1

    def test_empty_output_returns_empty(self):
        assert parse_synctex_view_records("") == []


class TestParseSynctexEditResult:
    """synctex edit 输出解析。"""

    EDIT_OUTPUT = (
        "SyncTeX result begin\n"
        "Input:./chapters/intro.tex\n"
        "Line:56\n"
        "Column:3\n"
        "Offset:10\n"
        "Context:\n"
        "SyncTeX result end\n"
    )

    def test_parses_input_line_column(self):
        assert parse_synctex_edit_result(self.EDIT_OUTPUT) == (
            "./chapters/intro.tex",
            56,
            3,
        )

    def test_missing_input_returns_none(self):
        output = "SyncTeX result begin\nLine:5\nColumn:0\nSyncTeX result end\n"
        assert parse_synctex_edit_result(output) is None

    def test_unknown_line_returns_none(self):
        # synctex 以 -1 标记未知行 → 视为未命中
        output = "Input:main.tex\nLine:-1\nColumn:0\n"
        assert parse_synctex_edit_result(output) is None

    def test_missing_column_defaults_zero(self):
        output = "Input:main.tex\nLine:7\n"
        assert parse_synctex_edit_result(output) == ("main.tex", 7, 0)


class TestRelativizeSynctexInput:
    """synctex Input 路径归一（响应绝不携带绝对路径 / 项目外路径）。"""

    def test_relative_path_normalized(self):
        assert (
            _relativize_synctex_input("./chapters/intro.tex", Path("/proj"))
            == "chapters/intro.tex"
        )

    def test_absolute_inside_root(self, tmp_path):
        sub = tmp_path / "chapters"
        sub.mkdir()
        assert (
            _relativize_synctex_input(str(sub / "intro.tex"), tmp_path)
            == "chapters/intro.tex"
        )

    def test_absolute_outside_root_rejected(self, tmp_path):
        assert (
            _relativize_synctex_input(str(tmp_path.parent / "evil.tex"), tmp_path)
            is None
        )

    def test_relative_traversal_rejected(self):
        assert _relativize_synctex_input("../escape.tex", Path("/proj")) is None

    def test_empty_rejected(self):
        assert _relativize_synctex_input("  ", Path("/proj")) is None


# ── HTTP 端点（Mock 沙箱执行）────────────────────────────────────────────


class TestSynctexEndpoints:
    """/api/compile/synctex/forward|backward 端点测试。"""

    @pytest.fixture
    def client(self):
        from main import app

        return TestClient(app)

    def test_forward_success(self, client, synctex_project):
        pm, root = synctex_project
        view_output = (
            "SyncTeX result begin\n"
            "Output:main.pdf\n"
            "{Page:1\n"
            "x:113.811016\n"
            "y:104.411476\n"
            "w:324.176788\n"
            "h:10.448627\n"
            "}\n"
            "SyncTeX result end\n"
        )
        fake = make_fake_runner(stdout=view_output)
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=pm,
        ):
            with patch(
                "backend.modules.compile.service.get_sandbox_runner",
                return_value=fake,
            ):
                response = client.post(
                    "/api/compile/synctex/forward?workspace_id=ws",
                    json={
                        "main_file": "main.tex",
                        "file_path": "chapters/intro.tex",
                        "line": 12,
                        "col": 0,
                    },
                )

        assert response.status_code == 200
        data = response.json()
        assert len(data["records"]) == 1
        assert data["records"][0]["page"] == 1
        assert data["records"][0]["x"] == pytest.approx(113.811016)

        # 沙箱执行契约：synctex view 经 tex 镜像在项目根目录执行，零写盘
        spec = fake.execute.await_args.args[0]
        assert spec.command[0] == "synctex"
        assert spec.command[1] == "view"
        assert "12:0:chapters/intro.tex" in spec.command
        assert spec.command[spec.command.index("-o") + 1] == "main.pdf"
        assert spec.image_kind == "tex"
        assert spec.cwd == str(root)

        # 响应体不含绝对路径（多用户脱敏约束）
        assert str(root) not in response.text

    def test_forward_empty_records_returns_200(self, client, synctex_project):
        pm, _root = synctex_project
        fake = make_fake_runner(stdout="SyncTeX result begin\nSyncTeX result end\n")
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=pm,
        ):
            with patch(
                "backend.modules.compile.service.get_sandbox_runner",
                return_value=fake,
            ):
                response = client.post(
                    "/api/compile/synctex/forward?workspace_id=ws",
                    json={
                        "main_file": "main.tex",
                        "file_path": "chapters/intro.tex",
                        "line": 999,
                    },
                )

        assert response.status_code == 200
        assert response.json()["records"] == []

    def test_forward_file_path_traversal_rejected(self, client, synctex_project):
        """file_path 越界拒绝（resolve_project_path 唯一入口），不执行命令。"""
        pm, _root = synctex_project
        fake = make_fake_runner(stdout="should not run")
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=pm,
        ):
            with patch(
                "backend.modules.compile.service.get_sandbox_runner",
                return_value=fake,
            ):
                response = client.post(
                    "/api/compile/synctex/forward?workspace_id=ws",
                    json={
                        "main_file": "main.tex",
                        "file_path": "../escape.tex",
                        "line": 1,
                    },
                )

        assert response.status_code == 400
        fake.execute.assert_not_awaited()

    def test_forward_file_path_suffix_rejected(self, client, synctex_project):
        """file_path 扩展名白名单外（仅 .tex/.bib/.sty/.cls 可定位）拒绝。"""
        pm, root = synctex_project
        (root / "notes.json").write_text("{}", encoding="utf-8")
        fake = make_fake_runner(stdout="should not run")
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=pm,
        ):
            with patch(
                "backend.modules.compile.service.get_sandbox_runner",
                return_value=fake,
            ):
                response = client.post(
                    "/api/compile/synctex/forward?workspace_id=ws",
                    json={
                        "main_file": "main.tex",
                        "file_path": "notes.json",
                        "line": 1,
                    },
                )

        assert response.status_code == 400
        fake.execute.assert_not_awaited()

    def test_forward_pdf_missing_returns_404(self, client, synctex_project):
        pm, root = synctex_project
        (root / "main.pdf").unlink()
        fake = make_fake_runner()
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=pm,
        ):
            with patch(
                "backend.modules.compile.service.get_sandbox_runner",
                return_value=fake,
            ):
                response = client.post(
                    "/api/compile/synctex/forward?workspace_id=ws",
                    json={
                        "main_file": "main.tex",
                        "file_path": "chapters/intro.tex",
                        "line": 1,
                    },
                )

        assert response.status_code == 404
        assert "PDF not found" in response.json()["detail"]

    def test_forward_no_project_returns_400(self, client):
        pm = MagicMock()
        pm.is_open = False
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=pm,
        ):
            response = client.post(
                "/api/compile/synctex/forward?workspace_id=ws",
                json={"main_file": "main.tex", "file_path": "main.tex", "line": 1},
            )

        assert response.status_code == 400

    def test_forward_rejects_invalid_line(self, client, synctex_project):
        """行号须 >= 1（1 基），0 / 负数 422。"""
        response = client.post(
            "/api/compile/synctex/forward?workspace_id=ws",
            json={
                "main_file": "main.tex",
                "file_path": "chapters/intro.tex",
                "line": 0,
            },
        )
        assert response.status_code == 422

    def test_forward_rejects_unknown_fields(self, client, synctex_project):
        """extra="forbid"：请求体未知字段 422。"""
        response = client.post(
            "/api/compile/synctex/forward?workspace_id=ws",
            json={
                "main_file": "main.tex",
                "file_path": "chapters/intro.tex",
                "line": 1,
                "evil": "field",
            },
        )
        assert response.status_code == 422

    def test_backward_success(self, client, synctex_project):
        pm, root = synctex_project
        edit_output = (
            "SyncTeX result begin\n"
            "Input:./chapters/intro.tex\n"
            "Line:56\n"
            "Column:3\n"
            "Offset:10\n"
            "Context:\n"
            "SyncTeX result end\n"
        )
        fake = make_fake_runner(stdout=edit_output)
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=pm,
        ):
            with patch(
                "backend.modules.compile.service.get_sandbox_runner",
                return_value=fake,
            ):
                response = client.post(
                    "/api/compile/synctex/backward?workspace_id=ws",
                    json={
                        "main_file": "main.tex",
                        "page": 2,
                        "x": 150.5,
                        "y": 200.25,
                    },
                )

        assert response.status_code == 200
        data = response.json()
        assert data["found"] is True
        assert data["file_path"] == "chapters/intro.tex"
        assert data["line"] == 56
        assert data["col"] == 3

        spec = fake.execute.await_args.args[0]
        assert spec.command[:2] == ["synctex", "edit"]
        assert "2:150.5:200.25:main.pdf" in spec.command
        assert spec.image_kind == "tex"
        assert spec.cwd == str(root)
        assert str(root) not in response.text

    def test_backward_absolute_input_inside_root_relativized(
        self, client, synctex_project
    ):
        """单用户本地模式：synctex 输出绝对路径（项目内）→ 归一为相对路径。"""
        pm, root = synctex_project
        edit_output = f"Input:{root}/chapters/intro.tex\nLine:8\nColumn:0\n"
        fake = make_fake_runner(stdout=edit_output)
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=pm,
        ):
            with patch(
                "backend.modules.compile.service.get_sandbox_runner",
                return_value=fake,
            ):
                response = client.post(
                    "/api/compile/synctex/backward?workspace_id=ws",
                    json={"main_file": "main.tex", "page": 1, "x": 10, "y": 10},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["found"] is True
        assert data["file_path"] == "chapters/intro.tex"
        assert str(root) not in response.text

    def test_backward_outside_root_treated_as_not_found(self, client, synctex_project):
        """定位到项目外文件（texmf 系统文件等）→ 不泄露路径，按未命中处理。"""
        pm, _root = synctex_project
        edit_output = "Input:/usr/local/texlive/texmf/article.cls\nLine:5\nColumn:0\n"
        fake = make_fake_runner(stdout=edit_output)
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=pm,
        ):
            with patch(
                "backend.modules.compile.service.get_sandbox_runner",
                return_value=fake,
            ):
                response = client.post(
                    "/api/compile/synctex/backward?workspace_id=ws",
                    json={"main_file": "main.tex", "page": 1, "x": 10, "y": 10},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["found"] is False
        assert "texlive" not in response.text

    def test_backward_not_found(self, client, synctex_project):
        """synctex edit 未命中 → 200 + found=False（合法空结果）。"""
        pm, _root = synctex_project
        fake = make_fake_runner(stdout="SyncTeX result begin\nSyncTeX result end\n")
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=pm,
        ):
            with patch(
                "backend.modules.compile.service.get_sandbox_runner",
                return_value=fake,
            ):
                response = client.post(
                    "/api/compile/synctex/backward?workspace_id=ws",
                    json={"main_file": "main.tex", "page": 9, "x": 1, "y": 1},
                )

        assert response.status_code == 200
        assert response.json()["found"] is False

    def test_backward_multiline_output_sanitized(self, client, synctex_project):
        """多用户模式：synctex 输出中的服务器绝对路径先脱敏再解析。"""
        pm, root = synctex_project
        cfg = MagicMock()
        cfg.multi_user.enabled = True
        edit_output = f"Input:{root}/chapters/intro.tex\nLine:21\nColumn:0\n"
        fake = make_fake_runner(stdout=edit_output)
        with patch(
            "backend.modules.compile.service.get_project_manager_for_workspace",
            return_value=pm,
        ):
            with patch(
                "backend.modules.compile.service.get_sandbox_runner",
                return_value=fake,
            ):
                with patch(
                    "backend.modules.compile.service.get_config", return_value=cfg
                ):
                    response = client.post(
                        "/api/compile/synctex/backward?workspace_id=ws",
                        json={"main_file": "main.tex", "page": 1, "x": 5, "y": 5},
                    )

        assert response.status_code == 200
        data = response.json()
        assert data["found"] is True
        # 脱敏后的 ./ 前缀路径归一为相对路径
        assert data["file_path"] == "chapters/intro.tex"
        assert str(root) not in response.text

    def test_backward_rejects_invalid_page(self, client, synctex_project):
        response = client.post(
            "/api/compile/synctex/backward?workspace_id=ws",
            json={"main_file": "main.tex", "page": 0, "x": 1, "y": 1},
        )
        assert response.status_code == 422

    def test_backward_non_finite_coordinates_degrade_to_not_found(
        self, client, synctex_project
    ):
        """NaN/±inf 坐标不做 422 拒绝（校验失败的 input 会让 422 响应体
        无法 JSON 序列化），而是放行给 synctex 查询自然落空 → found=false
        的合法空结果，响应体不含非有限值。"""
        pm, _root = synctex_project
        fake = make_fake_runner(stdout="SyncTeX result begin\nSyncTeX result end\n")
        for coords in (
            '"page": 1, "x": NaN, "y": 1',
            '"page": 1, "x": 1, "y": Infinity',
        ):
            with patch(
                "backend.modules.compile.service.get_project_manager_for_workspace",
                return_value=pm,
            ):
                with patch(
                    "backend.modules.compile.service.get_sandbox_runner",
                    return_value=fake,
                ):
                    # Python json.loads 默认接受 NaN/Infinity 字面量
                    response = client.post(
                        "/api/compile/synctex/backward?workspace_id=ws",
                        content=f'{{"main_file": "main.tex", {coords}}}'.encode(
                            "utf-8"
                        ),
                        headers={"Content-Type": "application/json"},
                    )

            assert response.status_code == 200
            data = response.json()
            assert data["found"] is False
            assert "NaN" not in response.text
            assert "Infinity" not in response.text
