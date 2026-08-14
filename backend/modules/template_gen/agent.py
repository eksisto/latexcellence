"""
模板生成自主 Agent。

设计目标：
    基于 Function Calling 的自主 Agent 生成完整 LaTeX 工程：
    Agent 自主规划文件结构 → 逐个 write_file → 调用 compile_latex 验证 →
    读取编译日志 → 修正错误 → 再编译，最多 N 轮，最终产出一个完整可编译的多文件
    LaTeX 项目。

模块分层：
    本文件包含 Function Calling Agent 主循环、工具 schema 构建与工具执行器；
    沙箱创建与业务编排在 service.py（TemplateGenService）中完成，
    service.generate() 创建沙箱后调用本类的 generate() 驱动 Agent 循环。

工具体系（session 级私有）：
    工具 schema 由本类动态构建并随 chat_with_tools 注入，执行函数在沙箱目录内
    操作。不注册到全局 ToolRegistry——因为这些工具绑定到一次性沙箱目录，注册成
    全局能力会污染其他模块（如 writing_chat）的工具集。

沙箱与路径安全：
    每次生成由 service.py 在 `output_dir/<session_id>/` 下创建独立目录，
    所有 file_path 参数经 _resolve_sandbox_path 校验：禁止绝对路径、禁止 '..'、
    解析后必须仍在沙箱内、扩展名白名单限制为 LaTeX 相关文件。

事件流（SSE）：
    {"type":"plan","content":"..."}               Agent 的思考/说明文本
    {"type":"tool_call","tool":"...","args":{...}} 工具调用开始
    {"type":"tool_result","tool":"...","ok":bool,"summary":"..."} 工具执行结果
    {"type":"compile","success":bool,"log":"...","errors":[...]} 编译结果（含结构化错误列表）
    {"type":"warning","message":"..."}            Agent 行为可疑（如编译未过却 finish）
    {"type":"done","session_id":"...","files":[...],"summary":"..."} 完成
    {"type":"error","message":"..."}               错误终止

编译错误修复回路：
    compile_latex 失败时，日志经 _extract_errors() 解析为结构化错误列表
    （type/file/line/detail），再由 _format_error_analysis() 拼成聚焦错误
    摘要回传 LLM。prompt 强制 LLM 走 ANALYZE→READ→FIX→RECOMPILE 循环，
    最新一次编译失败时不允许 finish。循环跟踪 last_compile_success 状态，
    若 Agent 在编译未通过时仍调用 finish，则推送 warning 事件提示前端。

运行追踪：
    每次生成由 trace.py 将 Agent loop 的完整交互（system prompt、LLM 回复、
    工具调用参数与结果、警告与错误）按时间顺序追加到沙箱 trace.jsonl，
    供分析、改进 prompt 与复现调试使用。
"""

import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.core.llm_client import get_llm_client
from backend.core.prompts import get_prompt
from backend.modules.template_gen.trace import append_trace, now_iso
from backend.utils.agent_file_tools import (
    AgentFileRead,
    AgentFileToolExecutor,
    AgentFileToolFailure,
    AgentFileWrite,
    build_agent_file_tools_schema,
    normalize_line_range,
)
from backend.utils.latex_json_guard import (
    check_latex_environments,
    check_latex_text,
    check_latex_text_lite,
)
from backend.utils.latex_literals import blank_literal_spans
from backend.utils.path_guard import resolve_project_path
from backend.utils.tex_compile import run_tex_compile

# 允许在沙箱内创建的文件扩展名白名单
_ALLOWED_SUFFIXES = (
    ".tex",
    ".sty",
    ".cls",
    ".bib",
    ".md",
    ".txt",
    ".latex",
    ".ltx",
    ".cfg",
    ".bst",
    ".def",
)

# read_file / 文件读取端点额外允许的生成文件扩展名（只读诊断用，
# 例如编译失败后读 main.log 定位根因；写入仍受 _ALLOWED_SUFFIXES 限制）
_ALLOWED_READ_SUFFIXES = _ALLOWED_SUFFIXES + (
    ".log",
    ".aux",
    ".toc",
    ".lof",
    ".lot",
    ".out",
    ".blg",
    ".bbl",
    ".bcf",
    ".run.xml",
    ".fls",
    ".fdb_latexmk",
    ".xdv",
    ".nav",
    ".snm",
    ".vrb",
    ".synctex.gz",
)

# 参与项目源码快照与静态环境检查的扩展名。生成的诊断文件不应使
# "源码是否变化"判断失真。
_LATEX_SOURCE_SUFFIXES = frozenset(
    {
        ".tex",
        ".sty",
        ".cls",
        ".bib",
        ".cfg",
        ".bst",
        ".def",
        ".ltx",
        ".latex",
    }
)
# 只对正文源文件做整文件环境配对检查。`.sty`/`.cls` 经常把环境的打开
# 与关闭分别放在 beamer/template hook 或 begin/end 宏中，按文件顺序扫描会
# 将这种合法的跨宏配对误判为错误；包代码仍由实际编译验证。
_LATEX_ENV_CHECK_SUFFIXES = frozenset({".tex", ".ltx", ".latex"})

# 正文文件在 TeX 中读取时 `@` 不是字母。把类/样式内部的实现宏泄漏到
# `.tex` 会将 `\@private@name` 拆成 `\@` + 普通文本，常见结果是
# `\spacefactor in vertical mode` 或一串看似无关的 undefined control
# sequence。这里仅拦截控制序列形式，不拦截普通正文中的邮箱地址等字面 `@`。
_PRIVATE_BODY_COMMAND_RE = re.compile(r"\\@(?:[A-Za-z@]+)")
# `\ProvidesClass` / `\ProvidesPackage` 的可选参数必须以 LaTeX 内核要求的
# YYYY/MM/DD 日期开头；写 `[2024/01 Master Thesis Template]` 这类值会把
# 后面的 "Master" 当作 `\ifnum` 的数字解析，在 `\documentclass` 处产生
# 非常难定位的 "Missing = inserted for \ifnum"。
_PROVIDES_STATEMENT_RE = re.compile(
    r"\\(?:ProvidesClass|ProvidesPackage)\s*\{[^{}]*\}\s*\[([^\]]*)\]"
)
_VALID_PROVIDES_DATE_RE = re.compile(r"^\s*\d{4}/\d{1,2}/\d{1,2}\b")
# 正文文件出现样式/命令定义时，说明格式没有全部收敛到 .cls/.sty。
_BODY_STYLE_COMMAND_RE = re.compile(
    r"\\(?:newcommand|renewcommand|newenvironment|renewenvironment|"
    r"g?def|setlength|addtolength|titleformat|titlespacing|geometry|"
    r"setmainfont|setsansfont|setCJKmainfont|setCJKsansfont|pagestyle|"
    r"hypersetup|ctexset)"
)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_CJK_PACKAGE_RE = re.compile(
    r"\\(?:usepackage|documentclass|RequirePackage)"
    r"(?:\[[^\]]*\])?\s*\{[^}]*\b(?:ctex[a-z]*|xecjk)\b",
    re.IGNORECASE,
)
# 本地样式/主题/输入文件可能提供 CJK 支持，需要实际解析这些依赖后再判断；
# 不能因为出现了 `\usepackage{graphicx}` 就默认“有人会负责加载 ctex”。
_CJK_DEPENDENCY_RE = re.compile(
    r"\\(?P<cmd>usepackage|RequirePackage|input|include|subfile|LoadClass|"
    r"usetheme|usecolortheme|usefonttheme)"
    r"(?:\s*\[[^\]]*\])?\s*\{(?P<name>[^{}]+)\}"
)
_SETTER_ACCESSOR_RE = re.compile(
    r"\\newcommand\{\\([A-Za-z@]+)\}\s*(?:\[[^\]]*\])?\s*"
    r"\{\\renewcommand\{\\([A-Za-z@]+)\}\{#1\}"
)


def _strip_latex_comments(text: str) -> list[str]:
    """返回去掉未转义注释后的行，保留行号以便给 Agent 可操作反馈。"""
    result: list[str] = []
    for line in text.splitlines():
        comment_at: int | None = None
        for index, char in enumerate(line):
            if char != "%":
                continue
            backslashes = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                comment_at = index
                break
        result.append(line if comment_at is None else line[:comment_at])
    return result


def _check_public_body_commands(content: str) -> str | None:
    """拒绝正文使用类/样式内部的 `@` 控制序列。"""
    for line_number, line in enumerate(_strip_latex_comments(content), 1):
        match = _PRIVATE_BODY_COMMAND_RE.search(line)
        if match:
            return (
                f"LaTeX body uses private control sequence "
                f"'{match.group(0)}' at line {line_number}. Commands containing "
                "@ are implementation details for .cls/.sty files; define a "
                "public accessor or public rendering command in the class/style "
                "and use that command from the .tex file."
            )
    return None


def _check_provides_date(content: str) -> str | None:
    """校验 .cls/.sty 的 \\ProvidesClass/\\ProvidesPackage 日期参数。"""
    match = _PROVIDES_STATEMENT_RE.search(content)
    if not match:
        return None
    optional = match.group(1)
    if _VALID_PROVIDES_DATE_RE.match(optional):
        return None
    return (
        "The optional argument of \\ProvidesClass/\\ProvidesPackage must "
        "start with a YYYY/MM/DD date (e.g. [2024/01/01 Template]); the "
        f"current value '{optional.strip()}' makes LaTeX parse the following "
        "text as an integer and fail with 'Missing = inserted for \\ifnum'. "
        "Use a full date such as [2024/01/01] or omit the optional argument."
    )


def _check_body_style_commands(content: str) -> str | None:
    """正文 .tex 不允许定义/重定义样式命令（格式应封装在 .cls/.sty）。"""
    for line_number, line in enumerate(_strip_latex_comments(content), 1):
        match = _BODY_STYLE_COMMAND_RE.search(line)
        if match:
            return (
                f"Body file defines or redefines the style command "
                f"'{match.group(0)}' at line {line_number}. All styling must "
                "live in the .cls/.sty file; body files should only call "
                "semantic commands. Move this definition into the class/style "
                "and call a public command from the body."
            )
    return None


def _cjk_dependency_candidates(
    name: str, command: str, current_dir: Path, root: Path
) -> list[Path]:
    r"""把 `\usepackage` / `\usetheme` / `\input` 等引用解析成本地候选文件。"""
    name = name.strip()
    if not name or name.startswith(("/", "\\")):
        return []

    relative = Path(name)
    bases = [current_dir / relative, root / relative]
    candidates: list[Path] = []

    if command in {"input", "include", "subfile"}:
        for base in bases:
            if base.suffix.lower() in _LATEX_SOURCE_SUFFIXES:
                candidates.append(base)
            else:
                candidates.append(base.with_suffix(".tex"))
    elif command in {"usepackage", "RequirePackage"}:
        for base in bases:
            candidates.append(Path(str(base) + ".sty"))
            candidates.append(Path(str(base) + ".cls"))
    elif command == "usetheme":
        for base in bases:
            candidates.append(base.parent / f"beamertheme{base.name}.sty")
            candidates.append(Path(str(base) + ".sty"))
    elif command == "usecolortheme":
        for base in bases:
            candidates.append(base.parent / f"beamercolortheme{base.name}.sty")
            candidates.append(Path(str(base) + ".sty"))
    elif command == "usefonttheme":
        for base in bases:
            candidates.append(base.parent / f"beamerfonttheme{base.name}.sty")
            candidates.append(Path(str(base) + ".sty"))
    else:  # LoadClass
        for base in bases:
            candidates.append(Path(str(base) + ".cls"))

    return candidates


