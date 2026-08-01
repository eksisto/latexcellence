"""plot_gen 沙箱执行器测试。"""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.core.config import get_config
from backend.modules.plot_gen.executor import (
    PlotExecutor,
    _check_python_syntax,
    _precheck_tikz,
    is_internal_rel_path,
)


def _load_cfg(tmp_path, monkeypatch):
    cfg = get_config()
    monkeypatch.setattr(cfg.features.plot_gen, "output_dir", str(tmp_path / "plots"))
    monkeypatch.setattr(cfg.features.plot_gen, "agent_max_files", 10)
    monkeypatch.setattr(cfg.features.plot_gen, "agent_max_file_size_kb", 64)
    monkeypatch.setattr(cfg.features.plot_gen, "sandbox_exec_timeout", 10)
    return cfg


def test_write_and_run_script(tmp_path, monkeypatch):
    _load_cfg(tmp_path, monkeypatch)
    sandbox = tmp_path / "session"
    (sandbox / "scripts").mkdir(parents=True)
    (sandbox / "artifacts").mkdir(parents=True)
    executor = PlotExecutor(sandbox, get_config())
    text, event = asyncio.run(
        executor.execute(
            "write_file",
            {
                "file_path": "scripts/plot.py",
                "content": "from pathlib import Path\nPath('artifacts/out.txt').write_text('ok', encoding='utf-8')\n",
            },
        )
    )
    assert event["ok"] is True
    result_text, result_event = asyncio.run(
        executor.execute("python_sandbox", {"script_name": "scripts/plot.py"})
    )
    assert result_event["success"] is True
    assert any(a["path"].endswith("out.txt") for a in result_event["artifacts"])


def test_path_traversal_rejected(tmp_path, monkeypatch):
    _load_cfg(tmp_path, monkeypatch)
    sandbox = tmp_path / "session"
    executor = PlotExecutor(sandbox, get_config())
    text, event = asyncio.run(
        executor.execute(
            "write_file",
            {"file_path": "../escape.txt", "content": "x"},
        )
    )
    assert event["ok"] is False


def _write_sandbox_script(sandbox, code):
    (sandbox / "scripts").mkdir(parents=True, exist_ok=True)
    (sandbox / "scripts" / "plot.py").write_text(code, encoding="utf-8")


def _run_sandbox_script(tmp_path, monkeypatch, code, sandbox=None):
    _load_cfg(tmp_path, monkeypatch)
    sandbox = sandbox or (tmp_path / "session")
    (sandbox / "artifacts").mkdir(parents=True, exist_ok=True)
    _write_sandbox_script(sandbox, code)
    executor = PlotExecutor(sandbox, get_config())
    text, event = asyncio.run(
        executor.execute("python_sandbox", {"script_name": "scripts/plot.py"})
    )
    return text, event


def test_sandbox_blocks_read_outside(tmp_path, monkeypatch):
    """沙箱内脚本读取沙箱外文件被拦截。"""
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    _, event = _run_sandbox_script(
        tmp_path,
        monkeypatch,
        "from pathlib import Path\nprint(Path('../secret.txt').read_text(encoding='utf-8'))\n",
    )
    assert event["success"] is False
    assert "blocked" in (event["traceback"] or event["stderr"])


def test_sandbox_blocks_write_outside(tmp_path, monkeypatch):
    """沙箱内脚本写入沙箱外文件被拦截。"""
    _, event = _run_sandbox_script(
        tmp_path,
        monkeypatch,
        "from pathlib import Path\nPath('../pwned.txt').write_text('x', encoding='utf-8')\n",
    )
    assert event["success"] is False
    assert "blocked" in (event["traceback"] or event["stderr"])
    assert not (tmp_path / "pwned.txt").exists()


def test_sandbox_blocks_delete_outside(tmp_path, monkeypatch):
    """沙箱内脚本删除沙箱外文件被拦截。"""
    target = tmp_path / "victim.txt"
    target.write_text("keep", encoding="utf-8")
    _, event = _run_sandbox_script(
        tmp_path,
        monkeypatch,
        "from pathlib import Path\nPath('../victim.txt').unlink()\n",
    )
    assert event["success"] is False
    assert "blocked" in (event["traceback"] or event["stderr"])
    assert target.exists()


def test_sandbox_blocks_subprocess(tmp_path, monkeypatch):
    """沙箱内启动子进程被拦截。"""
    _, event = _run_sandbox_script(
        tmp_path,
        monkeypatch,
        "import subprocess\nsubprocess.run(['echo', 'hi'])\n",
    )
    assert event["success"] is False
    assert "blocked" in (event["traceback"] or event["stderr"])


