"""模板生成 Agent 主循环的对话/状态辅助函数。"""

from typing import Any


def _content_hash(content: str) -> str:
    """计算写入内容的 SHA-256，用于识别"原样重发被拒内容"的退化循环。"""
    import hashlib

    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def _compact_messages(
    messages: list[dict[str, Any]], *, keep_rounds: int = 12
) -> list[dict[str, Any]]:
    """压缩 Agent 对话历史，防止超长工具调用把上下文撑爆。

    保留全部 system 消息（核心 system 与阶段 3 注入的 planning /
    compile_phase / validation_phase 块）与最初的 user 需求，然后只保留
    最近 keep_rounds 个 assistant 轮次及其随后的 tool 结果。更早的轮次
    替换为一条摘要提示，避免旧的大文件 tool_call 参数继续占用上下文。
    """
    if len(messages) <= 4:
        return messages
    assistant_indices = [
        i for i, message in enumerate(messages) if message.get("role") == "assistant"
    ]
    if len(assistant_indices) <= keep_rounds:
        return messages
    cut = assistant_indices[-keep_rounds]
    prefix = messages[:2] + [
        message for message in messages[2:] if message.get("role") == "system"
    ]
    summary = {
        "role": "user",
        "content": (
            "[Earlier generation rounds were compacted to fit the context "
            "window. The current project files on disk are the source of "
            "truth; continue from that state and call the next tool.]"
        ),
    }
    recent_messages = [
        message for message in messages[cut:] if message.get("role") != "system"
    ]
    return prefix + [summary] + recent_messages


def _nudge_message() -> str:
    """规划瘫痪引导文案（仅注入 LLM 历史与 trace，不推送到前端）。"""
    return (
        "You replied without calling any tool, which counts as a stalled round. "
        "Immediately call the next tool (write_file / read_file / list_files / "
        "compile_latex / finish) — never reply with prose only. Keep plan text to "
        "at most 2 sentences."
    )


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
