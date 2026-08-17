"""会话级 basetemp 机制回归测试（由根 conftest.py 配置）。"""

from pathlib import Path


def test_tmp_path_under_unique_session_basetemp(tmp_path, tmp_path_factory, request):
    """默认配置下 basetemp 是工作区内 .pytest_tmp/session-* 独立目录。"""
    base = tmp_path_factory.getbasetemp()
    assert tmp_path.is_relative_to(base)
    explicit = request.config.option.basetemp
    if explicit:
        # 显式 --basetemp 优先于 conftest 的会话目录机制，只校验一致性。
        assert Path(str(explicit)).resolve() == base.resolve()
    else:
        assert base.parent.name == ".pytest_tmp"
        assert base.name.startswith("session-")
