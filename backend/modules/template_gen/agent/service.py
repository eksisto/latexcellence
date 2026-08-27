"""模板生成自主 Agent 主循环与工具 schema。"""

import json
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from backend.core.config import (
    FontWhitelistEntry,
    default_font_whitelist,
    get_config,
)
from backend.core.logger import get_logger
from backend.core.llm_client import get_llm_client
from backend.core.prompts import get_prompt
from backend.modules.template_gen.trace import append_trace, now_iso
from backend.utils.agent_file_tools import build_agent_file_tools_schema

from .executor import _ToolExecutor
from .loop import _compact_messages, _content_hash, _nudge_message, _summarize_args
from .sandbox import resolve_sandbox_path


def _render_font_whitelist(entries: list[FontWhitelistEntry]) -> str:
    """把配置的字体白名单渲染为提示词清单（每行 name | aliases | note）。"""
    lines: list[str] = []
    for entry in entries:
        parts = [entry.name]
        if entry.aliases:
            parts.append("aliases: " + ", ".join(entry.aliases))
        if entry.note:
            parts.append(entry.note)
        lines.append("- " + " | ".join(parts))
    return "\n".join(lines)


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
        if llm is None and not cfg.llm.api_key:
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
        # 阶段 3：长提示词拆分。核心 system 只保留强制工作流/失败预算/
        # 禁止臆造；规划细节（架构原则、构建安全、类别 playbook）在开始时
        # 注入一次，编译恢复协议与视觉质量门禁分别在首次编译与首次成功编译
        # 后注入，避免长清单从头到尾稀释指令。
        # 字体白名单来自配置（空列表回退默认值），只允许 Agent 使用名单内字体。
        font_whitelist = (
            cfg.features.template_gen.font_whitelist or default_font_whitelist()
        )
        messages.append(
            {
                "role": "system",
                "content": get_prompt(
                    "template_gen",
                    "planning_system",
                    font_whitelist=_render_font_whitelist(font_whitelist),
                ),
            }
        )
        compile_enabled = cfg.features.template_gen.agent_compile_enabled
        if not compile_enabled:
            # 工具链不可用时没有编译阶段，质量门禁直接前置，保证 finish 前
            # 仍会执行验证步骤。
            messages.append(
                {
                    "role": "system",
                    "content": get_prompt("template_gen", "validation_phase_system"),
                }
            )

        # 3. Agent 循环
        max_rounds = cfg.features.template_gen.agent_max_rounds
        final_summary = ""
        # 跟踪编译状态：不允许 Agent 在最后一次编译仍失败时静默 finish
        compile_state = {"called": False, "success": None}
        # 阶段块注入标记：每个阶段指令只在首次触达对应事件时注入一次。
        compile_phase_injected = False
        validation_phase_injected = not compile_enabled
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
            validation_injected_this_round = False
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

                if tool_name == "finish" and validation_injected_this_round:
                    result_text = (
                        "Finish deferred: review the newly injected validation "
                        "guidance, then call finish in the next round."
                    )
                    result_event = {
                        "tool": "finish",
                        "ok": False,
                        "summary": result_text,
                    }
                else:
                    try:
                        result_text, result_event = await executor.execute(
                            tool_name,
                            tool_args,
                        )
                    except Exception as e:
                        self._logger.warning(
                            f"Tool execution exception {tool_name}: {e}"
                        )
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
                    # 首次编译后注入错误恢复协议，指导后续修复轮次。
                    if not compile_phase_injected:
                        compile_phase_injected = True
                        messages.append(
                            {
                                "role": "system",
                                "content": get_prompt(
                                    "template_gen", "compile_phase_system"
                                ),
                            }
                        )
                    # 首次成功编译后注入质量门禁，供 finish 前的验证使用。
                    if (
                        result_event.get("success") is True
                        and not validation_phase_injected
                    ):
                        validation_phase_injected = True
                        validation_injected_this_round = True
                        messages.append(
                            {
                                "role": "system",
                                "content": get_prompt(
                                    "template_gen", "validation_phase_system"
                                ),
                            }
                        )
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

                # finish 工具立即终止循环；但若执行器拒绝（例如源码在最后一次
                # 编译后又变更、未经验证），不能结束，必须让模型先编译。
                if tool_name == "finish":
                    if result_event.get("ok") is False:
                        continue
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

        # 4. 汇总文件（sandbox 绝对路径不向用户暴露，前端仅消费 session_id/files）
        files = executor.list_files()
        yield {
            "type": "done",
            "session_id": session_id,
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
        return resolve_sandbox_path(sandbox, file_path, for_read=for_read)

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

# ── 单例 ───────────────────────────────────────────────────────────────

_service: "TemplateAgentService | None" = None


def get_template_agent_service() -> TemplateAgentService:
    global _service
    if _service is None:
        _service = TemplateAgentService()
    return _service