def test_sandbox_blocks_network(tmp_path, monkeypatch):
    """沙箱内访问网络被拦截。"""
    _, event = _run_sandbox_script(
        tmp_path,
        monkeypatch,
        "import socket\nsocket.socket(socket.AF_INET, socket.SOCK_STREAM)\n",
    )
    assert event["success"] is False
    assert "blocked" in (event["traceback"] or event["stderr"])


def test_sandbox_blocks_ctypes(tmp_path, monkeypatch):
    """沙箱内 ctypes 加载进程句柄（pythonapi / CDLL(None)）被拦截。"""
    _, event = _run_sandbox_script(
        tmp_path,
        monkeypatch,
        "import ctypes\nctypes.CDLL(None)\n",
    )
    tb = event["traceback"] or event["stderr"]
    assert event["success"] is False
    # Windows 上 CDLL(None) 在审计前抛 TypeError，POSIX 上被钩子拦截
    assert "blocked" in tb or "ctypes" in tb


def test_sandbox_blocks_ctypes_path_load(tmp_path, monkeypatch):
    """沙箱内 ctypes 按路径加载库被拦截。"""
    _, event = _run_sandbox_script(
        tmp_path,
        monkeypatch,
        "import ctypes\nctypes.CDLL('./evil.dll')\n",
    )
    assert event["success"] is False
    assert "blocked" in (event["traceback"] or event["stderr"])


def test_sandbox_blocks_ctypes_dangerous_symbol(tmp_path, monkeypatch):
    """沙箱内 ctypes 解析危险符号（WinExec / system）被拦截。"""
    lib = "kernel32" if os.name == "nt" else "c"
    sym = "WinExec" if os.name == "nt" else "system"
    _, event = _run_sandbox_script(
        tmp_path,
        monkeypatch,
        f"import ctypes\nlib = ctypes.CDLL('{lib}')\nlib.{sym}\n",
    )
    assert event["success"] is False
    assert "blocked" in (event["traceback"] or event["stderr"])


def test_sandbox_blocks_listdir_outside(tmp_path, monkeypatch):
    """沙箱内枚举沙箱外目录被拦截。"""
    _, event = _run_sandbox_script(
        tmp_path,
        monkeypatch,
        "import os\nprint(os.listdir('../'))\n",
    )
    assert event["success"] is False
    assert "blocked" in (event["traceback"] or event["stderr"])


def test_sandbox_allows_normal_writes(tmp_path, monkeypatch):
    """沙箱内的正常读写不受影响。"""
    _, event = _run_sandbox_script(
        tmp_path,
        monkeypatch,
        "from pathlib import Path\n"
        "Path('artifacts/ok.txt').write_text('hi', encoding='utf-8')\n"
        "print(Path('artifacts/ok.txt').read_text(encoding='utf-8'))\n",
    )
    assert event["success"] is True
    assert (tmp_path / "session" / "artifacts" / "ok.txt").exists()


def test_sandbox_allows_imports_and_data_read(tmp_path, monkeypatch):
    """沙箱内正常导入第三方库并读取沙箱内数据不受影响。"""
    sandbox = tmp_path / "session"
    (sandbox / "data").mkdir(parents=True)
    (sandbox / "data" / "data.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    _, event = _run_sandbox_script(
        tmp_path,
        monkeypatch,
        "import pandas as pd\n"
        "df = pd.read_csv('data/data.csv')\n"
        "print(df.shape)\n",
        sandbox=sandbox,
    )
    assert event["success"] is True


def test_write_protected_paths_rejected(tmp_path, monkeypatch):
    """Agent 不得覆写上传数据与 session 控制文件。"""
    _load_cfg(tmp_path, monkeypatch)
    sandbox = tmp_path / "session"
    (sandbox / "data").mkdir(parents=True)
    executor = PlotExecutor(sandbox, get_config())
    for rel in ("data/raw.csv", "meta.json", "trace.jsonl"):
        text, event = asyncio.run(
            executor.execute("write_file", {"file_path": rel, "content": "x"})
        )
        assert event["ok"] is False, rel
        # trace.jsonl 已被后缀白名单拦截，其余走受保护路径检查
        if rel != "trace.jsonl":
            assert "protected" in event["summary"]


