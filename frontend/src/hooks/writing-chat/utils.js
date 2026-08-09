/**
 * Writing Chat 工具函数。
 */

/**
 * 解析 thinking 标签内容。
 */
export function parseThinking(rawText) {
  const thinkOpen = rawText.indexOf("<thinking>");
  if (thinkOpen === -1) {
    return {
      thinking: "",
      display: rawText,
      thinkingOpen: false,
      thinkingClosed: true,
    };
  }
  const thinkClose = rawText.indexOf("</thinking>", thinkOpen + 10);
  const thinking = rawText.slice(
    thinkOpen + 10,
    thinkClose !== -1 ? thinkClose : undefined,
  );
  const beforeThink = rawText.slice(0, thinkOpen);
  let afterThink = thinkClose !== -1 ? rawText.slice(thinkClose + 11) : "";
  if (thinkClose !== -1) {
    // 剥离正文中残留的完整 thinking 块，防止模型输出多个
    // <thinking>...</thinking> 时标签内容泄漏到正文字面展示
    afterThink = afterThink.replace(/<thinking>[\s\S]*?<\/thinking>/g, "");
  }
  const display = (beforeThink + afterThink).trim();
  return {
    thinking,
    display,
    thinkingOpen: true,
    thinkingClosed: thinkClose !== -1,
  };
}

/**
 * 判断消息是否应在聊天区展示。
 *
 * - role=tool 的工具执行结果属于内部上下文，只用于回传 LLM，不直接展示
 * - 仅携带 tool_calls、没有任何可见内容的 assistant 消息同样隐藏
 *   （工具调用过程由 ToolStatusBar 指示器呈现）
 */
export function isVisibleChatMessage(m) {
  if (!m) return false;
  if (m.role === "user" || m.role === "summary") return true;
  if (m.role === "tool") return false;
  if (m.role === "assistant") {
    return Boolean(
      m.streaming ||
      m.content ||
      m.thinkingContent ||
      m.displayContent ||
      m.edit_op ||
      (Array.isArray(m.searchResults) && m.searchResults.length > 0) ||
      (Array.isArray(m.scholarlyResults) && m.scholarlyResults.length > 0),
    );
  }
  return false;
}

/**
 * 重置某条流式 assistant 消息的累积文本（round_start 事件用）。
 *
 * 只清空文本类字段，保留 searchResults / scholarlyResults 等跨轮次
 * 累积的工具结果，与后端"仅最后一轮文本落盘"的语义保持一致。
 */
export function resetStreamingContent(messages, targetId) {
  return messages.map((m) =>
    m.id === targetId
      ? {
          ...m,
          rawContent: "",
          content: "",
          thinkingContent: "",
          displayContent: "",
          thinkingOpen: false,
          thinkingClosed: true,
        }
      : m,
  );
}

/**
 * 处理加载的消息，从 raw content 中提取 thinking 内容。
 */
export function processLoadedMessages(messages) {
  return messages.map((msg) => {
    const normalized = {
      ...msg,
      scholarlyResults: msg.scholarly_results || msg.scholarlyResults || [],
      scholarlyQuery: msg.scholarly_query || msg.scholarlyQuery || "",
      scholarlyTotal: msg.scholarly_total || msg.scholarlyTotal || 0,
      searchResults: msg.search_results || msg.searchResults || [],
      searchQuery: msg.search_query || msg.searchQuery || "",
      deepReadPages: msg.deep_read_pages || msg.deepReadPages || 0,
      rewrittenQueries: msg.rewritten_queries || msg.rewrittenQueries || [],
    };
    if (msg.role !== "assistant" || !msg.content) return normalized;
    if (!/<thinking>[\s\S]*?<\/thinking>/.test(msg.content)) return normalized;
    // Already has parsed thinking fields
    if (msg.thinkingContent !== undefined) return normalized;
    const parsed = parseThinking(msg.content);
    return {
      ...normalized,
      thinkingContent: parsed.thinking,
      displayContent: parsed.display,
      thinkingOpen: false,
      thinkingClosed: parsed.thinkingClosed,
    };
  });
}

/**
 * 草稿会话 ID 前缀。
 */
export const DRAFT_PREFIX = "__draft__";

/**
 * 判断是否为草稿会话。
 */
export const isDraftSession = (id) =>
  typeof id === "string" && id.startsWith(DRAFT_PREFIX);

/**
 * 生成唯一 ID。
 */
export const uid = () =>
  typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2) + Date.now().toString(36);
