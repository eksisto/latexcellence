"""git_diff 命令错误输出脱敏单元测试（审查遗留项 C1 收口）。

redact_command_output_for_user 是 git/latexdiff 失败消息返回用户前的
统一脱敏口：多用户模式改写 <cwd>/ 前缀与裸 cwd 边界，单用户保留原文。
纯函数测试：不启动子进程，仅 monkeypatch get_config 的多用户开关。
"""

from pathlib import Path
from types import SimpleNamespace

import backend.modules.git_diff.service.process as process_module


def _set_multi_user(monkeypatch, enabled: bool) -> None:
    monkeypatch.setattr(
        process_module,
        "get_config",
        lambda: SimpleNamespace(multi_user=SimpleNamespace(enabled=enabled)),
    )


CWD = Path("/srv/latexcellence/data/users/a1b2/projects/c3d4e5")


def test_multi_user_rewrites_cwd_prefix(monkeypatch):
    _set_multi_user(monkeypatch, True)
    text = f"fatal: cannot read {CWD}/main.tex: No such file or directory"
    out = process_module.redact_command_output_for_user(text, CWD)
    assert str(CWD) not in out
    assert "./main.tex" in out


def test_multi_user_rewrites_bare_cwd_token(monkeypatch):
    _set_multi_user(monkeypatch, True)
    text = f"chdir to {CWD}\nfatal: not a git repository"
    out = process_module.redact_command_output_for_user(text, CWD)
    assert str(CWD) not in out
    # 裸 cwd（行尾边界）改写为 "."
    assert "." in out


def test_single_user_keeps_raw_output(monkeypatch):
    _set_multi_user(monkeypatch, False)
    text = f"fatal: cannot read {CWD}/main.tex"
    out = process_module.redact_command_output_for_user(text, CWD)
    assert out == text


def test_paths_outside_cwd_are_left_untouched(monkeypatch):
    _set_multi_user(monkeypatch, True)
    other = Path("/opt/texlive/bin/x86_64/latexdiff")
    text = f"error: {other} exited unexpectedly"
    out = process_module.redact_command_output_for_user(text, CWD)
    # 兄弟路径 / 系统路径不属于本项目前缀，保持原样（避免误伤语义）
    assert str(other) in out


def test_relative_message_passthrough(monkeypatch):
    _set_multi_user(monkeypatch, True)
    out = process_module.redact_command_output_for_user("Git command failed", CWD)
    assert out == "Git command failed"
