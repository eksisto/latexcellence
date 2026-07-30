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
  const afterThink = thinkClose !== -1 ? rawText.slice(thinkClose + 11) : "";
  const display = (beforeThink + afterThink).trim();
  return {
    thinking,
    display,
    thinkingOpen: true,
    thinkingClosed: thinkClose !== -1,
  };
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
