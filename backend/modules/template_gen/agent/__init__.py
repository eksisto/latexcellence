"""模板生成自主 Agent 包（对外导入路径与原先 agent.py 保持一致）。"""

from backend.modules.template_gen.trace import append_trace, now_iso
from backend.utils.tex_compile import run_tex_compile

from .compile import (
    _attach_source_context,
    _error_hints,
    _extract_errors,
    _extract_quality_warnings,
    _extract_unresolved_reference_errors,
    _format_error_analysis,
    _format_quality_feedback,
    _normalize_log_file,
    _read_log_text,
    _trim_log,
)
from .constants import (
    _ALLOWED_READ_SUFFIXES,
    _ALLOWED_SUFFIXES,
    _LATEX_ENV_CHECK_SUFFIXES,
    _LATEX_SOURCE_SUFFIXES,
    _LOG_TRUNCATE,
)
from .executor import _ToolExecutor
from .loop import _compact_messages, _content_hash, _nudge_message, _summarize_args
from .sandbox import load_sandbox, resolve_sandbox_path, safe_read_sandbox_file
from .service import TemplateAgentService, get_template_agent_service
from .validation import (
    _check_body_style_commands,
    _check_cjk_support,
    _check_class_option_order,
    _check_provides_date,
    _check_public_body_commands,
    _check_setter_accessors,
    _cjk_dependency_candidates,
    _local_dependencies_provide_cjk,
    _strip_latex_comments,
)

__all__ = [
    "TemplateAgentService",
    "_ToolExecutor",
    "get_template_agent_service",
    "load_sandbox",
    "safe_read_sandbox_file",
    "resolve_sandbox_path",
    "_compact_messages",
    "_content_hash",
    "_nudge_message",
    "_summarize_args",
    "_check_body_style_commands",
    "_check_cjk_support",
    "_check_class_option_order",
    "_check_provides_date",
    "_check_public_body_commands",
    "_check_setter_accessors",
    "_cjk_dependency_candidates",
    "_local_dependencies_provide_cjk",
    "_strip_latex_comments",
    "_attach_source_context",
    "_error_hints",
    "_extract_errors",
    "_extract_quality_warnings",
    "_extract_unresolved_reference_errors",
    "_format_error_analysis",
    "_format_quality_feedback",
    "_normalize_log_file",
    "_read_log_text",
    "_trim_log",
    "_ALLOWED_SUFFIXES",
    "_ALLOWED_READ_SUFFIXES",
    "_LATEX_ENV_CHECK_SUFFIXES",
    "_LATEX_SOURCE_SUFFIXES",
    "_LOG_TRUNCATE",
    "append_trace",
    "now_iso",
    "run_tex_compile",
]
