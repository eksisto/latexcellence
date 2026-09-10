/**
 * Writing Chat SSE 事件处理逻辑。
 */

import { toast } from "sonner";
import i18n from "@/i18n";
import { parseThinking, isDraftSession, resetStreamingContent } from "./utils";
import { translateError } from "@/utils/errorMessages";

/**
 * 创建 SSE 事件处理器。
 */
export function createSSEEventHandler({
  tmpAssistantId,
  setWritingChatMessages,
  setToolStatus,
  setPendingTool,
  setPendingClarification,
  setCaptchaChallenge,
  setWritingChatCompressed,
  setWritingChatSessions,
  setWritingChatCurrentSessionId,
  setError,
  writingChatCurrentSessionId,
}) {
  // token 缓冲：requestAnimationFrame 批量提交，每帧至多一次 store 写入。
  // 此前每条 token 单独 setWritingChatMessages + 对全文重跑 parseThinking，
  // 长回复时整页重渲染与字符串扫描成本随 token 数线性放大
  // （useParagraphCheck 的 scheduleFlush 为同款先例）。
  let pendingText = "";
  let flushHandle = null;

  const applyPendingText = (chunk) => {
    setWritingChatMessages((prev) =>
      prev.map((m) => {
        if (m.id !== tmpAssistantId) return m;
        const raw = (m.rawContent || "") + chunk;
        const parsed = parseThinking(raw);
        return {
          ...m,
          rawContent: raw,
          content: raw,
          thinkingContent: parsed.thinking,
          displayContent: parsed.display,
          thinkingOpen: parsed.thinkingOpen,
          thinkingClosed: parsed.thinkingClosed,
        };
      }),
    );
  };

  const flushPendingTokens = () => {
    if (flushHandle != null) {
      cancelAnimationFrame(flushHandle);
      flushHandle = null;
    }
    if (!pendingText) return;
    const chunk = pendingText;
    pendingText = "";
    applyPendingText(chunk);
  };

  const handler = (event) => {
    if (event.type === "token") {
      pendingText += event.content || "";
      if (flushHandle == null) {
        flushHandle = requestAnimationFrame(() => {
          flushHandle = null;
          flushPendingTokens();
        });
      }
      return;
    }
    // 非 token 事件先同步落盘已缓冲 token，保证事件顺序
    //（如 round_start 的重置只应作用于已展示的内容）
    flushPendingTokens();
    if (event.type === "round_start") {
      // 新一轮工具轮次开始：重置累积的流式内容，只保留最后一轮的回复，
      // 与后端落盘的 assistant 消息内容保持一致
      setWritingChatMessages((prev) =>
        resetStreamingContent(prev, tmpAssistantId),
      );
    } else if (event.type === "tool_status") {
      // web_search / project_tools 调用中 → 显示工具状态指示器
      setToolStatus(event);
    } else if (event.type === "web_stage") {
      setToolStatus(event.status === "complete" ? null : event);
    } else if (event.type === "tool_pending") {
      // 敏感工具等待用户确认
      setToolStatus(null);
      setPendingTool(event);
    } else if (event.type === "tool_cancelled") {
      // 工具被用户取消
      setToolStatus(null);
      setPendingTool(null);
      toast.info(i18n.t("hooks:writingChat.toolCancelled"), {
        description: event.tool,
      });
    } else if (event.type === "clarification_request") {
      // LLM 向用户提问，等待回复
      setToolStatus(null);
      setPendingClarification(event);
    } else if (event.type === "tool_result") {
      // 工具执行完毕 → 隐藏指示器
      setToolStatus(null);
      // 存储搜索结果到 assistant 消息
      if (event.search_results && event.search_results.length > 0) {
        setWritingChatMessages((prev) =>
          prev.map((m) => {
            if (m.id !== tmpAssistantId) return m;
            return {
              ...m,
              searchResults: event.search_results,
              searchQuery: event.query || "",
              deepReadPages: event.deep_read_pages || 0,
              rewrittenQueries: event.rewritten_queries || [],
            };
          }),
        );
      }
      if (event.scholarly_results && event.scholarly_results.length > 0) {
        setWritingChatMessages((prev) =>
          prev.map((m) => {
            if (m.id !== tmpAssistantId) return m;
            const combined = [
              ...(m.scholarlyResults || []),
              ...event.scholarly_results,
            ];
            const uniqueResults = Array.from(
              new Map(
                combined.map((result) => [
                  result.id || result.doi || result.title,
                  result,
                ]),
              ).values(),
            );
            return {
              ...m,
              scholarlyResults: uniqueResults,
              scholarlyQuery: event.query || "",
              scholarlyTotal: Math.max(
                m.scholarlyTotal || 0,
                event.total_estimated || 0,
              ),
            };
          }),
        );
      }
      if (event.evidence_bundle?.evidence?.length > 0 || event.page_read) {
        setWritingChatMessages((prev) =>
          prev.map((m) => {
            if (m.id !== tmpAssistantId) return m;
            const evidence = [
              ...(m.evidenceSources || []),
              ...(event.evidence_bundle?.evidence || []),
            ];
            const uniqueEvidence = Array.from(
              new Map(
                evidence.map((item) => [item.evidence_id, item]),
              ).values(),
            );
            const pageRead = event.page_read
              ? {
                  ...event.page_read,
                  error_message: event.page_read.error_message
                    ? translateError(event.page_read.error_message)
                    : "",
                }
              : null;
            return {
              ...m,
              evidenceSources: uniqueEvidence,
              pageReads: pageRead
                ? [...(m.pageReads || []), pageRead]
                : m.pageReads || [],
            };
          }),
        );
      }
    } else if (event.type === "captcha_challenge") {
      setToolStatus(null);
      setCaptchaChallenge({
        challenge_id: event.challenge_id,
        images: event.images || [],
        query: event.query || "",
        engine_name: event.engine_name || "",
        instruction: event.instruction || "",
      });
    } else if (event.type === "compressed") {
      setWritingChatCompressed(true);
      toast.info(i18n.t("hooks:writingChat.toastCompressed"), {
        description: i18n.t("hooks:writingChat.toastCompressedDesc"),
      });
    } else if (event.type === "citation_note") {
      // 引用校验结果：回复中存在未在搜索结果中出现的链接
      setWritingChatMessages((prev) =>
        prev.map((m) =>
          m.id === tmpAssistantId
            ? {
                ...m,
                citationNote: event.note || "",
                unverifiedCitations: event.unverified || [],
              }
            : m,
        ),
      );
    } else if (event.type === "done") {
      setToolStatus(null);
      if (event.captcha_pending) {
        // 验证码流程中止：后端未持久化 assistant 消息，移除占位消息，
        // 避免刷新后出现 JSON 中不存在的"幽灵"回复
        setWritingChatMessages((prev) =>
          prev.filter((m) => m.id !== tmpAssistantId),
        );
      } else {
        setWritingChatMessages((prev) =>
          prev.map((m) =>
            m.id === tmpAssistantId
              ? {
                  ...m,
                  id: event.message_id || m.id,
                  streaming: false,
                  tokens: event.tokens || 0,
                }
              : m,
          ),
        );
      }
      if (event.session_id) {
        if (isDraftSession(writingChatCurrentSessionId)) {
          setWritingChatSessions((prev) =>
            prev.map((s) =>
              s.id === writingChatCurrentSessionId
                ? { id: event.session_id, title: s.title }
                : s,
            ),
          );
        }
        if (event.session_id !== writingChatCurrentSessionId) {
          setWritingChatCurrentSessionId(event.session_id);
        }
      }
    } else if (event.type === "error") {
      const msg = translateError(event.message);
      setError(msg);
      // 移除占位的 assistant 消息
      setWritingChatMessages((prev) =>
        prev.filter((m) => m.id !== tmpAssistantId),
      );
      toast.error(i18n.t("hooks:writingChat.chatError"), { description: msg });
    }
  };
  // 流收尾兜底：正常 done/error 已同步落盘，这里覆盖「无 done 事件即结束」
  // 的异常收尾（由 useWritingChat 在 finally 中调用）
  handler.flush = flushPendingTokens;
  return handler;
}