def _local_dependencies_provide_cjk(
    content: str, project_root: Path, current_path: Path
) -> bool:
    """递归检查当前文件引用的本地依赖是否实际加载 ctex/xeCJK。"""
    root = project_root.resolve()
    current_dir = current_path.resolve().parent
    queue: list[Path] = []
    code = "\n".join(_strip_latex_comments(blank_literal_spans(content)))
    for match in _CJK_DEPENDENCY_RE.finditer(code):
        for raw_name in match.group("name").split(","):
            queue.extend(
                _cjk_dependency_candidates(
                    raw_name, match.group("cmd"), current_dir, root
                )
            )

    seen: set[Path] = set()
    while queue:
        candidate = queue.pop(0)
        try:
            candidate = candidate.resolve()
        except OSError:
            continue
        if (
            candidate in seen
            or not candidate.is_file()
            or not candidate.is_relative_to(root)
        ):
            continue
        seen.add(candidate)
        try:
            file_text = candidate.read_text(encoding="utf-8", errors="replace")
            dep_code = "\n".join(_strip_latex_comments(blank_literal_spans(file_text)))
        except OSError:
            continue
        if _CJK_PACKAGE_RE.search(dep_code):
            return True
        for match in _CJK_DEPENDENCY_RE.finditer(dep_code):
            for raw_name in match.group("name").split(","):
                queue.extend(
                    _cjk_dependency_candidates(
                        raw_name,
                        match.group("cmd"),
                        candidate.parent,
                        root,
                    )
                )
    return False


def _check_cjk_support(
    content: str,
    *,
    project_root: Path | None = None,
    current_path: Path | None = None,
) -> str | None:
    r"""beamer 主文件含中文但未加载 ctex/xeCJK 时拒绝，避免字形静默缺失。

    普通 article/custom class 的 CJK 支持可能由本地 .sty/.cls 提供，不能
    对每个分章文件做全文件检查，否则会误伤合法工程。检查只针对真正会被
    typeset 的内容：先剥离 verbatim/URL 与注释，避免中文注释或字面量误报；
    若正文引用了本地样式/主题/输入文件，则递归检查这些文件是否确实加载
    ctex/xeCJK；不能因为存在任意 `\usepackage` 就默认 CJK 已有人负责。
    """
    code = "\n".join(_strip_latex_comments(blank_literal_spans(content)))
    if not _CJK_RE.search(code):
        return None
    if _CJK_PACKAGE_RE.search(code):
        return None
    if not re.search(r"\\documentclass(?:\[[^\]]*\])?\s*\{[^}]*beamer", code):
        return None
    if project_root is not None and current_path is not None:
        if _local_dependencies_provide_cjk(content, project_root, current_path):
            return None
    return (
        "This file contains Chinese/CJK text but does not load ctex or "
        "xeCJK, so every CJK glyph will be missing from the PDF. Add "
        "\\usepackage{ctex} (or \\usepackage{xeCJK}) to the preamble/class "
        "before any Chinese content and rely on its default fontset."
    )


def _check_class_option_order(content: str) -> str | None:
    """\\DeclareOption/\\ExecuteOptions 必须出现在 \\ProcessOptions 之前。"""
    code = "\n".join(_strip_latex_comments(content))
    process_pos = code.find("\\ProcessOptions")
    if process_pos < 0:
        return None
    for keyword in ("\\DeclareOption", "\\ExecuteOptions"):
        pos = code.find(keyword)
        while pos >= 0:
            if pos > process_pos:
                return (
                    f"{keyword} appears after \\ProcessOptions in the class "
                    "options section. LaTeX only honors class options declared "
                    "before \\ProcessOptions; move every \\DeclareOption and "
                    "\\ExecuteOptions above it, then \\LoadClass, then packages."
                )
            pos = code.find(keyword, pos + 1)
    return None


def _check_setter_accessors(content: str) -> str | None:
    """公开 setter 的 \\renewcommand 目标必须已在类/样式中定义。"""
    code = "\n".join(_strip_latex_comments(content))
    for match in _SETTER_ACCESSOR_RE.finditer(code):
        setter_name, accessor_name = match.group(1), match.group(2)
        if re.search(rf"\\newcommand\{{\\{accessor_name}\}}", code) or re.search(
            rf"\\providecommand\{{\\{accessor_name}\}}", code
        ):
            continue
        return (
            f"Public setter \\{setter_name} uses "
            f"\\renewcommand{{\\{accessor_name}}}, but \\{accessor_name} is "
            "never defined in this file. Define a public accessor default "
            "with \\newcommand (for example \\newcommand{\\universityvalue}{}) "
            "before or after the setter, otherwise calling the setter from "
            "main.tex fails with 'Command undefined'."
        )
    return None


def _content_hash(content: str) -> str:
    """计算写入内容的 SHA-256，用于识别"原样重发被拒内容"的退化循环。"""
    import hashlib

    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def _compact_messages(
    messages: list[dict[str, Any]], *, keep_rounds: int = 12
) -> list[dict[str, Any]]:
    """压缩 Agent 对话历史，防止超长工具调用把上下文撑爆。

    保留 system 与最初的 user 需求，然后只保留最近 keep_rounds 个
    assistant 轮次及其随后的 tool 结果。更早的轮次替换为一条摘要提示，
    避免旧的大文件 tool_call 参数继续占用上下文。
    """
    if len(messages) <= 4:
        return messages
    assistant_indices = [
        i for i, message in enumerate(messages) if message.get("role") == "assistant"
    ]
    if len(assistant_indices) <= keep_rounds:
        return messages
    cut = assistant_indices[-keep_rounds]
    prefix = messages[:2]
    summary = {
        "role": "user",
        "content": (
            "[Earlier generation rounds were compacted to fit the context "
            "window. The current project files on disk are the source of "
            "truth; continue from that state and call the next tool.]"
        ),
    }
    return prefix + [summary] + messages[cut:]


# 编译日志回传给 LLM 时的截断长度（字符）
_LOG_TRUNCATE = 4000