def test_is_internal_rel_path():
    """内部运行目录（.cache / tmp）的文件应从文件清单与 ZIP 中排除。"""
    assert is_internal_rel_path(".cache/matplotlib/fontlist.json")
    assert is_internal_rel_path("tmp/tmpabc123/tmp.txt")
    assert not is_internal_rel_path("scripts/plot.py")
    assert not is_internal_rel_path("artifacts/figure.pdf")
    assert not is_internal_rel_path("data/data.csv")


def test_list_files_excludes_internal_dirs(tmp_path, monkeypatch):
    """list_files 应排除 .cache / tmp 内部目录，保留 scripts 与 artifacts。"""
    _load_cfg(tmp_path, monkeypatch)
    sandbox = tmp_path / "session"
    (sandbox / "artifacts").mkdir(parents=True)
    (sandbox / ".cache" / "matplotlib").mkdir(parents=True)
    (sandbox / "tmp").mkdir(parents=True)
    (sandbox / "artifacts" / "figure.pdf").write_bytes(b"%PDF")
    (sandbox / ".cache" / "matplotlib" / "fontlist.json").write_text("{}")
    (sandbox / "tmp" / "x.txt").write_text("x")
    executor = PlotExecutor(sandbox, get_config())
    paths = [f["path"] for f in executor.list_files()]
    assert "artifacts/figure.pdf" in paths
    assert all(not p.startswith((".cache/", "tmp/")) for p in paths)


def test_stats_test_two_groups(tmp_path, monkeypatch):
    _load_cfg(tmp_path, monkeypatch)
    sandbox = tmp_path / "session"
    (sandbox / "data").mkdir(parents=True)
    (sandbox / "data" / "data.csv").write_text(
        "group,value\nA,1\nA,2\nB,5\nB,6\n", encoding="utf-8"
    )
    executor = PlotExecutor(sandbox, get_config())
    text, event = asyncio.run(
        executor.execute(
            "stats_test",
            {
                "data_file": "data/data.csv",
                "test": "t-test",
                "y": "value",
                "group": "group",
            },
        )
    )
    assert event["ok"] is True
    assert event["result"]["test"] == "t-test"
    assert event["result"]["p_value"] is not None


FULL_DOC_TEX = r"""\documentclass[border=10pt]{standalone}
\usepackage{pgfplots}
\begin{document}
\begin{tikzpicture}
\end{tikzpicture}
\end{document}
"""

FRAGMENT_TEX = r"""\begin{tikzpicture}
\end{tikzpicture}
"""


def _fake_tex_result(success=True, raw_output="ok"):
    from backend.utils.tex_compile import TexCompileResult

    return TexCompileResult(
        success=success,
        filtered_output="ok",
        raw_output=raw_output,
        used_texfot=False,
    )


def test_compile_tikz_full_document(tmp_path, monkeypatch):
    """完整 standalone 文档直接作为主文件编译，cwd 为 artifacts/。"""
    _load_cfg(tmp_path, monkeypatch)
    cfg = get_config()
    cfg.features.plot_gen.tikz_compile_enabled = True
    sandbox = tmp_path / "session"
    (sandbox / "artifacts").mkdir(parents=True)
    (sandbox / "artifacts" / "figure.tex").write_text(FULL_DOC_TEX, encoding="utf-8")
    executor = PlotExecutor(sandbox, cfg)
    with patch(
        "backend.modules.plot_gen.executor.run_tex_compile",
        new=AsyncMock(return_value=_fake_tex_result()),
    ) as m_compile:
        text, event = asyncio.run(
            executor.execute("compile_tikz", {"tikz_file": "artifacts/figure.tex"})
        )
    assert event["success"] is True
    main_file, cwd = m_compile.await_args.args[:2]
    assert main_file == "tikz_main.tex"
    assert str(cwd) == str((sandbox / "artifacts").resolve())
    main_tex = (sandbox / "artifacts" / "tikz_main.tex").read_text(encoding="utf-8")
    assert "\\documentclass" in main_tex
    assert "\\input" not in main_tex


