"""逐段检查 apply 链路回归。

覆盖：
    - file_path 缺省回退：路由侧以 ``model_dump()`` 展开 ApplyChangeItem，
      change 未显式携带 file_path 时该键存在且值为 None，
      ``dict.get(key, default)`` 的默认值参数永不生效，目标文件必须
      真值回退到请求级 file_path
    - 批内乐观锁：同一文件多条 change 基于同一基线生成，逐条写回时
      后续 change 必须以批内最新版本校验，否则误报 file_conflict
"""

from unittest.mock import MagicMock, patch

from backend.core.base_paragraph_check import (
    ApplyChangeItem,
    BaseParagraphCheckService,
    inject_expected_md5s,
    resolve_change_file_path,
)
from backend.core.project_manager import LatexProject
from backend.utils.file_ops import compute_md5


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

    def test_already_set_not_overwritten(self):
        """调用方已显式携带 expected_md5 时不得覆盖（跨工作区校验值优先）。"""
        md5s = {"main.tex": "md5-latest"}
        changes = [
            ApplyChangeItem(
                paragraph_index=0, new_text="x", expected_md5="md5-mine"
            ).model_dump()
        ]
        inject_expected_md5s(changes, "main.tex", md5s)
        assert changes[0]["expected_md5"] == "md5-mine"

    def test_missing_file_in_map_not_injected(self):
        """map 缺该文件时不注入（保持 None，写回落到实例缓存回退而非错值）。"""
        md5s = {"other.tex": "md5-other"}
        changes = [ApplyChangeItem(paragraph_index=0, new_text="x").model_dump()]
        inject_expected_md5s(changes, "main.tex", md5s)
        assert changes[0]["expected_md5"] is None


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


class TestApplyChangesBatchSameFile:
    def test_same_file_batch_shares_baseline(self, tmp_path):
        """同文件同基线的两条 change 逐条写回：首条成功后文件 MD5 已变，
        第二条必须以批内新版本校验而非调用方旧基线（旧行为必报
        file_conflict，「全部应用」每文件只能写入第一段）。"""
        target = tmp_path / "main.tex"
        target.write_text("Para one.\n\nPara two.\n\nPara three.\n", encoding="utf-8")
        pm = LatexProject()
        pm.open(str(tmp_path))
        paras = pm.get_paragraphs("main.tex")
        assert len(paras) >= 2
        baseline = compute_md5(target)

        svc = _NoMarkerService()
        with patch(
            "backend.core.base_paragraph_check.get_project_manager_for_workspace",
            return_value=pm,
        ):
            result = svc.apply_changes(
                "main.tex",
                [
                    {
                        "paragraph_index": paras[0].index,
                        "new_text": "Para one edited.",
                        "expected_md5": baseline,
                    },
                    {
                        "paragraph_index": paras[1].index,
                        "new_text": "Para two edited.",
                        "expected_md5": baseline,
                    },
                ],
                workspace_id="ws",
            )

        assert result["applied"] == 2
        assert result["errors"] == []
        # 响应携带写后最新 MD5，前端同步本地乐观锁缓存
        assert result["file_md5s"]["main.tex"]
        assert result["file_md5s"]["main.tex"] != baseline
        content = target.read_text(encoding="utf-8")
        assert "Para one edited." in content
        assert "Para two edited." in content

    def test_single_apply_response_updates_md5(self, tmp_path):
        """单段应用响应同样携带写后 MD5，供连续应用免冲突。"""
        target = tmp_path / "main.tex"
        target.write_text("Only paragraph.\n", encoding="utf-8")
        pm = LatexProject()
        pm.open(str(tmp_path))

        svc = _NoMarkerService()
        with patch(
            "backend.core.base_paragraph_check.get_project_manager_for_workspace",
            return_value=pm,
        ):
            result = svc.apply_changes(
                "main.tex",
                [{"paragraph_index": 0, "new_text": "Only paragraph edited."}],
                workspace_id="ws",
            )

        assert result["applied"] == 1
        assert result["file_md5s"]["main.tex"] == compute_md5(target)


class TestApplyChangesFileConflict:
    def test_stale_md5_reports_conflict_and_keeps_file(self, tmp_path):
        """读后被外部修改（MD5 漂移）时 apply_changes 返回
        errors[].reason=file_conflict（含双 MD5），且文件不被写入。
        这是前端 StaleCheckBanner「保鲜」链路的后端根基。"""
        target = tmp_path / "main.tex"
        original = "Stale check paragraph.\n"
        target.write_text(original, encoding="utf-8")
        pm = LatexProject()
        pm.open(str(tmp_path))
        stale_md5 = compute_md5(target)

        # 检查后文件被另一工作区/编辑器改动
        target.write_text(
            "Stale check paragraph modified externally.\n", encoding="utf-8"
        )

        svc = _NoMarkerService()
        with patch(
            "backend.core.base_paragraph_check.get_project_manager_for_workspace",
            return_value=pm,
        ):
            result = svc.apply_changes(
                "main.tex",
                [
                    {
                        "paragraph_index": 0,
                        "new_text": "Should not be written.",
                        "expected_md5": stale_md5,
                    }
                ],
                workspace_id="ws",
            )

        assert result["applied"] == 0
        assert len(result["errors"]) == 1
        err = result["errors"][0]
        assert err["reason"] == "file_conflict"
        assert err["expected_md5"] == stale_md5
        assert err["current_md5"] == compute_md5(target)
        # 文件保持外部修改后的内容，建议未写入
        assert target.read_text(encoding="utf-8") == (
            "Stale check paragraph modified externally.\n"
        )
