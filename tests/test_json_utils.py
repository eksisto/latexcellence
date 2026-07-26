"""
json_utils 模块单元测试。

覆盖内容：
    - safe_parse_json 各种畸形输入
    - format_validation_error 错误格式化
    - parse_and_validate 组合解析+校验
    - retry_with_validation 重试逻辑（Mock LLM）
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, ValidationError

from backend.utils.json_utils import (
    format_validation_error,
    parse_and_validate,
    retry_with_validation,
    safe_parse_json,
    validate_model,
)


class SampleItem(BaseModel):
    name: str
    value: int


# ── safe_parse_json ─────────────────────────────────────────────────────────


class TestSafeParseJson:
    """safe_parse_json 容错解析测试。"""

    def test_dict_passthrough(self):
        data = {"key": "value"}
        result = safe_parse_json(data)
        assert result == data

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            safe_parse_json("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            safe_parse_json("   \n\t   ")

    def test_valid_json(self):
        result = safe_parse_json('{"a": 1, "b": "hello"}')
        assert result == {"a": 1, "b": "hello"}

    def test_markdown_code_fence(self):
        result = safe_parse_json('```json\n{"x": 42}\n```')
        assert result == {"x": 42}

    def test_markdown_code_fence_no_lang(self):
        result = safe_parse_json('```\n{"x": 42}\n```')
        assert result == {"x": 42}

    def test_markdown_with_surrounding_text(self):
        result = safe_parse_json('Here is the result:\n```json\n{"x": 1}\n```\nDone.')
        assert result == {"x": 1}

    def test_trailing_comma_in_object(self):
        result = safe_parse_json('{"a": 1, "b": 2,}')
        assert result == {"a": 1, "b": 2}

    def test_trailing_comma_in_array(self):
        result = safe_parse_json('{"items": [1, 2, 3,]}')
        assert result == {"items": [1, 2, 3]}

    def test_single_quotes(self):
        result = safe_parse_json("{'name': 'test'}")
        assert result == {"name": "test"}

    def test_nested_objects(self):
        result = safe_parse_json('{"a": {"b": {"c": 3}}}')
        assert result == {"a": {"b": {"c": 3}}}

    def test_braces_in_string(self):
        # JSON with braces inside a string value; \\ in JSON string → single \
        result = safe_parse_json('{"latex": "\\\\textbf{hello}"}')
        assert result == {"latex": "\\textbf{hello}"}

    def test_unicode(self):
        result = safe_parse_json('{"text": "你好世界"}')
        assert result == {"text": "你好世界"}

    def test_unparseable_raises(self):
        with pytest.raises(ValueError, match="Failed to parse"):
            safe_parse_json("not json at all {{{")

    def test_empty_object(self):
        result = safe_parse_json("{}")
        assert result == {}

    def test_empty_array(self):
        result = safe_parse_json("[]")
        assert result == []

    def test_json_with_text_before_and_after(self):
        result = safe_parse_json('Some text {"key": "value"} more text')
        assert result == {"key": "value"}

    def test_numbers_floats_bools(self):
        result = safe_parse_json(
            '{"int": 42, "float": 3.14, "bool": true, "null_val": null}'
        )
        assert result == {"int": 42, "float": 3.14, "bool": True, "null_val": None}

    def test_none_input_converted_to_dict(self):
        # When dict is passed, it should be returned as-is
        data = {"key": "value"}
        assert safe_parse_json(data) is data


# ── format_validation_error ─────────────────────────────────────────────────


class TestFormatValidationError:
    """format_validation_error 错误格式化测试。"""

    def test_empty_errors(self):
        result = format_validation_error([])
        assert "JSON validation failed" in result

    def test_single_error(self):
        errors = [{"loc": ["name"], "msg": "field required", "type": "missing"}]
        result = format_validation_error(errors)
        assert "name" in result
        assert "field required" in result
        assert "missing" in result

    def test_multiple_errors(self):
        errors = [
            {"loc": ["name"], "msg": "field required", "type": "missing"},
            {"loc": ["value"], "msg": "not an integer", "type": "type_error"},
        ]
        result = format_validation_error(errors)
        assert "name" in result
        assert "value" in result
        assert result.count("Field") == 2

    def test_nested_loc(self):
        errors = [
            {"loc": ["items", 0, "sub"], "msg": "wrong type", "type": "type_error"}
        ]
        result = format_validation_error(errors)
        assert "items -> 0 -> sub" in result


# ── parse_and_validate ──────────────────────────────────────────────────────


class TestParseAndValidate:
    """parse_and_validate 组合解析+校验测试。"""

    def test_valid_dict_input(self):
        model, error = parse_and_validate({"name": "test", "value": 42}, SampleItem)
        assert error is None
        assert model.name == "test"
        assert model.value == 42

    def test_valid_string_input(self):
        model, error = parse_and_validate('{"name": "test", "value": 42}', SampleItem)
        assert error is None
        assert model.name == "test"

    def test_invalid_json_raise(self):
        with pytest.raises(ValueError):
            parse_and_validate("not json", SampleItem, raise_on_error=True)

    def test_invalid_json_no_raise(self):
        model, error = parse_and_validate("not json", SampleItem, raise_on_error=False)
        assert model is None
        assert error is not None

    def test_validation_failure_raise(self):
        with pytest.raises(ValidationError):
            parse_and_validate('{"name": "test"}', SampleItem, raise_on_error=True)

    def test_validation_failure_no_raise(self):
        model, error = parse_and_validate(
            '{"name": "test"}', SampleItem, raise_on_error=False
        )
        assert model is None
        assert "value" in error

    def test_missing_field(self):
        model, error = parse_and_validate(
            '{"value": 1}', SampleItem, raise_on_error=False
        )
        assert model is None
        assert "name" in error

    def test_wrong_type(self):
        model, error = parse_and_validate(
            '{"name": 123, "value": "abc"}', SampleItem, raise_on_error=False
        )
        assert model is None
        assert error != ""


# ── retry_with_validation ──────────────────────────────────────────────────


class TestRetryWithValidation:
    """retry_with_validation 重试逻辑测试。"""

    @pytest.mark.asyncio
    async def test_success_first_attempt(self):
        chat_fn = AsyncMock(return_value='{"name": "test", "value": 42}')
        messages = [{"role": "user", "content": "test"}]

        result = await retry_with_validation(
            chat_fn=chat_fn,
            messages=messages,
            model_cls=SampleItem,
            max_retries=3,
        )

        assert result.name == "test"
        assert result.value == 42
        chat_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_after_one_retry(self):
        chat_fn = AsyncMock(
            side_effect=[
                '{"name": "test"}',  # missing value → validation fails
                '{"name": "test", "value": 42}',
            ]
        )
        messages = [{"role": "user", "content": "test"}]

        result = await retry_with_validation(
            chat_fn=chat_fn,
            messages=messages,
            model_cls=SampleItem,
            max_retries=3,
        )

        assert result.name == "test"
        assert result.value == 42
        assert chat_fn.call_count == 2

    @pytest.mark.asyncio
    async def test_exhausted_retries(self):
        chat_fn = AsyncMock(return_value='{"name": "test"}')  # always invalid
        messages = [{"role": "user", "content": "test"}]

        with pytest.raises(ValueError, match="Validation failed after"):
            await retry_with_validation(
                chat_fn=chat_fn,
                messages=messages,
                model_cls=SampleItem,
                max_retries=3,
            )

        assert chat_fn.call_count == 3

    @pytest.mark.asyncio
    async def test_messages_unchanged(self):
        chat_fn = AsyncMock(return_value='{"name": "test", "value": 42}')
        messages = [{"role": "user", "content": "original"}]

        await retry_with_validation(
            chat_fn=chat_fn,
            messages=messages,
            model_cls=SampleItem,
            max_retries=3,
        )

        # Original messages list should be unchanged
        assert len(messages) == 1
        assert messages[0]["content"] == "original"

    @pytest.mark.asyncio
    async def test_retry_passes_correction_prompt(self):
        chat_fn = AsyncMock(
            side_effect=[
                '{"name": "test"}',  # missing value
                '{"name": "test", "value": 42}',
            ]
        )
        messages = [{"role": "user", "content": "test"}]

        await retry_with_validation(
            chat_fn=chat_fn,
            messages=messages,
            model_cls=SampleItem,
            max_retries=3,
        )

        # Second call should include the original message + correction prompt
        second_call_messages = chat_fn.call_args_list[1][0][0]
        assert len(second_call_messages) == 2  # 1 original + 1 correction
        assert "validation" in second_call_messages[1]["content"].lower()

    @pytest.mark.asyncio
    async def test_exception_handling(self):
        chat_fn = AsyncMock(side_effect=RuntimeError("LLM down"))
        messages = [{"role": "user", "content": "test"}]

        with pytest.raises(RuntimeError, match="LLM down"):
            await retry_with_validation(
                chat_fn=chat_fn,
                messages=messages,
                model_cls=SampleItem,
                max_retries=1,
            )