def test_compile_tikz_fragment_wrapped(tmp_path, monkeypatch):
    """tikzpicture 片段被包装进 standalone 主文件并通过 \\input 引用。"""
    _load_cfg(tmp_path, monkeypatch)
    cfg = get_config()
    cfg.features.plot_gen.tikz_compile_enabled = True
    sandbox = tmp_path / "session"
    (sandbox / "artifacts").mkdir(parents=True)
    (sandbox / "artifacts" / "figure.tex").write_text(FRAGMENT_TEX, encoding="utf-8")
    executor = PlotExecutor(sandbox, cfg)
    with patch(
        "backend.modules.plot_gen.executor.run_tex_compile",
        new=AsyncMock(return_value=_fake_tex_result()),
    ) as m_compile:
        text, event = asyncio.run(
            executor.execute("compile_tikz", {"tikz_file": "artifacts/figure.tex"})
        )
    assert event["success"] is True
    main_file, cwd = m_compile.await_args.args[:2]
    assert main_file == "tikz_main.tex"
    main_tex = (sandbox / "artifacts" / "tikz_main.tex").read_text(encoding="utf-8")
    assert "\\input{figure.tex}" in main_tex


def test_compile_tikz_copies_foreign_file(tmp_path, monkeypatch):
    """目标不在 artifacts/ 下时复制进 artifacts/ 再编译。"""
    _load_cfg(tmp_path, monkeypatch)
    cfg = get_config()
    cfg.features.plot_gen.tikz_compile_enabled = True
    sandbox = tmp_path / "session"
    (sandbox / "artifacts").mkdir(parents=True)
    (sandbox / "standalone.tex").write_text(FULL_DOC_TEX, encoding="utf-8")
    executor = PlotExecutor(sandbox, cfg)
    with patch(
        "backend.modules.plot_gen.executor.run_tex_compile",
        new=AsyncMock(return_value=_fake_tex_result()),
    ) as m_compile:
        text, event = asyncio.run(
            executor.execute("compile_tikz", {"tikz_file": "standalone.tex"})
        )
    assert event["success"] is True
    main_file, cwd = m_compile.await_args.args[:2]
    assert main_file == "tikz_main.tex"
    assert (sandbox / "artifacts" / "standalone.tex").exists()
    assert (sandbox / "artifacts" / "tikz_main.tex").exists()


def test_compile_tikz_missing_character_surfaces_warning(tmp_path, monkeypatch):
    """latexmk 退出码为 0 时：真实标签的 Missing character 上浮为质量信号，
    nullfont 噪音（版本字符串）应被过滤，避免 Agent 做无效修复。"""
    _load_cfg(tmp_path, monkeypatch)
    cfg = get_config()
    cfg.features.plot_gen.tikz_compile_enabled = True
    sandbox = tmp_path / "session"
    (sandbox / "artifacts").mkdir(parents=True)
    (sandbox / "artifacts" / "figure.tex").write_text(FULL_DOC_TEX, encoding="utf-8")
    executor = PlotExecutor(sandbox, cfg)
    raw = (
        "This is pdfTeX, Version 3.141592653-2.6-1.40.29\n"
        "Missing character: There is no . in font nullfont!\n"
        "Missing character: There is no 6 in font nullfont!\n"
        "Output written on tikz_main.pdf (1 page, 22608 bytes).\n"
    )
    with patch(
        "backend.modules.plot_gen.executor.run_tex_compile",
        new=AsyncMock(return_value=_fake_tex_result(success=True, raw_output=raw)),
    ):
        text, event = asyncio.run(
            executor.execute("compile_tikz", {"tikz_file": "artifacts/figure.tex"})
        )
    assert event["success"] is True
    assert event["warnings"] == [], "nullfont 噪音警告应被过滤"
    assert "TIKZ ISSUES DETECTED" not in text

    # 真实标签的 Missing character（引用具体字形而非 nullfont）仍需上浮
    raw_real = (
        "This is pdfTeX, Version 3.141592653-2.6-1.40.29\n"
        "Missing character: There is no \u4ea7 (U+4EA7) in font [lmroman10-bold]:\n"
        "Output written on tikz_main.pdf (1 page, 22608 bytes).\n"
    )
    with patch(
        "backend.modules.plot_gen.executor.run_tex_compile",
        new=AsyncMock(return_value=_fake_tex_result(success=True, raw_output=raw_real)),
    ):
        text, event = asyncio.run(
            executor.execute("compile_tikz", {"tikz_file": "artifacts/figure.tex"})
        )
    assert event["success"] is True
    assert event["warnings"], "真实标签的 Missing character 警告应被提取"
    assert "TIKZ ISSUES DETECTED" in text


