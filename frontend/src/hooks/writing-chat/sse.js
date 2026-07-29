/**
 * Writing Chat SSE 事件处理逻辑。
 */

import { toast } from "sonner";
import i18n from "@/i18n";
import { parseThinking, isDraftSession } from "./utils";
import { translateError } from "@/utils/errorMessages";

/**
 * 创建 SSE 事件处理器。
 */
export function createSSEEventHandler({
  tmpAssistantId,
  setWritingChatMessages,
  setSearchStatus,
  setCaptchaChallenge,
  setWritingChatCompressed,
  setWritingChatSessions,
  setWritingChatCurrentSessionId,
  setError,
  writingChatCurrentSessionId,
}) {
  return (event) => {
    if (event.type === "token") {
      setWritingChatMessages((prev) =>
        prev.map((m) => {
          if (m.id !== tmpAssistantId) return m;
          const raw = (m.rawContent || "") + event.content;
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
    } else if (event.type === "searching") {
      setSearchStatus({ query: event.query, engine: event.engine });
    } else if (event.type === "tool_result") {
      setSearchStatus(null);
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
    } else if (event.type === "captcha_challenge") {
      setSearchStatus(null);
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
    } else if (event.type === "done") {
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
}
