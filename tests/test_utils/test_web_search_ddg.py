"""DuckDuckGo 引擎工具函数单元测试。"""

from backend.utils.web_search.engines.ddg import _urlencode


def test_urlencode_encodes_reserved_characters():
    """表单参数必须经过 URL 编码，否则 & / = 会破坏 application/x-www-form-urlencoded。"""
    assert _urlencode({"q": "a&b=c d", "b": ""}) == b"q=a%26b%3Dc+d&b="


def test_urlencode_preserves_empty_fields():
    assert _urlencode({"q": "latex", "b": ""}) == b"q=latex&b="