def test_compile_tikz_error_downgrades_success(tmp_path, monkeypatch):
    """latexmk 退出码为 0 但日志含真实错误时，success 应降级为 False。"""
    _load_cfg(tmp_path, monkeypatch)
    cfg = get_config()
    cfg.features.plot_gen.tikz_compile_enabled = True
    sandbox = tmp_path / "session"
    (sandbox / "artifacts").mkdir(parents=True)
    (sandbox / "artifacts" / "figure.tex").write_text(FULL_DOC_TEX, encoding="utf-8")
    executor = PlotExecutor(sandbox, cfg)
    raw = (
        "! LaTeX Error: Undefined control sequence.\n"
        "See the LaTeX manual or LaTeX Companion for explanation.\n"
    )
    with patch(
        "backend.modules.plot_gen.executor.run_tex_compile",
        new=AsyncMock(return_value=_fake_tex_result(success=True, raw_output=raw)),
    ):
        text, event = asyncio.run(
            executor.execute("compile_tikz", {"tikz_file": "artifacts/figure.tex"})
        )
    assert event["success"] is False
    assert event["ok"] is False
    assert "TIKZ ISSUES DETECTED" in text


# ---------- 4.1 figure.tex 编译前预检 ----------


def _run_compile_tikz(tmp_path, monkeypatch, content):
    _load_cfg(tmp_path, monkeypatch)
    cfg = get_config()
    cfg.features.plot_gen.tikz_compile_enabled = True
    sandbox = tmp_path / "session"
    (sandbox / "artifacts").mkdir(parents=True)
    (sandbox / "artifacts" / "figure.tex").write_text(content, encoding="utf-8")
    executor = PlotExecutor(sandbox, cfg)
    with patch(
        "backend.modules.plot_gen.executor.run_tex_compile",
        new=AsyncMock(return_value=_fake_tex_result()),
    ) as m_compile:
        text, event = asyncio.run(
            executor.execute("compile_tikz", {"tikz_file": "artifacts/figure.tex"})
        )
    return text, event, m_compile


def test_precheck_inline_y_error_blocks_compile(tmp_path, monkeypatch):
    """内联 y error= 语法应在编译前拦截，不启动 latexmk。"""
    content = (
        "\\begin{tikzpicture}\n"
        "\\begin{axis}\n"
        "\\addplot+[y error=0.5] coordinates {(1, 2) (2, 3)};\n"
        "\\end{axis}\n"
        "\\end{tikzpicture}\n"
    )
    text, event, m_compile = _run_compile_tikz(tmp_path, monkeypatch, content)
    assert event["success"] is False
    assert event["precheck_blocked"] is True
    assert any(f["rule"] == "error_bar" for f in event["precheck"])
    assert "PRECHECK" in text
    m_compile.assert_not_awaited()


def test_precheck_nan_error_bar_blocks_compile(tmp_path, monkeypatch):
    """matplot2tikz 的 `+- (nan, nan)` 误差增量应在编译前拦截。"""
    content = (
        "\\begin{tikzpicture}\n"
        "\\begin{axis}\n"
        "\\addplot+ coordinates {(44928, 22.47) +- (nan, nan)};\n"
        "\\end{axis}\n"
        "\\end{tikzpicture}\n"
    )
    text, event, m_compile = _run_compile_tikz(tmp_path, monkeypatch, content)
    assert event["success"] is False
    assert event["precheck_blocked"] is True
    assert any(f["rule"] == "error_bar" for f in event["precheck"])
    m_compile.assert_not_awaited()


def test_precheck_large_coordinate_blocks_compile(tmp_path, monkeypatch):
    """线性轴上绝对值 > 1e4 的坐标（日期序号）应在编译前拦截。"""
    content = (
        "\\begin{tikzpicture}\n"
        "\\begin{axis}\n"
        "\\addplot coordinates {(44927, 20.6) (44928, 22.5)};\n"
        "\\end{axis}\n"
        "\\end{tikzpicture}\n"
    )
    text, event, m_compile = _run_compile_tikz(tmp_path, monkeypatch, content)
    assert event["success"] is False
    assert event["precheck_blocked"] is True
    assert any(f["rule"] == "large_coordinate" for f in event["precheck"])
    assert "Dimension too large" in text
    m_compile.assert_not_awaited()


def test_precheck_large_coordinate_log_axis_allowed(tmp_path, monkeypatch):
    """对数轴上的大数值坐标是合法的，不应被预检拦截。"""
    content = (
        "\\begin{tikzpicture}\n"
        "\\begin{axis}[xmode=log]\n"
        "\\addplot coordinates {(0.01, 2) (100000, 98.7)};\n"
        "\\end{axis}\n"
        "\\end{tikzpicture}\n"
    )
    text, event, m_compile = _run_compile_tikz(tmp_path, monkeypatch, content)
    assert event["success"] is True
    assert event["precheck_blocked"] is False
    m_compile.assert_awaited_once()