class TemplateAgentService:
    """模板生成自主 Agent 服务（单例）。"""

    def __init__(self) -> None:
        self._logger = get_logger("template_gen.agent")

    async def _append_trace(self, sandbox: Path, event: dict[str, Any]) -> None:
        """追加追踪事件；写入失败仅记日志，不中断生成主流程。"""
        try:
            await append_trace(sandbox, event)
        except Exception as e:
            self._logger.warning(f"Failed to append trace event: {e}")

    # ── 公共入口 ─────────────────────────────────────────────────────────

    async def generate(
        self,
        description: str,
        language: str = "zh",
        *,
        sandbox: Path,
        llm: Any | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        自主生成 LaTeX 模板项目，SSE 事件流。

        参数：
            description: 用户对模板的自然语言描述
            language: 输出语言（注入 system prompt）
            sandbox: 由 service.py 创建的沙箱目录（`output_dir/<session_id>/`）
            llm: 可选 LLM 客户端（测试注入用）
        """
        cfg = get_config()
        if not cfg.llm.api_key:
            yield {"type": "error", "message": "No API key configured"}
            return

        if llm is None:
            llm = get_llm_client()

        session_id = sandbox.name

        # 1. 构造工具 schema 与执行器
        tools = self._build_tools_schema(cfg)
        executor = _ToolExecutor(sandbox, cfg, self._logger)

        # 2. 构造 messages
        system_prompt = get_prompt(
            "template_gen",
            "agent_system",
            language=language,
            max_rounds=cfg.features.template_gen.agent_max_rounds,
            max_files=cfg.features.template_gen.agent_max_files,
            compile_enabled=(
                "available"
                if cfg.features.template_gen.agent_compile_enabled
                else "unavailable"
            ),
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Generate a complete, compilable LaTeX project for the "
                    f"following requirement:\n\n{description}\n\n"
                    f"Respond in {language} for any explanatory text."
                ),
            },
        ]

        # 3. Agent 循环
        max_rounds = cfg.features.template_gen.agent_max_rounds
        final_summary = ""
        # 跟踪编译状态：不允许 Agent 在最后一次编译仍失败时静默 finish
        compile_state = {"called": False, "success": None}
        run_id = uuid.uuid4().hex[:12]
        rounds_used = 0
        # 连续出现"只输出文本、不调用工具"的次数（规划瘫痪防护）
        no_tool_strikes = 0
        # 连续重发同一份被拒内容时的防呆：同一 (file, content) 失败 3 次后
        # 注入显式纠正指令，打断模型"原样重发"的退化循环。
        last_fail_key: tuple[str, str] | None = None
        repeat_failures = 0
        injected_fail_keys: set[tuple[str, str]] = set()
        # 同一文件连续被拒（不限内容哈希）的防呆：内容哈希变化会让上面的
        # 计数器不断重置，导致"近似重发"绕过干预；这里按文件路径累计，
        # 连续 5 次被拒后强制要求简化/重写该文件。
        last_fail_file: str | None = None
        file_repeat_failures = 0
        injected_file_keys: set[str] = set()
        # 连续出现同一编译错误签名时的防呆：3 次后注入显式纠正指令，
        # 打断"同一根因反复试错"的循环。
        last_compile_key: str | None = None
        repeat_compiles = 0
        injected_compile_keys: set[str] = set()
        # compile_latex 在源码未变化时会快速拒绝。模型有时会忽略该工具
        # 反馈，在连续几轮中重复提交同一个 compile call；单独计数，便于
        # 给出更强的动作性干预，而不是让这种无效调用消耗整个轮次预算。
        compile_skip_strikes = 0
        # 不同编译错误签名总数：超过阈值说明项目存在多个独立缺陷，
        # 逐个修补效率太低，应系统性重写/拆分。
        distinct_compile_keys: set[str] = set()
        distinct_guidance_injected = False
        # 首次编译之后连续写文件却不编译的防呆：项目搭建阶段大量首写是
        # 正常的，但编译失败后的"反复重写同一文件、不验证"会烧光轮次。
        writes_without_compile = 0
        same_file_writes = 0
        last_write_file: str | None = None
        injected_write_loop_thresholds: set[int] = set()
        injected_same_file_keys: set[str] = set()
        # 连续对"已成功编译且源码未变"的项目重复调用 compile 的空转防呆。
        success_recompile_strikes = 0
        injected_success_recompile = False
        # 连续多次成功编译但始终不 finish 的防呆：模型可能在追非致命
        # 质量警告（overfull box 等）上无限打磨，烧光轮次预算。
        consecutive_successes = 0
        injected_finish_nudge = False

        await self._append_trace(
            sandbox,
            {
                "ts": now_iso(),
                "run_id": run_id,
                "type": "run_start",
                "data": {
                    "description": description,
                    "language": language,
                    "system_prompt": system_prompt,
                    "messages": messages,
                    "max_rounds": max_rounds,
                },
            },
        )

        for round_idx in range(max_rounds):
            rounds_used = round_idx + 1
            if round_idx == 0:
                yield {
                    "type": "plan",
                    "content": f"Start generating (up to {max_rounds} rounds)...",
                }

            try:
                if len(messages) > 20:
                    total_chars = sum(
                        len(json.dumps(message, ensure_ascii=False))
                        for message in messages
                    )
                    if total_chars > 600_000:
                        messages = _compact_messages(messages)
                        await self._append_trace(
                            sandbox,
                            {
                                "ts": now_iso(),
                                "run_id": run_id,
                                "type": "nudge",
                                "data": {
                                    "kind": "context_compacted",
                                    "message": (
                                        "Conversation history was compacted "
                                        "because it exceeded the context "
                                        "budget; earlier rounds were replaced "
                                        "with a summary."
                                    ),
                                },
                            },
                        )
                response = await llm.chat_with_tools(
                    messages=messages,
                    tools=tools,
                    temperature=cfg.llm.temperature,
                    # 模板文件（尤其 .cls/.sty）常接近 10KB，若 max_tokens 太小可能被截断
                    max_tokens=max(cfg.llm.max_tokens, 8192),
                )
            except Exception as e:
                self._logger.error(
                    f"chat_with_tools failed (round {round_idx + 1}): {e}"
                )
                await self._append_trace(
                    sandbox,
                    {
                        "ts": now_iso(),
                        "run_id": run_id,
                        "type": "error",
                        "data": {"message": str(e)},
                    },
                )
                yield {"type": "error", "message": str(e)}
                return

            choice = response.choices[0]
            msg = choice.message
            tool_calls = msg.tool_calls

            # 推送 LLM 的思考文本（若有）
            if msg.content:
                yield {"type": "plan", "content": msg.content}
                final_summary = msg.content

            await self._append_trace(
                sandbox,
                {
                    "ts": now_iso(),
                    "run_id": run_id,
                    "type": "llm",
                    "data": {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": getattr(tc, "type", "function"),
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in (tool_calls or [])
                        ],
                    },
                },
            )

            if not tool_calls:
                # LLM 只输出文本未调用工具：可能是"规划瘫痪"（长篇思考却不行动）。
                # 引导最多 2 次后仍无工具调用，才视为异常结束。
                if no_tool_strikes < 2 and round_idx < max_rounds - 1:
                    no_tool_strikes += 1
                    nudge = _nudge_message()
                    messages.append(msg.model_dump())
                    messages.append({"role": "user", "content": nudge})
                    await self._append_trace(
                        sandbox,
                        {
                            "ts": now_iso(),
                            "run_id": run_id,
                            "type": "nudge",
                            "data": {"strike": no_tool_strikes, "message": nudge},
                        },
                    )
                    continue
                break

            # 将 assistant 含 tool_calls 的消息追加进历史
            messages.append(msg.model_dump())

            # 本轮是否触发了 finish（finish 后立即跳出循环）
            finish_this_round = False
            # 依次执行每个 tool_call
            for tc in tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments)
                except Exception:
                    tool_args = {}

                yield {
                    "type": "tool_call",
                    "tool": tool_name,
                    "args": _summarize_args(tool_name, tool_args),
                }

                try:
                    result_text, result_event = await executor.execute(
                        tool_name,
                        tool_args,
                    )
                except Exception as e:
                    self._logger.warning(f"Tool execution exception {tool_name}: {e}")
                    result_text = f"Tool execution failed: {e}"
                    result_event = {
                        "tool": tool_name,
                        "ok": False,
                        "summary": result_text,
                    }

                await self._append_trace(
                    sandbox,
                    {
                        "ts": now_iso(),
                        "run_id": run_id,
                        "type": "tool_result",
                        "data": {
                            "tool": tool_name,
                            "args": tool_args,
                            "ok": result_event.get("ok"),
                            "result_text": result_text,
                            "event": result_event,
                        },
                    },
                )

                # 编译单独事件，便于前端高亮展示
                if tool_name == "compile_latex":
                    compile_state["called"] = True
                    compile_state["success"] = result_event.get("success")
                    yield {"type": "compile", **result_event}
                else:
                    yield {"type": "tool_result", **result_event}

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    }
                )

                # 编译工具可能返回"源码未变化"的快速失败结果。此时不要让
                # LLM 继续消耗真实编译机会，明确要求它先修改根因。
                if tool_name == "compile_latex" and result_event.get("compile_skipped"):
                    compile_skip_strikes += 1
                    guidance = (
                        "[SYSTEM INTERVENTION] compile_latex was skipped because "
                        "the project source is byte-identical to the previous "
                        "failed attempt. Do not call compile_latex again without "
                        "a meaningful source change. Read the reported source "
                        "excerpt and log context, fix the root cause in the "
                        "project file, then compile once. Never remove a third-"
                        "party package merely because its path appears in a TeX "
                        "stack trace. This is skip strike "
                        f"{compile_skip_strikes}; the next tool call must be "
                        "read_file or write_file, not compile_latex."
                    )
                    messages.append({"role": "user", "content": guidance})
                    await self._append_trace(
                        sandbox,
                        {
                            "ts": now_iso(),
                            "run_id": run_id,
                            "type": "nudge",
                            "data": {
                                "kind": "unchanged_compile",
                                "message": guidance,
                            },
                        },
                    )
                elif tool_name == "write_file" and result_event.get("ok"):
                    # 一次成功的源码写入打断 compile-skip 计数；失败的写入
                    # 不应伪装成已经改变了编译输入。
                    compile_skip_strikes = 0
                    success_recompile_strikes = 0
                    # 只有已经尝试过编译后才跟踪"写而不编"；项目初始搭建
                    # 阶段连续创建十几个文件是正常流程，不应打断。
                    if compile_state["called"]:
                        writes_without_compile += 1
                        write_path = str(tool_args.get("file_path") or "")
                        if write_path:
                            if write_path == last_write_file:
                                same_file_writes += 1
                            else:
                                last_write_file = write_path
                                same_file_writes = 1
                        guidance_parts: list[str] = []
                        if (
                            same_file_writes >= 5
                            and write_path
                            and write_path not in injected_same_file_keys
                        ):
                            injected_same_file_keys.add(write_path)
                            guidance_parts.append(
                                f"You have rewritten '{write_path}' "
                                f"{same_file_writes} times since the last "
                                "compile_latex call."
                            )
                        if (
                            writes_without_compile >= 5
                            and writes_without_compile
                            not in injected_write_loop_thresholds
                        ):
                            injected_write_loop_thresholds.add(writes_without_compile)
                            guidance_parts.append(
                                f"You have made {writes_without_compile} "
                                "successful file writes since the last "
                                "compile_latex call."
                            )
                        if guidance_parts:
                            guidance = (
                                "[SYSTEM INTERVENTION] "
                                + " ".join(guidance_parts)
                                + " STOP editing unverified files. Call "
                                "compile_latex NOW to check the current "
                                "project; if it fails, read the reported "
                                "source excerpt and log (e.g. read_file "
                                "main.log), fix the root cause ONCE, then "
                                "compile again. Unverified rewrites do not "
                                "count as progress."
                            )
                            messages.append({"role": "user", "content": guidance})
                            await self._append_trace(
                                sandbox,
                                {
                                    "ts": now_iso(),
                                    "run_id": run_id,
                                    "type": "nudge",
                                    "data": {
                                        "kind": "write_without_compile",
                                        "message": guidance,
                                    },
                                },
                            )
                if tool_name == "compile_latex":
                    # 无论成功/失败/跳过，一次编译尝试都视为验证动作，
                    # 重置"写而不编"计数。
                    writes_without_compile = 0
                    same_file_writes = 0
                    last_write_file = None
                    if result_event.get("success") is True and not result_event.get(
                        "already_verified"
                    ):
                        consecutive_successes += 1
                        if consecutive_successes >= 4 and not injected_finish_nudge:
                            injected_finish_nudge = True
                            guidance = (
                                "[SYSTEM INTERVENTION] You have produced "
                                f"{consecutive_successes} consecutive "
                                "successful compiles. Non-fatal quality "
                                "warnings (overfull boxes, font shape "
                                "fallbacks, etc.) do NOT block delivery. "
                                "Unless you have identified a specific new "
                                "defect to fix, call finish now with the "
                                "main file and a summary; polishing "
                                "warnings indefinitely will exhaust the "
                                "round budget."
                            )
                            messages.append({"role": "user", "content": guidance})
                            await self._append_trace(
                                sandbox,
                                {
                                    "ts": now_iso(),
                                    "run_id": run_id,
                                    "type": "nudge",
                                    "data": {
                                        "kind": "finish_after_successes",
                                        "message": guidance,
                                    },
                                },
                            )
                    elif result_event.get("success") is False:
                        consecutive_successes = 0
                    if result_event.get("already_verified"):
                        success_recompile_strikes += 1
                        if (
                            success_recompile_strikes >= 2
                            and not injected_success_recompile
                        ):
                            injected_success_recompile = True
                            guidance = (
                                "[SYSTEM INTERVENTION] You have called "
                                "compile_latex "
                                f"{success_recompile_strikes} times without "
                                "changing the source, and the project already "
                                "compiled successfully. Do NOT recompile "
                                "again. Call finish now (with main_file and "
                                "a summary), or make a real source change "
                                "first."
                            )
                            messages.append({"role": "user", "content": guidance})
                            await self._append_trace(
                                sandbox,
                                {
                                    "ts": now_iso(),
                                    "run_id": run_id,
                                    "type": "nudge",
                                    "data": {
                                        "kind": "success_recompile_loop",
                                        "message": guidance,
                                    },
                                },
                            )

                # 同一份被拒内容重发 3 次 → 显式纠正（防退化循环）
                if tool_name == "write_file" and result_event.get("ok") is False:
                    content_hash = _content_hash(tool_args.get("content", ""))
                    fail_key = (str(tool_args.get("file_path", "")), content_hash)
                    if fail_key == last_fail_key:
                        repeat_failures += 1
                    else:
                        last_fail_key = fail_key
                        repeat_failures = 1
                    if repeat_failures >= 3 and fail_key not in injected_fail_keys:
                        injected_fail_keys.add(fail_key)
                        guidance = (
                            "[SYSTEM INTERVENTION] Your last "
                            f"{repeat_failures} write_file calls for "
                            f"'{tool_args.get('file_path')}' were rejected "
                            f"for the SAME reason: {result_text[:300]}. "
                            "Do NOT resend identical or near-identical "
                            "content — it will be rejected again. Rewrite "
                            "the file completely with balanced braces, or "
                            "split it into smaller files, then call "
                            "write_file again."
                        )
                        messages.append({"role": "user", "content": guidance})
                        await self._append_trace(
                            sandbox,
                            {
                                "ts": now_iso(),
                                "run_id": run_id,
                                "type": "nudge",
                                "data": {
                                    "kind": "repeated_rejected_write",
                                    "message": guidance,
                                },
                            },
                        )
                    # 文件级干预：同一路径连续被拒 5 次 → 强制简化/重写
                    fail_file = str(tool_args.get("file_path") or "")
                    if fail_file:
                        if fail_file == last_fail_file:
                            file_repeat_failures += 1
                        else:
                            last_fail_file = fail_file
                            file_repeat_failures = 1
                    if (
                        file_repeat_failures >= 5
                        and fail_file not in injected_file_keys
                    ):
                        injected_file_keys.add(fail_file)
                        guidance = (
                            f"[SYSTEM INTERVENTION] '{fail_file}' has been "
                            f"rejected {file_repeat_failures} times in a row "
                            "for structural problems (unbalanced braces / "
                            "corrupt content). STOP resending modified "
                            "versions of the same large file. Fix the exact "
                            "line reported in the rejection, or rewrite the "
                            "file as a MUCH SIMPLER version: remove "
                            "conditional \\ifx/\\ifnum icon chains, use plain "
                            "text instead of icon macros, and keep custom "
                            "commands minimal. If it is still too large, "
                            "split it into smaller .sty files. Do NOT resend "
                            "the same structure again."
                        )
                        messages.append({"role": "user", "content": guidance})
                        await self._append_trace(
                            sandbox,
                            {
                                "ts": now_iso(),
                                "run_id": run_id,
                                "type": "nudge",
                                "data": {
                                    "kind": "repeated_file_rejection",
                                    "message": guidance,
                                },
                            },
                        )

                # 同一编译错误连续 3 次 → 显式纠正（防根因循环）
                if (
                    tool_name == "compile_latex"
                    and result_event.get("success") is False
                ):
                    errs = result_event.get("errors") or []
                    if errs:
                        first = errs[0]
                        compile_key = (
                            f"{first.get('type')}|{first.get('file')}|"
                            f"{first.get('line')}|{first.get('detail')}"
                        )
                    else:
                        compile_key = "NO_ERRORS"
                    distinct_compile_keys.add(compile_key)
                    if compile_key == last_compile_key:
                        repeat_compiles += 1
                    else:
                        last_compile_key = compile_key
                        repeat_compiles = 1
                    if (
                        repeat_compiles >= 3
                        and compile_key not in injected_compile_keys
                    ):
                        injected_compile_keys.add(compile_key)
                        guidance = (
                            f"[SYSTEM INTERVENTION] Your last "
                            f"{repeat_compiles} compile_latex calls failed "
                            f"with the SAME error. Stop symptom patching: "
                            "read the generated log with read_file (e.g. "
                            "'read_file main.log') and read the source file "
                            "named in the error, then rewrite the "
                            "root-cause block ONCE. If the error mentions a "
                            "font, remove explicit CJK font commands and let "
                            "ctex's default fontset handle it. If it is a "
                            "'spacefactor' error, replace `\\@...` internals "
                            "in body files with public commands defined in "
                            "the class."
                        )
                        messages.append({"role": "user", "content": guidance})
                        await self._append_trace(
                            sandbox,
                            {
                                "ts": now_iso(),
                                "run_id": run_id,
                                "type": "nudge",
                                "data": {
                                    "kind": "repeated_compile_error",
                                    "message": guidance,
                                },
                            },
                        )
                    if (
                        len(distinct_compile_keys) >= 6
                        and not distinct_guidance_injected
                    ):
                        distinct_guidance_injected = True
                        guidance = (
                            f"[SYSTEM INTERVENTION] You have now hit "
                            f"{len(distinct_compile_keys)} DISTINCT compile "
                            "errors in a row — the project has multiple "
                            "independent defects, and fixing them one at a "
                            "time will exhaust your round budget. STOP. "
                            "Read the entire .cls/.sty and main .tex files, "
                            "then rewrite the class/style ONCE in smaller "
                            ".sty files (or simplify the design: remove "
                            "constructs you cannot verify), and recompile. "
                            "Do not add new features at this point."
                        )
                        messages.append({"role": "user", "content": guidance})
                        await self._append_trace(
                            sandbox,
                            {
                                "ts": now_iso(),
                                "run_id": run_id,
                                "type": "nudge",
                                "data": {
                                    "kind": "many_distinct_compile_errors",
                                    "message": guidance,
                                },
                            },
                        )

                # finish 工具立即终止循环
                if tool_name == "finish":
                    # 校验：若曾被调用过编译且最后一次仍失败，
                    # 不放任 Agent 逃避，推送 warning 提示前端（仍允许结束）
                    if compile_state["called"] and compile_state["success"] is False:
                        warning_msg = (
                            "Agent called finish, but the last compilation still failed. "
                            "The project may not build successfully. Please check the sandbox files manually."
                        )
                        await self._append_trace(
                            sandbox,
                            {
                                "ts": now_iso(),
                                "run_id": run_id,
                                "type": "warning",
                                "data": {"message": warning_msg},
                            },
                        )
                        yield {
                            "type": "warning",
                            "message": warning_msg,
                        }
                    final_summary = tool_args.get("summary", final_summary)
                    finish_this_round = True
                    break

            # finish 已经触发，跳出整体循环
            if finish_this_round:
                self._logger.info(
                    f"Agent finished proactively, ending at round {round_idx + 1}"
                )
                break

        else:
            # for...else：未 break（达到 max_rounds）
            final_summary = (
                final_summary
                or f"Agent reached the maximum number of rounds {max_rounds}, automatically ending."
            )
            yield {
                "type": "plan",
                "content": f"Reached maximum rounds {max_rounds}, stopping generation.",
            }
            # 达到极限时，若仍编译失败，也推送一次 warning
            if compile_state["called"] and compile_state["success"] is False:
                warning_msg = (
                    f"Reached the maximum number of rounds ({max_rounds}), but the last compilation still did not pass. "
                    "The project may not build successfully. Please check the sandbox files manually."
                )
                await self._append_trace(
                    sandbox,
                    {
                        "ts": now_iso(),
                        "run_id": run_id,
                        "type": "warning",
                        "data": {"message": warning_msg},
                    },
                )
                yield {
                    "type": "warning",
                    "message": warning_msg,
                }

        await self._append_trace(
            sandbox,
            {
                "ts": now_iso(),
                "run_id": run_id,
                "type": "run_end",
                "data": {
                    "summary": final_summary,
                    "rounds": rounds_used,
                    "compile_success": compile_state["success"],
                },
            },
        )

        # 4. 汇总文件
        files = executor.list_files()
        yield {
            "type": "done",
            "session_id": session_id,
            "sandbox": str(sandbox),
            "files": files,
            "summary": final_summary,
        }

    # ── 沙箱路径校验 ─────────────────────────────────────────────────────

    @staticmethod
    def _resolve_sandbox_path(
        sandbox: Path, file_path: str, *, for_read: bool = False
    ) -> Path:
        """校验并解析沙箱内相对路径，拒绝绝对路径 / '..' / 越界。

        for_read=True 时允许读取编译生成文件（.log/.aux/.toc 等），
        写入仍严格限制在 _ALLOWED_SUFFIXES 白名单内。
        """
        fp = file_path.replace("\\", "/")
        resolved = resolve_project_path(sandbox, fp, allow_symlink=True)
        allowed = _ALLOWED_READ_SUFFIXES if for_read else _ALLOWED_SUFFIXES
        if resolved.suffix.lower() not in allowed:
            raise ValueError(
                f"file_path has disallowed extension: {resolved.suffix} "
                f"(allowed: {allowed})"
            )
        return resolved

    # ── 工具 schema ─────────────────────────────────────────────────────

    def _build_tools_schema(self, cfg: Any) -> list[dict[str, Any]]:
        """构建 agent 专属工具 schema（session 级私有，不进全局 ToolRegistry）。"""
        tools = build_agent_file_tools_schema(
            supports_line_range=True,
            write_description=(
                "Create or overwrite a file in the project workspace with the "
                "given content. Use forward slashes for paths (e.g. "
                "'chapters/intro.tex'). Create all needed parent directories "
                "implicitly. Only LaTeX-related extensions are allowed."
            ),
            read_description=(
                "Read the current contents of a file already created in the "
                "workspace. Useful before editing to fix compile errors, and "
                "for the pre-finish quality gate (re-check files against the "
                "requirement checklist). Generated diagnostic files "
                "(.log/.aux/.toc/.out/.blg/.fls) can also be read — e.g. "
                "'read_file main.log' gives the full compiler log when the "
                "error summary is unclear. Optional line_range can limit a "
                "source read to the reported error area."
            ),
            list_description=(
                "List all files currently in the workspace with their relative "
                "paths and sizes. Call this after writing files to verify the "
                "project structure."
            ),
            write_path_description=(
                "Relative path within the project, e.g. 'main.tex'."
            ),
            write_content_description=(
                "The full file content to write. LaTeX backslashes are literal "
                "characters; inside this JSON string each one must be escaped "
                "as two backslash characters (\\\\) so JSON parsing does not "
                "corrupt them into control characters."
            ),
            line_range_description=(
                "Optional inclusive 1-based [start, end] line range."
            ),
        )

        if cfg.features.template_gen.agent_compile_enabled:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "compile_latex",
                        "description": (
                            "Compile the project with latexmk -xelatex to verify it "
                            "builds successfully. Returns success status and a trimmed "
                            "log on failure. Call this after writing all files, and "
                            "again after any fix. On success, also inspect the log "
                            "tail for warnings such as undefined references or "
                            "overfull boxes and fix them when present. On failure the "
                            "returned text includes a structured error summary "
                            "(extracted from the build output and, when needed, from "
                            "the generated .log file); you may also call read_file on "
                            "main.log for the full log. Do not loop more than "
                            "necessary."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "main_file": {
                                    "type": "string",
                                    "description": "Relative path to the main .tex file, e.g. 'main.tex'.",
                                },
                            },
                            "required": ["main_file"],
                        },
                    },
                }
            )

        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "finish",
                    "description": (
                        "Signal that the project is complete and ready. Call this "
                        "exactly once at the end, after successful compilation (or "
                        "when compilation is unavailable). Provide a short summary."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "summary": {
                                "type": "string",
                                "description": "A brief summary of the generated project.",
                            },
                            "main_file": {
                                "type": "string",
                                "description": "The main .tex file to compile.",
                            },
                        },
                        "required": ["summary"],
                    },
                },
            }
        )
        return tools


# ── 工具执行器（持有沙箱，无状态可重建） ─────────────────────────────────


class _ToolExecutor(AgentFileToolExecutor):
    """沙箱内工具执行器，封装路径校验、文件 I/O 与编译。"""

    _supports_line_range = True

    def __init__(
        self,
        sandbox: Path,
        cfg: Any,
        logger: Any,
    ) -> None:
        self._sandbox = sandbox
        self._cfg = cfg
        self._logger = logger
        # 已写入的唯一文件路径（相对路径，正斜杠）。预算按"不同文件数"
        # 计算：重写已有文件不消耗预算，防止 Agent 把轮次耗在反复修复
        # 同一批文件上时被预算卡死。
        self._written_paths: set[str] = set()
        # 记录最近一次失败编译对应的源码快照。若源码完全未变，重复编译
        # 只会产生相同结果，直接返回可操作的干预信息，避免浪费超时预算。
        self._last_failed_source_hash: str | None = None
        # 记录最近一次成功编译对应的源码快照。源码未变时重复"成功编译"
        # 同样是空转：项目已经验证过，应该 finish 或先改源码。
        self._last_success_source_hash: str | None = None

    async def execute(
        self, tool_name: str, args: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """执行单个工具，返回 (回传 LLM 的文本, 前端事件 dict)。"""
        if tool_name in self._file_tool_names.values:
            return await self.execute_file_tool(tool_name, args)
        if tool_name == "compile_latex":
            return await self._compile_latex(args)
        if tool_name == "finish":
            return self._finish(args)
        raise ValueError(f"Unknown tool: {tool_name}")

    # ── write_file ────────────────────────────────────────────────────

    def _write_agent_file(
        self,
        file_path: str,
        content: str,
    ) -> AgentFileWrite | AgentFileToolFailure:
        fcfg = self._cfg.features.template_gen

        try:
            resolved = TemplateAgentService._resolve_sandbox_path(
                self._sandbox, file_path
            )
        except ValueError as e:
            return AgentFileToolFailure(
                str(e),
                summary=f"Path validation failed: {e}",
            )

        rel_path = resolved.relative_to(self._sandbox).as_posix()
        if (
            rel_path not in self._written_paths
            and len(self._written_paths) >= fcfg.agent_max_files
        ):
            msg = (
                f"Maximum distinct-file count {fcfg.agent_max_files} reached; "
                f"refusing to create a NEW file {file_path}. You may still "
                "REWRITE (overwrite) existing files to fix them, but do not "
                "create any additional files."
            )
            return AgentFileToolFailure(msg)

        max_bytes = fcfg.agent_max_file_size_kb * 1024
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > max_bytes:
            msg = (
                f"File {file_path} exceeds the maximum size "
                f"of {fcfg.agent_max_file_size_kb} KB"
            )
            return AgentFileToolFailure(msg)

        # 写盘前内容校验：LaTeX/BibTeX 走各自的结构规则，README 等文档只做
        # 传输完整性检查。不能把普通 Markdown 的花括号/$ 当成 LaTeX 语法，
        # 也不能把 BibTeX URL 中的百分号当作 LaTeX 注释。
        suffix = resolved.suffix.lower()
        if suffix == ".bib":
            latex_error = check_latex_text(content, bibtex=True)
        elif suffix in _LATEX_SOURCE_SUFFIXES - {".bib", ".bst"}:
            latex_error = check_latex_text(content)
        elif suffix == ".bst":
            latex_error = check_latex_text(content, bibtex=True)
        else:
            latex_error = check_latex_text_lite(content)
        if latex_error:
            self._logger.warning(
                f"write_file rejected corrupt LaTeX content for "
                f"{file_path}: {latex_error}"
            )
            msg = f"File {file_path} was rejected: {latex_error}."
            if suffix in _LATEX_SOURCE_SUFFIXES:
                msg += (
                    " In JSON tool arguments, each LaTeX backslash must be "
                    "escaped as two backslash characters (\\\\); resend the "
                    "complete file with intact LaTeX."
                )
            else:
                msg += " Resend the complete file with intact text content."
            return AgentFileToolFailure(msg)

        if resolved.suffix.lower() in {".cls", ".sty"}:
            provides_error = _check_provides_date(content)
            if provides_error:
                self._logger.warning(
                    f"write_file rejected invalid Provides date for "
                    f"{file_path}: {provides_error}"
                )
                msg = f"File {file_path} was rejected: {provides_error}"
                return AgentFileToolFailure(msg)
            if resolved.suffix.lower() == ".cls":
                option_order_error = _check_class_option_order(content)
                if option_order_error:
                    self._logger.warning(
                        f"write_file rejected class option order for "
                        f"{file_path}: {option_order_error}"
                    )
                    msg = f"File {file_path} was rejected: {option_order_error}"
                    return AgentFileToolFailure(msg)
            setter_accessor_error = _check_setter_accessors(content)
            if setter_accessor_error:
                self._logger.warning(
                    f"write_file rejected undefined setter accessor for "
                    f"{file_path}: {setter_accessor_error}"
                )
                msg = f"File {file_path} was rejected: {setter_accessor_error}"
                return AgentFileToolFailure(msg)

        if resolved.suffix.lower() in _LATEX_ENV_CHECK_SUFFIXES:
            cjk_error = _check_cjk_support(
                content,
                project_root=self._sandbox,
                current_path=resolved,
            )
            if cjk_error:
                self._logger.warning(
                    f"write_file rejected missing CJK support for "
                    f"{file_path}: {cjk_error}"
                )
                msg = f"File {file_path} was rejected: {cjk_error}"
                return AgentFileToolFailure(msg)
            body_style_error = _check_body_style_commands(content)
            if body_style_error:
                self._logger.warning(
                    f"write_file rejected style definition in body for "
                    f"{file_path}: {body_style_error}"
                )
                msg = f"File {file_path} was rejected: {body_style_error}"
                return AgentFileToolFailure(msg)

        # 环境配对错误在 TeX 编译时通常会级联成难以定位的错误。对源文件
        # 做一次轻量静态检查，把明确的结构错误在写盘阶段反馈给 Agent；
        # 不检查 README/BibTeX，因为它们不遵循 LaTeX 环境语法。
        if resolved.suffix.lower() in _LATEX_ENV_CHECK_SUFFIXES:
            environment_error = check_latex_environments(content)
            if environment_error:
                self._logger.warning(
                    f"write_file rejected mismatched LaTeX environments for "
                    f"{file_path}: {environment_error}"
                )
                msg = (
                    f"File {file_path} was rejected: {environment_error}. "
                    "Fix the begin/end environment pair and resend the complete "
                    "file."
                )
                return AgentFileToolFailure(msg)

            public_api_error = _check_public_body_commands(content)
            if public_api_error:
                self._logger.warning(
                    f"write_file rejected private body command for "
                    f"{file_path}: {public_api_error}"
                )
                msg = (
                    f"File {file_path} was rejected: {public_api_error} "
                    "Keep internal @ macros inside the .cls/.sty implementation "
                    "and resend the complete body file using its public API."
                )
                return AgentFileToolFailure(msg)

        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(content_bytes)
        self._written_paths.add(rel_path)
        # 源码已变化：允许（并要求）重新编译，清空成功快照防呆。
        self._last_success_source_hash = None
        self._logger.info(f"write_file: {file_path} ({len(content_bytes)} bytes)")

        return AgentFileWrite(path=file_path, size=len(content_bytes))

    # ── read_file ─────────────────────────────────────────────────────

    def _read_agent_file(
        self,
        file_path: str,
    ) -> AgentFileRead | AgentFileToolFailure:
        try:
            resolved = TemplateAgentService._resolve_sandbox_path(
                self._sandbox, file_path, for_read=True
            )
        except ValueError as e:
            return AgentFileToolFailure(
                str(e),
                summary=f"Path validation failed: {e}",
            )
        if not resolved.exists() or not resolved.is_file():
            msg = f"File not found: {file_path}"
            return AgentFileToolFailure(msg)
        return AgentFileRead(
            path=file_path,
            content=resolved.read_text(encoding="utf-8"),
        )

    def _prepare_agent_line_range(
        self,
        content: str,
        value: Any,
    ) -> tuple[int, int] | None:
        return normalize_line_range(value, coerce=True)

    # ── list_files ────────────────────────────────────────────────────

    def _list_agent_files(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        return self.list_files()

    # ── compile_latex ─────────────────────────────────────────────────

    async def _compile_latex(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        main_file = args.get("main_file", "")
        fcfg = self._cfg.features.template_gen
        try:
            resolved = TemplateAgentService._resolve_sandbox_path(
                self._sandbox, main_file
            )
        except ValueError as e:
            return str(e), {
                "tool": "compile_latex",
                "ok": False,
                "success": False,
                "log": str(e),
                "summary": f"Path validation failed: {e}",
            }
        if not resolved.exists():
            msg = f"Main file not found: {main_file}"
            return msg, {
                "tool": "compile_latex",
                "ok": False,
                "success": False,
                "log": msg,
                "summary": msg,
            }

        # latexmk 不能以 '-' 开头的路径作为参数
        if main_file.lstrip().startswith("-"):
            msg = "main_file cannot start with '-'"
            return msg, {
                "tool": "compile_latex",
                "ok": False,
                "success": False,
                "log": msg,
                "summary": msg,
            }

        source_hash = await asyncio.to_thread(self._source_hash)
        if self._last_success_source_hash == source_hash:
            msg = (
                "Compilation skipped: project source is unchanged since the "
                "previous SUCCESSFUL compile, so the project is already "
                "verified. Do not recompile without a meaningful source "
                "change. Either call finish (with main_file and summary) "
                "now, or edit a source file with write_file first."
            )
            return msg, {
                "tool": "compile_latex",
                "ok": True,
                "success": True,
                "already_verified": True,
                "log": msg,
                "summary": msg,
            }
        if self._last_failed_source_hash == source_hash:
            msg = (
                "Compilation skipped: project source is unchanged since the "
                "previous failed compile. Read the source excerpt and log "
                "context from that failure, make a meaningful source edit, "
                "then call compile_latex again."
            )
            return msg, {
                "tool": "compile_latex",
                "ok": False,
                "success": False,
                "compile_skipped": True,
                "log": msg,
                "summary": msg,
            }

        timeout = fcfg.agent_compile_timeout
        self._logger.info(f"agent compile | main={main_file} | cwd={self._sandbox}")

        try:
            tex_result = await run_tex_compile(
                main_file,
                self._sandbox,
                timeout,
                # 每次源码发生变化都从干净的辅助文件状态开始。模板 Agent
                # 可能在修复过程中切换 biblatex/BibTeX、class 或 engine；
                # latexmk 默认复用旧 .aux/.bcf 会把上一轮格式的命令带入本轮，
                # 产生与当前源码无关的假错误。
                extra_args=["-halt-on-error", "-gg"],
            )
        except FileNotFoundError:
            msg = "latexmk is not installed or is not on PATH"
            return msg, {
                "tool": "compile_latex",
                "ok": False,
                "success": False,
                "log": msg,
                "summary": msg,
            }

        if tex_result.timed_out:
            msg = f"Compilation timed out ({timeout}s)"
            return msg, {
                "tool": "compile_latex",
                "ok": False,
                "success": False,
                "log": msg,
                "summary": msg,
            }

        full_log = tex_result.raw_output
        success = tex_result.success

        trimmed = _trim_log(full_log, success)
        self._logger.info(f"agent compile done | success={success}")

        # latexmk/texfot 的摘要可能只保留外层 main.tex 的错误行，而真正的
        # `!` 错误和输入栈位于完整的 .log 中。无论成功还是失败都读取主日志：
        # 成功时用于缺字/未解析引用质量门禁，失败时用于给 Agent 正确的
        # .cls/.sty/.tex 文件上下文，避免它根据错误的外层行号盲修。
        log_text = await asyncio.to_thread(_read_log_text, resolved.with_suffix(".log"))
        diagnostic_log = f"{log_text}\n{full_log}" if log_text else full_log
        # latexmk 的标准输出可能包含同一次构建中早期 TeX pass 的警告，
        # 而主 .log 在 latexmk 完成后代表最后一次 pass。质量门禁（缺字、
        # 未解析引用、版面警告）只能检查最后一次 pass；否则已经由 BibTeX
        # / 后续 pass 修复的首轮 warning 会被错误地再次升级成失败。
        quality_log = log_text or full_log
        errors = _extract_errors(
            diagnostic_log,
            project_root=self._sandbox,
            fallback_file=Path(main_file).as_posix(),
        )
        quality_errors = [
            error
            for error in _extract_errors(
                quality_log,
                project_root=self._sandbox,
                fallback_file=Path(main_file).as_posix(),
            )
            if str(error.get("type", "")).startswith("Missing character")
        ]
        quality_errors.extend(
            _extract_unresolved_reference_errors(
                quality_log,
                fallback_file=Path(main_file).as_posix(),
            )
        )
        quality_warnings = _extract_quality_warnings(quality_log)
        build_success = success and not quality_errors

        if build_success:
            # 保留成功状态，但把可修复的版面警告明确交给模型；它们不是
            # 编译失败，不应让 Agent 为了消除每个 overfull box 无限重写。
            # 成功时不要把 latexmk stdout 中早期 pass 的原始 warning 再次
            # 回传给模型；质量 warning 已从最后一次主 .log 重新提取，成功
            # 摘要只需保留这一份权威结果。
            llm_text = _format_quality_feedback(
                quality_warnings, "Compilation succeeded."
            )
            summary = "Compilation succeeded"
        else:
            if success:
                # 质量门禁失败时仍使用统一的结构化错误回路；此时 PDF 可能
                # 已生成，但不能让 finish 把缺字/断引用伪装成合格产物。
                errors = quality_errors
            log_file = resolved.with_suffix(".log")
            log_note = (
                f"[note: complete {log_file.name} was included when extracting "
                "the structured errors and source context]"
                if log_text
                else ""
            )
            # latexmk 的 stdout/stderr 可能不含 `!` 错误行（错误只写进了
            # <main>.log）。此时直接从沙箱读取日志文件兜底提取，避免
            # Agent 因"看不到错误"而盲目重试。
            if not errors:
                log_text = await asyncio.to_thread(_read_log_text, log_file)
                if log_text:
                    log_errors = _extract_errors(
                        log_text,
                        project_root=self._sandbox,
                        fallback_file=Path(main_file).as_posix(),
                    )
                    if log_errors:
                        errors = log_errors
                        log_note = (
                            f"[note: no error lines in latexmk output; errors "
                            f"below were extracted from {log_file.name}]"
                        )
                    else:
                        log_note = (
                            f"[note: no '!' error lines in latexmk output or "
                            f"{log_file.name}; a tail of {log_file.name} is "
                            f"included below]"
                        )
                        log_tail = (
                            log_text[-_LOG_TRUNCATE:]
                            if len(log_text) > _LOG_TRUNCATE
                            else log_text
                        )
                        trimmed = (
                            trimmed + f"\n\n--- {log_file.name} (tail) ---\n" + log_tail
                        )
                    # 参考文献失败时真实错误在 .blg 里（如 .bst 不存在、
                    # bib 条目缺字段），同样兜底追加
                    blg_file = resolved.with_suffix(".blg")
                    blg_text = await asyncio.to_thread(_read_log_text, blg_file)
                    if blg_text and (
                        "bibtex" in log_text.lower()
                        or "bbl" in log_text.lower()
                        or "blg" in log_text.lower()
                    ):
                        log_note = (
                            log_note + " [bibtex note: if the .blg tail shows "
                            "'couldn't open style file', the "
                            "\\bibliographystyle name does not exist — use "
                            "an installed style (e.g. gbt7714-numerical / "
                            "gbt7714-2005-numerical) or a standard one; if "
                            "it shows 'missing field for entry', fix that "
                            "entry's type or add the missing field (e.g. "
                            "@inproceedings needs booktitle).]"
                        )
                        blg_tail = (
                            blg_text[-_LOG_TRUNCATE:]
                            if len(blg_text) > _LOG_TRUNCATE
                            else blg_text
                        )
                        trimmed = (
                            trimmed + f"\n\n--- {blg_file.name} (tail) ---\n" + blg_tail
                        )
            errors = _attach_source_context(
                errors,
                self._sandbox,
                fallback_file=Path(main_file).as_posix(),
            )
            analysis = _format_error_analysis(errors)
            if log_note:
                analysis = log_note + "\n\n" + analysis
            llm_text = analysis + "\n\n--- Raw log (trimmed) ---\n" + trimmed
            error_count = len(errors)
            summary = (
                f"Compilation failed or quality gate rejected the build "
                f"({error_count} error(s)); analyze the error summary, "
                f"use read_file to inspect the relevant files (main.log is readable), "
                f"then use write_file to fix and compile again"
            )

        event = {
            "tool": "compile_latex",
            "ok": True,
            "success": build_success,
            "log": trimmed,
            "summary": summary,
        }
        if quality_warnings:
            event["quality_warnings"] = quality_warnings
        if not build_success:
            self._last_failed_source_hash = source_hash
            self._last_success_source_hash = None
            event["errors"] = errors
        else:
            self._last_failed_source_hash = None
            self._last_success_source_hash = source_hash
        return llm_text, event

    # ── finish ────────────────────────────────────────────────────────

    def _finish(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        summary = args.get("summary", "")
        main_file = args.get("main_file", "")
        files = self.list_files()
        text = (
            f"Project finished. Summary: {summary}\n"
            f"Main file: {main_file}\n"
            f"Files ({len(files)}): " + ", ".join(f["path"] for f in files)
        )
        return text, {
            "tool": "finish",
            "ok": True,
            "summary": "Finished",
            "main_file": main_file,
        }

    # ── 文件列表（公共） ──────────────────────────────────────────────

    def list_files(self) -> list[dict[str, Any]]:
        """列出沙箱内所有文件（相对路径 + 字节数），按路径排序。"""
        result: list[dict[str, Any]] = []
        root = self._sandbox.resolve()
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            result.append({"path": rel, "size": p.stat().st_size})
        return result

    def _source_hash(self) -> str:
        """计算当前项目源码快照，用于阻止未改源码的重复编译。"""
        import hashlib

        digest = hashlib.sha256()
        root = self._sandbox.resolve()
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _LATEX_SOURCE_SUFFIXES:
                continue
            try:
                digest.update(path.relative_to(root).as_posix().encode("utf-8"))
                digest.update(b"\0")
                digest.update(path.read_bytes())
            except OSError:
                continue
        return digest.hexdigest()


# ── 辅助函数 ───────────────────────────────────────────────────────────


def _read_log_text(log_path: Path, max_chars: int = 262144) -> str:
    """读取编译日志文件文本（供 _compile_latex 兜底解析），失败返回空串。"""
    try:
        if not log_path.exists() or not log_path.is_file():
            return ""
        text = log_path.read_text(encoding="utf-8", errors="replace")
        return text if len(text) <= max_chars else text[:max_chars]
    except OSError:
        return ""


def _trim_log(log: str, success: bool) -> str:
    """截断编译日志，失败时优先保留错误行附近上下文。

    仅用于前端展示和 LLM 回传的原始日志片段；结构化错误信息由
    _extract_errors / _format_error_analysis 额外提供。
    """
    if success:
        # 成功时只取尾部简短信息
        tail = log[-1500:] if len(log) > 1500 else log
        return "Compilation succeeded.\n\n" + tail
    lines = log.splitlines()
    # 找到第一个明显错误标记
    err_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("!") or "Fatal error" in line or "Error:" in line:
            err_idx = i
            break
    if err_idx >= 0:
        start = max(0, err_idx - 10)
        end = min(len(lines), err_idx + 40)
        snippet = "\n".join(lines[start:end])
    else:
        snippet = log[-_LOG_TRUNCATE:] if len(log) > _LOG_TRUNCATE else log
    if len(snippet) > _LOG_TRUNCATE:
        snippet = snippet[:_LOG_TRUNCATE] + "\n...[log truncated]"
    return "Compilation FAILED.\n\n" + snippet


def _nudge_message() -> str:
    """规划瘫痪引导文案（仅注入 LLM 历史与 trace，不推送到前端）。"""
    return (
        "You replied without calling any tool, which counts as a stalled round. "
        "Immediately call the next tool (write_file / read_file / list_files / "
        "compile_latex / finish) — never reply with prose only. Keep plan text to "
        "at most 2 sentences."
    )


# 匹配 latexmk/xelatex 日志中的文件入栈标记，形如 `(./main.tex` 或
# `(chapters/intro.tex`，也覆盖 `(./thesis.cls` / `(./theme.sty`。
# 用于追踪错误发生在哪个源文件中（错误常出现在 .cls/.sty 的宏定义里）。
_FILE_CTX_RE = re.compile(r"\(\s*((?:\.{1,2}/)?[\w./\-]+\.(?:tex|cls|sty))\b")
# 匹配行号提示，形如 `l.123 ...`（不带转义因为 LaTeX 错误日志是纯文本）
_LINE_INFO_RE = re.compile(r"l\.(\d+)\s?(.*)")


def _normalize_log_file(
    raw_file: str, project_root: Path | None
) -> tuple[str | None, str | None]:
    """把日志文件上下文限制在项目沙箱内，避免误导 Agent 修改 TeX 安装文件。"""
    raw = raw_file.strip()
    if project_root is None:
        return raw[2:] if raw.startswith("./") else raw, None

    root = project_root.resolve()
    raw_path = Path(raw)
    if raw_path.is_absolute():
        try:
            return raw_path.resolve().relative_to(root).as_posix(), None
        except ValueError:
            return None, raw

    relative = raw[2:] if raw.startswith("./") else raw
    candidate = (root / relative).resolve()
    if candidate.is_relative_to(root) and candidate.is_file():
        return candidate.relative_to(root).as_posix(), None
    return None, raw


def _extract_errors(
    log: str,
    *,
    project_root: Path | None = None,
    fallback_file: str | None = None,
) -> list[dict[str, Any]]:
    """从 latexmk/xelatex 日志中提取结构化错误信息。

    识别以 `!` 开头的错误声明行，并：
      - 回溯最近的 `(xxx.tex` 上下文记录错误所在文件
      - 向后扫 15 行寻找 `l.NNN ...` 行号提示

    传入 project_root 时，TeX 发行版内部文件会被标记为
    ``external_file``，不会伪装成项目文件。若错误只显示外部展开栈，使用
    fallback_file 作为需要 Agent 检查的项目入口。

    LaTeX 错误会级联（第一个错误往往引来一系列后续错误），所以只
    返回前若干个错误就足以让 LLM 聚焦根因。

    另外识别"缺字"类诊断（如中文内容未配 CJK 字体时 xelatex 报
    `Missing character: There is no ...`）：这类问题不产生 `!` 行，
    但会导致 latexmk 退出码非 0 且 PDF 缺字。缺字诊断会逐字符刷屏，
    同一段文本可能产生成百上千条，因此只保留第一条并合并计数。
    """
    errors: list[dict[str, Any]] = []
    lines = log.splitlines()
    last_file: str | None = None
    last_external_file: str | None = None
    # 只保留第一条缺字诊断（记录首条所在文件/字体），后续只累计数量
    missing_char: dict[str, Any] | None = None
    missing_char_count = 0

    for i, line in enumerate(lines):
        # 更新当前文件上下文（粗略追踪，不处理栈深度，仅用最近出现的）
        for m in _FILE_CTX_RE.finditer(line):
            normalized, external = _normalize_log_file(m.group(1), project_root)
            if normalized:
                last_file = normalized
                last_external_file = None
            elif external:
                last_external_file = external

        # 缺字诊断：`Missing character: There is no X (U+XXXX) in font Y`
        if "Missing character" in line or "missing character" in line:
            missing_char_count += 1
            if missing_char is None:
                # 尝试提取字体名（in font <字体>，可能含空格，字体参数以 / 或 ; 分隔）
                font = None
                m_font = re.search(r"in font ([^/;!]+)", line)
                if m_font:
                    font = m_font.group(1).strip()
                missing_char = {
                    "type": "Missing character (CJK font not configured?)",
                    "file": last_file,
                    "line": None,
                    "detail": line.strip()[:200],
                }
                if last_external_file:
                    missing_char["external_file"] = last_external_file
                if font:
                    missing_char["font"] = font
                # 按日志顺序插入首条缺字诊断，后续刷屏行不再产生新条目
                errors.append(missing_char)
            continue

        if not line.startswith("!"):
            continue

        err: dict[str, Any] = {
            "type": line[1:].strip(),
            "file": last_file,
            "line": None,
            "detail": "",
        }
        if last_external_file:
            err["external_file"] = last_external_file
        # 向后扫 15 行寻找 l.NNN
        for j in range(i + 1, min(len(lines), i + 15)):
            m_line = _LINE_INFO_RE.match(lines[j])
            if m_line:
                err["line"] = int(m_line.group(1))
                err["detail"] = m_line.group(2).strip()
                break
            if lines[j].startswith("!"):
                # 下一个错误已经开始，停止继续找行号
                break
        errors.append(err)

    # 合并计数：多条缺字诊断合并为一条，避免挤占错误列表上限
    if missing_char is not None and missing_char_count > 1:
        missing_char["detail"] = (
            f"{missing_char['detail']} "
            f"(+{missing_char_count - 1} more missing-character lines)"
        )

    # 限制最多 8 个错误，避免极端情况日志回传过长
    limited = errors[:8]
    if project_root and fallback_file:
        for error in limited:
            if error.get("external_file") or error.get("file") is None:
                error["file"] = fallback_file
    return limited


def _extract_unresolved_reference_errors(
    log: str, *, fallback_file: str
) -> list[dict[str, Any]]:
    """把成功构建但仍未解析的交叉引用/文献引用升级为质量错误。

    latexmk 在某些情况下会返回 0 并生成 PDF，即使日志仍包含 undefined
    reference/citation。对模板交付而言，这与编译失败同样不可接受；这里仅
    提取明确的 unresolved 诊断，不把普通 rerun 提示误判为错误。
    """
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    lines = log.splitlines()
    for line in lines:
        stripped = line.strip()
        lowered = stripped.lower()
        is_unresolved = (
            "undefined references" in lowered
            or "undefined citations" in lowered
            or ("reference `" in lowered and "undefined" in lowered)
            or ("citation `" in lowered and "undefined" in lowered)
        )
        if not is_unresolved:
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        errors.append(
            {
                "type": "Unresolved cross-reference or citation.",
                "file": fallback_file,
                "line": None,
                "detail": stripped[:240],
            }
        )
        if len(errors) >= 8:
            break
    return errors


def _extract_quality_warnings(log: str, *, limit: int = 12) -> list[str]:
    """提取不阻断 PDF 生成、但值得 Agent 在交付前处理的排版警告。"""
    warnings: list[str] = []
    seen: set[str] = set()
    patterns = (
        "overfull \\hbox",
        "underfull \\hbox",
        "font warning",
        "label(s) may have changed",
        "rerun to get cross-references",
    )
    for line in log.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if not stripped or not any(pattern in lowered for pattern in patterns):
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        warnings.append(stripped[:240])
        if len(warnings) >= limit:
            break
    return warnings


def _format_quality_feedback(warnings: list[str], trimmed_log: str) -> str:
    """把非致命质量警告放在成功编译结果的最前面。"""
    if not warnings:
        return trimmed_log
    lines = [
        "Compilation succeeded, but review these non-fatal quality warnings "
        "before finish:",
    ]
    lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    lines.append(trimmed_log)
    return "\n".join(lines)


def _attach_source_context(
    errors: list[dict[str, Any]],
    project_root: Path,
    *,
    fallback_file: str,
    radius: int = 3,
) -> list[dict[str, Any]]:
    """为结构化错误附加有限行号上下文，减少 LLM 盲读整份大文件。"""
    root = project_root.resolve()
    for error in errors:
        file_name = error.get("file") or fallback_file
        line_number = error.get("line")
        if error.get("external_file"):
            error["scope_note"] = (
                "The log stack entered an external TeX distribution file; "
                "do not edit that file. Inspect the project source instead."
            )
            file_name = fallback_file
        if not isinstance(line_number, int) or line_number < 1:
            continue
        try:
            source_path = (root / str(file_name)).resolve()
            if not source_path.is_relative_to(root) or not source_path.is_file():
                continue
            source_lines = source_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError:
            continue
        start = max(1, line_number - radius)
        end = min(len(source_lines), line_number + radius)
        error["source_context"] = "\n".join(
            f"{index}: {source_lines[index - 1]}" for index in range(start, end + 1)
        )
    return errors


def _error_hints(errors: list[dict[str, Any]]) -> list[str]:
    """根据已知的通用 LaTeX 失败签名生成针对性修复提示（与具体模板无关）。"""
    hints: list[str] = []
    for e in errors:
        combined = f"{e.get('type', '')} {e.get('detail', '')}".lower()
        if "undefined control sequence" in combined:
            hint = (
                "Public API check: determine whether the undefined command is "
                "project-defined or package-provided. Read the loaded .cls/.sty "
                "and all body callers, then define one stable public command with "
                "the exact argument count or replace the caller; do not create a "
                "second accessor with a similar name. If the undefined command "
                "appears while expanding a public setter, check whether that "
                "setter performs `\\def`/`\\gdef` of a name containing `@` — "
                "in a body file `@` is not a letter, so store the value in a "
                "public accessor directly or wrap the assignment in "
                "\\makeatletter ... \\makeatother. If it is package-provided, "
                "load that package before the first use."
            )
            if hint not in hints:
                hints.append(hint)
        if "illegal unit of measure" in combined or "missing number" in combined:
            hint = (
                "Dimension parsing issue: inspect custom macros and key-value "
                "options in the project source for a width, height, skip, or "
                "ratio that lacks an explicit unit or expands to text. If the "
                "reported line is `\\begin{document}` or a third-party package, "
                "the root cause is usually an earlier project definition; do "
                "not remove packages merely because the stack trace entered one. "
                "Simplify the custom dimension macro and use a literal dimension "
                "with a unit first."
            )
            if hint not in hints:
                hints.append(hint)
        if "missing = inserted for \\ifnum" in combined or (
            "ifnum" in combined and "missing =" in combined
        ):
            hint = (
                "Template/class arithmetic issue: inspect the project .sty/.cls "
                "loaded at the reported `\\usetheme` or `\\documentclass` line, "
                "not the theme name itself. A TeX `\\ifnum` must compare two "
                "integer counts with `<`, `=`, or `>`; it cannot compare a "
                "decimal or an expanded dimension. Also check that every "
                "`\\ProvidesClass`/`\\ProvidesPackage` optional argument starts "
                "with a YYYY/MM/DD date (e.g. [2024/01/01 Template]); an "
                "incomplete date such as `2024/01` makes the next word be "
                "parsed as an integer. And `\\setbeamertemplate{blocks}` accepts "
                "a predefined template name such as `[default]` or `[rounded]`, "
                "not a key-value option like `[rounded=false]`. Remove unsafe "
                "custom template arithmetic and use a simple literal `\\rule` "
                "progress bar until the base theme compiles."
            )
            if hint not in hints:
                hints.append(hint)
        if "tex capacity exceeded" in combined or "input stack size" in combined:
            hint = (
                "TeX capacity / recursion issue: the document is likely "
                "redefining a beamer template or environment in a recursive "
                "way (for example setting the same `\\setbeamertemplate` "
                "twice, or an item template that calls itself). Inspect every "
                "custom template definition in the .sty files, remove "
                "duplicate/recursive `\\setbeamertemplate` calls, and keep "
                "one built-in template name per element until the base "
                "compiles."
            )
            if hint not in hints:
                hints.append(hint)
        if "iffontexiststf" in combined:
            hint = (
                "Font feature issue: `\\IfFontExistsTF` is provided by "
                "fontspec, so it must not be used before fontspec is loaded. "
                "Use it only for an explicitly requested Latin font; for CJK "
                "text, load ctex/xeCJK and rely on its default fontset instead."
            )
            if hint not in hints:
                hints.append(hint)
        if "spacefactor" in combined:
            hint = (
                "Metadata trap: this error is caused by a `\\@...` internal "
                "macro (or a bare `\\@`) typeset in a body .tex file, where "
                "`@` is not a letter. Define PUBLIC metadata commands in the "
                ".cls/.sty (e.g. `\\thesistitlevalue` accessors) or a "
                "`\\maketitlepage` command, and use only those public "
                "commands in body files."
            )
            if hint not in hints:
                hints.append(hint)
        if (
            "cannot be found" in combined
            or "fontspec" in combined
            or ("font" in combined and "missing" in combined)
        ):
            hint = (
                "Font issue: do NOT manually set CJK fonts "
                "(\\setCJKmainfont / \\setCJKsansfont / \\setCJKfamilyfont) "
                "or probe fonts with \\IfFontExistsTF on CJK family names "
                "(unreliable for CJK). Delete the explicit CJK font commands "
                "and let ctex's automatic platform fontset pick a working "
                "stack; for Latin fonts fall back to Latin Modern or the "
                "class default. Document any substitution in the README."
            )
            if hint not in hints:
                hints.append(hint)
        if "already defined" in combined:
            hint = (
                "Command collision: the command already exists in the loaded "
                "class/package. Use \\renewcommand when the base definition "
                "must change, or define a NEW name; never \\newcommand over "
                "an existing command. For theorem/abstract/list infrastructure, "
                "prefer the inherited environment or a project-specific name "
                "instead of replacing the class interface."
            )
            if hint not in hints:
                hints.append(hint)
        if "unresolved cross-reference" in combined or (
            "undefined" in combined
            and ("reference" in combined or "citation" in combined)
        ):
            hint = (
                "Reference integrity issue: every `\\ref`/`\\eqref`/citation "
                "must point to a label or BibTeX key that exists in the project. "
                "Read the cited source and references.bib, then either correct "
                "the key or remove the unused reference and recompile until the "
                "undefined-reference warning is gone."
            )
            if hint not in hints:
                hints.append(hint)
        if "macro parameter character" in combined:
            hint = (
                "A literal `#` appears in text or in a command argument. In "
                "LaTeX text, write `\\#` for the hash symbol; for HTML/RGB "
                "colors, write `\\definecolor{...}{HTML}{1F4E79}` WITHOUT "
                "the `#` prefix. Never place a bare `#` outside a macro "
                "definition."
            )
            if hint not in hints:
                hints.append(hint)
        if "capacity exceeded" in combined or "input stack size" in combined:
            hint = (
                "Infinite recursion or runaway expansion: a macro is calling "
                "itself (e.g. a command redefines its own name, or "
                "\\labelenumi/\\theenumi is redefined circularly). Remove or "
                "simplify the recursive redefinition; if a custom theme "
                "redefines list counters, delete those lines and let the "
                "class/beamer defaults handle them."
            )
            if hint not in hints:
                hints.append(hint)
        if "a node must have" in combined:
            hint = (
                "Every tikz \\node needs a (possibly empty) label text: "
                "`\\node[options] (name) {text};` — the braces are "
                "mandatory, e.g. `\\node[fill=blue] (box) {};`."
            )
            if hint not in hints:
                hints.append(hint)
        if "no counter" in combined:
            hint = (
                "The referenced counter is not defined (or not yet defined "
                "at that point). Use counters provided by the class "
                "(e.g. beamer defines 'frame'/'framenumber'; LaTeX defines "
                "'figure'/'table'/'equation'), or define your own with "
                "\\newcounter before using it. Do not \\newcounter over "
                "names the class already provides."
            )
            if hint not in hints:
                hints.append(hint)
        if "not found" in combined and "file" in combined:
            hint = (
                "A file/package/theme is missing. If it is a file YOU "
                "intended to provide (e.g. a custom .sty or "
                "beamerthemeX.sty), create it with write_file; if it is a "
                "third-party package, use a standard equivalent already "
                "available in TeX distributions or remove the "
                "\\usepackage/\\usetheme line."
            )
            if hint not in hints:
                hints.append(hint)
        if "missing \\begin{document}" in combined:
            hint = (
                "In a .cls/.sty file, `@` is already a letter, so a stray "
                "`\\makeatother` turns every later `\\@...` command name into "
                "`\\@` + letters, and the letters get typeset in the "
                "preamble — which raises 'Missing \\begin{document}'. Remove "
                "`\\makeatother` from class/style files (`\\makeatletter` is "
                "unnecessary there). Also verify no body text appears in "
                "main.tex before \\begin{document}."
            )
            if hint not in hints:
                hints.append(hint)
        if "missing \\endcsname" in combined:
            hint = (
                "Missing \\endcsname: a command used inside a key-value or "
                "\\csname context is undefined or malformed. Chinese size "
                "names like `\\wuhao` are NOT commands — use `\\zihao{5}` "
                "(or `font=small` in \\captionsetup). Check every control "
                "sequence in the reported line and the key it belongs to."
            )
            if hint not in hints:
                hints.append(hint)
        if "extra alignment tab" in combined:
            hint = (
                "A tabular row has more `&` separators than the column spec "
                "allows, OR a `p{}`-cell declaration like \\centering/"
                "\\raggedright swallowed the row break (\\centering "
                "redefines `\\\\` inside the cell). Fix: end rows with "
                "\\tabularnewline when a cell uses \\centering, or use "
                "`>{\\centering\\arraybackslash}p{...}` column specs; also "
                "count `&` against the column specification."
            )
            if hint not in hints:
                hints.append(hint)
        if "environment" in combined and "undefined" in combined:
            hint = (
                "Environment undefined: the environment's defining package "
                "is not loaded (e.g. `algorithm` needs \\usepackage{algorithm}"
                "+\\usepackage{algorithmic} or algorithm2e; `tikzpicture` "
                "needs \\usepackage{tikz}), or the name is misspelled. Add "
                "the package or fix the name."
            )
            if hint not in hints:
                hints.append(hint)
        if "undefined color" in combined or (
            "xcolor" in combined and "undefined" in combined
        ):
            hint = (
                "Color mismatch: a color name used in the document or theme "
                "is not defined. Define every custom color with "
                "\\definecolor{<name>}{<model>}{<spec>} in the color theme "
                "file BEFORE its first use, or use a color name the loaded "
                "class/theme already provides. Check spelling and case: "
                "color names are case-sensitive, and a theme does not "
                "magically provide a color unless you define it."
            )
            if hint not in hints:
                hints.append(hint)
        if "bibliography not compatible" in combined or (
            "natbib" in combined and "bbl" in combined
        ):
            hint = (
                "Bibliography style mismatch: natbib's citation mode and the "
                ".bbl format disagree (author-year vs numeric). latexmk -gg "
                "already regenerates .bbl from the current .bst, so do NOT "
                "try to delete or hand-edit .bbl. Align the two sides in "
                "source: either load natbib in numeric mode (e.g. [numbers] "
                "or [sort&compress]) with a numeric .bst (plain / unsrt / "
                "gbt7714-numerical), or switch to author-year mode with an "
                "author-year .bst (plainnat / gbt7714-author-year). Pick ONE "
                "mode and make \\bibliographystyle, natbib options and "
                "citation commands consistent."
            )
            if hint not in hints:
                hints.append(hint)
    return hints


def _format_error_analysis(errors: list[dict[str, Any]]) -> str:
    """把结构化错误列表格式化为给 LLM 看的聚焦错误摘要。

    强调"修第一个错误"——LaTeX 错误级联是常态，后续错误通常是
    第一个的连锁反应。
    """
    if not errors:
        return (
            "Compilation FAILED. No '! ' error lines were detected in the "
            "build output or the generated .log file. Read the full log "
            "with read_file (e.g. 'read_file main.log') and inspect it for "
            "missing files, font issues, or other diagnostics, then fix and "
            "recompile."
        )
    parts = [
        f"Compilation FAILED. Detected {len(errors)} LaTeX error(s).",
        "Fix the FIRST error and recompile — later errors are usually "
        "consequences of the first.\n",
    ]
    for idx, e in enumerate(errors, 1):
        parts.append(f"Error {idx}:")
        parts.append(f"  type: {e['type']}")
        if e["file"]:
            parts.append(f"  file: {e['file']}")
        if e["line"] is not None:
            parts.append(f"  line: {e['line']}")
        if e["detail"]:
            parts.append(f"  detail: {e['detail']}")
        if e.get("scope_note"):
            parts.append(f"  scope: {e['scope_note']}")
        if e.get("source_context"):
            parts.append("  source excerpt:")
            parts.extend(f"    {line}" for line in e["source_context"].splitlines())
        if e.get("font"):
            parts.append(f"  font: {e['font']}")
        if e["type"].startswith("Missing character"):
            parts.append(
                "  hint: Chinese/CJK text is typeset in a font that lacks "
                "those glyphs — load xeCJK/ctex and use its default CJK "
                "fontset for CJK text; do not manually set CJK fonts."
            )
        parts.append("")
    hints = _error_hints(errors)
    if hints:
        parts.append("HINTS (apply before the next compile):")
        parts.extend(f"- {h}" for h in hints)
        parts.append("")
    return "\n".join(parts)


def _summarize_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """生成给前端展示的 args 摘要（write_file 的 content 截断）。"""
    if tool_name == "write_file":
        fp = args.get("file_path", "")
        content = args.get("content", "")
        return {
            "file_path": fp,
            "content_length": len(content),
            "preview": content[:300],
        }
    if tool_name == "compile_latex":
        return {"main_file": args.get("main_file", "")}
    if tool_name == "read_file":
        return {
            key: value
            for key, value in args.items()
            if key in ("file_path", "line_range")
        }
    if tool_name == "finish":
        return {
            "summary": args.get("summary", ""),
            "main_file": args.get("main_file", ""),
        }
    return args


# ── 单例 ───────────────────────────────────────────────────────────────

_service: "TemplateAgentService | None" = None


def get_template_agent_service() -> TemplateAgentService:
    global _service
    if _service is None:
        _service = TemplateAgentService()
    return _service


def load_sandbox(output_dir: str, session_id: str) -> Path:
    """根据 session_id 解析沙箱目录（供 router 的文件读取端点使用）。

    安全约束：session_id 必须是纯十六进制/字母数字（生成时为 uuid hex），
    解析后必须严格位于 `output_dir/` 子目录内，禁止 `..` 逃逸到
    其他模板或兄弟会话目录。
    """
    if not session_id or not session_id.strip():
        raise ValueError("session_id cannot be empty")
    if "\x00" in session_id:
        raise ValueError("session_id contains invalid characters")
    # 仅允许字母数字与下划线/短横线，杜绝 '..' / '/' / '\\' 注入
    if not all(c.isalnum() or c in "_-" for c in session_id):
        raise ValueError("session_id contains invalid characters")

    base = Path(output_dir)
    if not base.is_absolute():
        base = Path.cwd() / base
    base_root = base.resolve()
    sandbox = (base_root / session_id).resolve()
    if not sandbox.is_relative_to(base_root):
        raise ValueError("session_id resolves outside the sandbox root")
    return sandbox


def safe_read_sandbox_file(
    sandbox: Path, rel_path: str
) -> tuple[str | None, str | None]:
    """安全读取沙箱内文件，返回 (content, error)。"""
    try:
        resolved = TemplateAgentService._resolve_sandbox_path(
            sandbox, rel_path, for_read=True
        )
    except ValueError as e:
        return None, str(e)
    if not resolved.exists() or not resolved.is_file():
        return None, f"File not found: {rel_path}"
    return resolved.read_text(encoding="utf-8"), None
