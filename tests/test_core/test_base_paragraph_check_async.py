"""逐段检查基类 preview/apply 线程池封装回归。

覆盖：
    - preview_paragraphs_async / apply_changes_async 返回同步方法的结果
    - 同步主体在事件循环外的工作线程执行（路由不得在事件循环上做
      段落提取、整文件重写等阻塞工作）
"""

import threading

import pytest

from backend.core.base_paragraph_check import BaseParagraphCheckService


class _MinimalService(BaseParagraphCheckService):
    """仅用于验证线程池封装的最小子类（钩子不被触达）。"""

    def __init__(self):
        super().__init__("test.base_paragraph_check_async")


@pytest.mark.asyncio
async def test_async_wrappers_delegate_and_run_off_event_loop(monkeypatch):
    svc = _MinimalService()
    main_tid = threading.get_ident()
    seen: dict[str, int] = {}

    def fake_preview(file_path, resolve_input=False, workspace_id=""):
        seen["preview"] = threading.get_ident()
        return {"paragraphs": [], "total": 0, "operation_id": "op"}

    def fake_apply(file_path, changes, workspace_id="", operation_id=None):
        seen["apply"] = threading.get_ident()
        return {"applied": 0, "total": 0, "errors": []}

    monkeypatch.setattr(svc, "preview_paragraphs", fake_preview)
    monkeypatch.setattr(svc, "apply_changes", fake_apply)

    preview = await svc.preview_paragraphs_async("main.tex", False, "ws-1")
    applied = await svc.apply_changes_async("main.tex", [], "ws-1", "op")

    assert preview == {"paragraphs": [], "total": 0, "operation_id": "op"}
    assert applied == {"applied": 0, "total": 0, "errors": []}
    assert seen["preview"] != main_tid
    assert seen["apply"] != main_tid