def test_precheck_non_ascii_warns_but_compiles(tmp_path, monkeypatch):
    """非 ASCII 标签只给警告（xelatex 可渲染），不拦截编译。"""
    content = (
        "\\begin{tikzpicture}\n"
        "\\begin{axis}[xlabel={浓度 (uM)}]\n"
        "\\addplot coordinates {(1, 2) (2, 3)};\n"
        "\\end{axis}\n"
        "\\end{tikzpicture}\n"
    )
    text, event, m_compile = _run_compile_tikz(tmp_path, monkeypatch, content)
    assert event["success"] is True
    assert event["precheck_blocked"] is False
    assert any(f["rule"] == "non_ascii" for f in event["precheck"])
    assert "Non-ASCII" in text
    m_compile.assert_awaited_once()


def test_precheck_clean_tikz_no_findings(tmp_path, monkeypatch):
    """干净的手写 PGFPlots 不应触发任何预检问题。"""
    content = (
        "\\begin{tikzpicture}\n"
        "\\begin{axis}[xlabel={Dose (uM)}]\n"
        "\\addplot table[x=dose, y=mean] {data.csv};\n"
        "\\end{axis}\n"
        "\\end{tikzpicture}\n"
    )
    text, event, m_compile = _run_compile_tikz(tmp_path, monkeypatch, content)
    assert event["success"] is True
    assert event["precheck"] == []
    assert "PRECHECK" not in text
    m_compile.assert_awaited_once()


def test_precheck_numeric_error_bar_warns_only():
    """`+-` 数值增量是合法 PGFPlots 语法，只给警告不拦截。"""
    findings, blocked = _precheck_tikz(
        "\\addplot+[error bars/.cd, y explicit] "
        "coordinates {(1, 2) +- (0, 0.5)};\n"
    )
    assert blocked is False
    assert any(f["rule"] == "error_bar" and f["level"] == "warning" for f in findings)


# ---------- 4.2 write_file 阶段 Python 静态校验 ----------


def test_write_file_python_syntax_error_rejected(tmp_path, monkeypatch):
    """写入含语法错误的 .py 应直接拒绝，不落盘、不发子进程。"""
    _load_cfg(tmp_path, monkeypatch)
    sandbox = tmp_path / "session"
    executor = PlotExecutor(sandbox, get_config())
    text, event = asyncio.run(
        executor.execute(
            "write_file",
            {
                "file_path": "scripts/plot.py",
                "content": "def plot():\n    print('unbalanced'\n",
            },
        )
    )
    assert event["ok"] is False
    assert "syntax error" in text
    assert "line " in event["summary"]
    assert not (sandbox / "scripts" / "plot.py").exists()


def test_write_file_python_valid_accepted(tmp_path, monkeypatch):
    """语法正确的 .py 正常写入。"""
    _load_cfg(tmp_path, monkeypatch)
    sandbox = tmp_path / "session"
    executor = PlotExecutor(sandbox, get_config())
    code = (
        "from pathlib import Path\n"
        "Path('artifacts/out.txt').write_text('ok', encoding='utf-8')\n"
    )
    text, event = asyncio.run(
        executor.execute(
            "write_file", {"file_path": "scripts/plot.py", "content": code}
        )
    )
    assert event["ok"] is True
    assert (sandbox / "scripts" / "plot.py").exists()


def test_write_file_non_python_not_syntax_checked(tmp_path, monkeypatch):
    """非 .py 文件不受语法检查影响（如 .tex）。"""
    _load_cfg(tmp_path, monkeypatch)
    sandbox = tmp_path / "session"
    executor = PlotExecutor(sandbox, get_config())
    text, event = asyncio.run(
        executor.execute(
            "write_file",
            {
                "file_path": "artifacts/figure.tex",
                "content": "\\begin{tikzpicture} % unclosed brace {",
            },
        )
    )
    assert event["ok"] is True
    assert (sandbox / "artifacts" / "figure.tex").exists()


def test_check_python_syntax_helper():
    """语法检查助手对合法/非法代码返回正确结果。"""
    assert _check_python_syntax("x = 1\nprint(x)\n") is None
    err = _check_python_syntax("def f(:\n    pass\n")
    assert err is not None
    assert "line 1" in err
