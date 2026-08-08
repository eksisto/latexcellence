"""changes 契约归一化 + 未列出修改检测测试。"""

from backend.core.base_paragraph_check import (
    has_unlisted_changes,
    normalize_changes_contract,
)


def test_unchanged_text_drops_spurious_changes():
    """文本未变但 changes 非空 → 丢弃虚假条目。"""
    changes = [{"type": "typo", "original": "a", "corrected": "b"}]
    assert normalize_changes_contract(changes, "abc", "abc") == []


def test_changed_text_with_empty_changes_not_fabricated():
    """文本已变但 changes 为空 → 不伪造条目（由 prompt 约束保证非空）。"""
    assert normalize_changes_contract([], "abc2", "abc") == []


def test_changed_text_keeps_existing_changes():
    """文本已变且 changes 非空 → 原样保留。"""
    changes = [{"type": "typo", "original": "a", "corrected": "b"}]
    result = normalize_changes_contract(changes, "abc2", "abc")
    assert result == changes


def test_changed_text_without_changes_flagged():
    """文本已变但 changes 为空 → 标记为未列出的全文修改。"""
    assert has_unlisted_changes([], "abc2", "abc") is True


def test_changed_text_with_changes_not_flagged():
    """文本已变且 changes 非空 → 不需要未列出标记。"""
    changes = [{"type": "typo", "original": "a", "corrected": "b"}]
    assert has_unlisted_changes(changes, "abc2", "abc") is False


def test_unchanged_text_not_flagged():
    """文本未变（含虚假 changes 归一化后为空）→ 不标记。"""
    assert has_unlisted_changes([], "abc", "abc") is False
    assert has_unlisted_changes([{"type": "typo"}], "abc", "abc") is False
