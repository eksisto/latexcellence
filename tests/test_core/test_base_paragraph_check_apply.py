"""逐段检查 apply 链路 file_path 缺省回退回归。

路由侧以 ``model_dump()`` 展开 ApplyChangeItem，change 未显式携带
file_path 时该键存在且值为 None，``dict.get(key, default)`` 的默认值
参数永不生效；目标文件必须真值回退到请求级 file_path，
否则写回链路以 None 为目标文件查 marker 映射 / 定位源文件。
"""

from unittest.mock import MagicMock, patch

from backend.core.base_paragraph_check import (
    ApplyChangeItem,
    BaseParagraphCheckService,
    inject_expected_md5s,
    resolve_change_file_path,
)


class _NoMarkerService(BaseParagraphCheckService):
    """关闭 marker 保护的最小子类，apply 直接透传 new_text。"""

    use_marker_protection = False

    def __init__(self):
        super().__init__("test.base_paragraph_check_apply")


class TestResolveChangeFilePath:
    def test_model_dump_none_falls_back(self):
        change = ApplyChangeItem(paragraph_index=0, new_text="x").model_dump()
        assert change["file_path"] is None
        assert resolve_change_file_path(change, "main.tex") == "main.tex"

    def test_explicit_file_path_wins(self):
        change = ApplyChangeItem(
            paragraph_index=0, new_text="x", file_path="ch1.tex"
        ).model_dump()
        assert resolve_change_file_path(change, "main.tex") == "ch1.tex"


class TestInjectExpectedMd5s:
    def test_none_file_path_uses_fallback_md5(self):
        """change 缺省 file_path 时按请求级默认文件补 MD5，而非 None 键取空。"""
        md5s = {"main.tex": "md5-main", "ch1.tex": "md5-ch1"}
        changes = [ApplyChangeItem(paragraph_index=0, new_text="x").model_dump()]
        inject_expected_md5s(changes, "main.tex", md5s)
        assert changes[0]["expected_md5"] == "md5-main"

    def test_explicit_file_path_uses_own_md5(self):
        md5s = {"main.tex": "md5-main", "ch1.tex": "md5-ch1"}
        changes = [
            ApplyChangeItem(
                paragraph_index=0, new_text="x", file_path="ch1.tex"
            ).model_dump()
        ]
        inject_expected_md5s(changes, "main.tex", md5s)
        assert changes[0]["expected_md5"] == "md5-ch1"


class TestApplyChangesFallbackFile:
    def test_model_dump_change_targets_fallback_file(self):
        """change 携带 file_path=None（model_dump 产物）时写回请求级默认文件。"""
        svc = _NoMarkerService()
        pm = MagicMock()
        with patch(
            "backend.core.base_paragraph_check.get_project_manager_for_workspace",
            return_value=pm,
        ):
            result = svc.apply_changes(
                "main.tex",
                [ApplyChangeItem(paragraph_index=3, new_text="新文本").model_dump()],
                workspace_id="ws",
            )
        assert result["applied"] == 1
        assert result["errors"] == []
        pm.apply_change.assert_called_once()
        call_args = pm.apply_change.call_args
        assert call_args.args[0] == "main.tex"
        assert call_args.args[1] == 3
        assert call_args.args[2] == "新文本"
