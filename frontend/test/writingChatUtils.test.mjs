import test from "node:test";
import assert from "node:assert/strict";

import {
  parseThinking,
  isVisibleChatMessage,
  processLoadedMessages,
  resetStreamingContent,
  isDraftSession,
} from "../src/hooks/writing-chat/utils.js";

// ── parseThinking ──────────────────────────────────────────────────────

test("parseThinking 无 thinking 标签时原样返回", () => {
  const parsed = parseThinking("纯正文内容");
  assert.equal(parsed.thinking, "");
  assert.equal(parsed.display, "纯正文内容");
  assert.equal(parsed.thinkingOpen, false);
  assert.equal(parsed.thinkingClosed, true);
});

test("parseThinking 提取单个 thinking 块", () => {
  const parsed = parseThinking(
    "<thinking>\n1. 核心主张\n</thinking>\n\n### 正文",
  );
  assert.equal(parsed.thinking, "\n1. 核心主张\n");
  assert.equal(parsed.display, "### 正文");
  assert.equal(parsed.thinkingOpen, true);
  assert.equal(parsed.thinkingClosed, true);
});

test("parseThinking 剥离正文中残留的完整 thinking 块", () => {
  const parsed = parseThinking(
    "<thinking>A</thinking><thinking>B</thinking>正文",
  );
  assert.equal(parsed.thinking, "A");
  assert.equal(parsed.display, "正文");
});

test("parseThinking 未闭合 thinking 块保持打开状态", () => {
  const parsed = parseThinking("正文<thinking>还在思考");
  assert.equal(parsed.thinking, "还在思考");
  assert.equal(parsed.display, "正文");
  assert.equal(parsed.thinkingClosed, false);
});

// ── isVisibleChatMessage ───────────────────────────────────────────────

test("isVisibleChatMessage 用户与 summary 消息可见", () => {
  assert.equal(isVisibleChatMessage({ role: "user", content: "hi" }), true);
  assert.equal(isVisibleChatMessage({ role: "summary", content: "s" }), true);
});

test("isVisibleChatMessage 工具内部消息隐藏", () => {
  assert.equal(
    isVisibleChatMessage({ role: "tool", content: "result" }),
    false,
  );
});

test("isVisibleChatMessage 仅含 tool_calls 的 assistant 消息隐藏", () => {
  assert.equal(
    isVisibleChatMessage({
      role: "assistant",
      content: "",
      tool_calls: [{ id: "call_1" }],
    }),
    false,
  );
});

test("isVisibleChatMessage 空 assistant 消息隐藏，流式/有内容/带结果时可见", () => {
  assert.equal(isVisibleChatMessage({ role: "assistant", content: "" }), false);
  assert.equal(
    isVisibleChatMessage({ role: "assistant", content: "", streaming: true }),
    true,
  );
  assert.equal(
    isVisibleChatMessage({ role: "assistant", content: "正文" }),
    true,
  );
  assert.equal(
    isVisibleChatMessage({
      role: "assistant",
      content: "",
      searchResults: [{ title: "t" }],
    }),
    true,
  );
  assert.equal(
    isVisibleChatMessage({
      role: "assistant",
      content: "",
      scholarlyResults: [{ title: "s" }],
    }),
    true,
  );
  assert.equal(
    isVisibleChatMessage({ role: "assistant", content: "", edit_op: {} }),
    true,
  );
});

test("isVisibleChatMessage 非法输入返回 false", () => {
  assert.equal(isVisibleChatMessage(null), false);
  assert.equal(isVisibleChatMessage(undefined), false);
});

// ── processLoadedMessages ──────────────────────────────────────────────

test("processLoadedMessages 归一化 scholarly 与 search 字段", () => {
  const loaded = processLoadedMessages([
    {
      id: "a1",
      role: "assistant",
      content: "正文",
      scholarly_results: [{ id: "W1", title: "paper" }],
      scholarly_query: "q",
      scholarly_total: 3,
      search_results: [{ index: 1, title: "t", snippet: "s", url: "u" }],
      search_query: "sq",
      deep_read_pages: 2,
      rewritten_queries: ["q1"],
    },
  ]);
  assert.equal(loaded[0].scholarlyResults[0].id, "W1");
  assert.equal(loaded[0].scholarlyQuery, "q");
  assert.equal(loaded[0].scholarlyTotal, 3);
  assert.equal(loaded[0].searchResults[0].url, "u");
  assert.equal(loaded[0].searchQuery, "sq");
  assert.equal(loaded[0].deepReadPages, 2);
  assert.deepEqual(loaded[0].rewrittenQueries, ["q1"]);
});

test("processLoadedMessages 从 content 提取 thinking 并保留正文", () => {
  const loaded = processLoadedMessages([
    {
      id: "a2",
      role: "assistant",
      content: "<thinking>思考</thinking>\n\n正文",
    },
  ]);
  assert.equal(loaded[0].thinkingContent, "思考");
  assert.equal(loaded[0].displayContent, "正文");
  assert.equal(loaded[0].thinkingClosed, true);
});

test("processLoadedMessages 已解析字段不重复处理", () => {
  const loaded = processLoadedMessages([
    {
      id: "a3",
      role: "assistant",
      content: "<thinking>思考</thinking>正文",
      thinkingContent: "已解析",
      displayContent: "已展示",
    },
  ]);
  assert.equal(loaded[0].thinkingContent, "已解析");
  assert.equal(loaded[0].displayContent, "已展示");
});

// ── resetStreamingContent ──────────────────────────────────────────────

test("resetStreamingContent 清空目标消息文本字段并保留工具结果", () => {
  const messages = [
    { id: "u1", role: "user", content: "问题" },
    {
      id: "a1",
      role: "assistant",
      rawContent: "旧文本",
      content: "旧文本",
      thinkingContent: "旧思考",
      displayContent: "旧展示",
      thinkingOpen: true,
      thinkingClosed: false,
      searchResults: [{ title: "t" }],
      scholarlyResults: [{ title: "s" }],
      streaming: true,
    },
  ];
  const reset = resetStreamingContent(messages, "a1");
  const target = reset.find((m) => m.id === "a1");
  assert.equal(target.rawContent, "");
  assert.equal(target.content, "");
  assert.equal(target.thinkingContent, "");
  assert.equal(target.displayContent, "");
  assert.equal(target.thinkingOpen, false);
  assert.equal(target.thinkingClosed, true);
  assert.equal(target.streaming, true);
  assert.equal(target.searchResults.length, 1);
  assert.equal(target.scholarlyResults.length, 1);
  assert.equal(reset[0], messages[0], "其他消息保持不变");
});

// ── isDraftSession ─────────────────────────────────────────────────────

test("isDraftSession 仅识别 __draft__ 前缀", () => {
  assert.equal(isDraftSession("__draft__abc"), true);
  assert.equal(isDraftSession("scope:uuid"), false);
  assert.equal(isDraftSession(""), false);
  assert.equal(isDraftSession(null), false);
});
