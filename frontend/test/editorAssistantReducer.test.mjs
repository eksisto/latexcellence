import test from "node:test";
import assert from "node:assert/strict";

import {
  createStreamingPlaceholder,
  isStaleEvent,
  reduceAssistantCaptcha,
  reduceAssistantMessages,
  reduceAssistantPendingTool,
  reduceAssistantSessionId,
  reduceAssistantToolStatus,
} from "../src/lib/editor/assistantStream.js";

const PLACEHOLDER_ID = "tmp-42-abc123";

const USER_MESSAGE = {
  id: "u-1",
  role: "user",
  content: "帮我检查第一章的错别字",
  filePath: "main.tex",
};

/** 每个用例从「用户消息 + 流式占位」的初始视图出发 */
function initialMessages() {
  return [USER_MESSAGE, createStreamingPlaceholder(PLACEHOLDER_ID)];
}

function appendToken(messages, content) {
  return reduceAssistantMessages(
    messages,
    { type: "token", content },
    { placeholderId: PLACEHOLDER_ID },
  );
}

function deepFreeze(value) {
  if (value && typeof value === "object") {
    Object.freeze(value);
    for (const key of Object.keys(value)) deepFreeze(value[key]);
  }
  return value;
}

test("createStreamingPlaceholder 生成带初始字段的 assistant 占位消息", () => {
  assert.deepEqual(createStreamingPlaceholder(PLACEHOLDER_ID), {
    id: PLACEHOLDER_ID,
    role: "assistant",
    content: "",
    streaming: true,
    suggestions: [],
    appliedSuggestions: {},
  });
});

test("token 连续追加进同一条 streaming 占位消息", () => {
  const once = appendToken(initialMessages(), "第一段增量。");
  assert.equal(once.length, 2);
  assert.equal(once[1].content, "第一段增量。");
  assert.equal(once[1].streaming, true);
  assert.equal(once[1].id, PLACEHOLDER_ID);

  const twice = appendToken(once, "第二段增量。");
  assert.equal(twice.length, 2);
  assert.equal(twice[1].content, "第一段增量。第二段增量。");
  // 其余消息原样保留（同一引用，未被重建）
  assert.equal(twice[0], USER_MESSAGE);
});

test("round_start 重置占位消息内容以开启新一轮工具轮次", () => {
  const streamed = appendToken(initialMessages(), "将被丢弃的草稿。");
  const reset = reduceAssistantMessages(
    streamed,
    { type: "round_start" },
    { placeholderId: PLACEHOLDER_ID },
  );
  assert.equal(reset[1].content, "");
  // 其余字段维持流式中的形态
  assert.equal(reset[1].id, PLACEHOLDER_ID);
  assert.equal(reset[1].streaming, true);
  assert.deepEqual(reset[1].suggestions, []);
});

test("tool_status / tool_result 在工具指示器上的状态流转", () => {
  const statusEvent = {
    type: "tool_status",
    tool: "latex_check",
    status: "running",
  };
  // tool_status 把整个事件对象落为指示器值
  assert.equal(reduceAssistantToolStatus(null, statusEvent), statusEvent);

  // tool_result 清空指示器
  assert.equal(
    reduceAssistantToolStatus(statusEvent, {
      type: "tool_result",
      tool: "latex_check",
    }),
    null,
  );

  // done 收尾同样清空指示器
  assert.equal(
    reduceAssistantToolStatus(statusEvent, { type: "done", tokens: 3 }),
    null,
  );

  // 无关事件不改变指示器（原值透传）
  assert.equal(
    reduceAssistantToolStatus(statusEvent, { type: "token", content: "x" }),
    statusEvent,
  );
});

test("done 收尾：关闭 streaming 并按现实现落位 suggestions/tokens/id", () => {
  const streamed = appendToken(initialMessages(), "修改完成，共两处建议。");
  const suggestions = [{ index: 0, old_text: "a", new_text: "b" }];
  const doneEvent = {
    type: "done",
    session_id: "default:sess-9",
    message_id: "srv-msg-9",
    tokens: 321,
    suggestions,
  };

  const finished = reduceAssistantMessages(streamed, doneEvent, {
    placeholderId: PLACEHOLDER_ID,
  });
  assert.equal(finished.length, 2);
  const card = finished[1];
  // 占位 id 被服务端 message_id 替换，已累积 content 保持不变
  assert.equal(card.id, "srv-msg-9");
  assert.equal(card.streaming, false);
  assert.equal(card.content, "修改完成，共两处建议。");
  assert.equal(card.tokens, 321);
  // suggestions 数组按原引用落位（patch 展开语义）
  assert.equal(card.suggestions, suggestions);

  // 服务端 session_id 经独立归约同步到会话 id
  assert.equal(
    reduceAssistantSessionId("old:sess-1", doneEvent),
    "default:sess-9",
  );
});

test("done 缺省字段按现实现兜底", () => {
  const finished = reduceAssistantMessages(
    initialMessages(),
    { type: "done", suggestions: "不是数组", tokens: undefined },
    { placeholderId: PLACEHOLDER_ID },
  );
  // 无 message_id 回落占位 id；非数组 suggestions 回落空数组；tokens 回落 0
  assert.equal(finished[1].id, PLACEHOLDER_ID);
  assert.equal(finished[1].streaming, false);
  assert.deepEqual(finished[1].suggestions, []);
  assert.equal(finished[1].tokens, 0);
  // done 未携带 session_id 时会话 id 维持原值
  assert.equal(
    reduceAssistantSessionId("keep:sess", { type: "done" }),
    "keep:sess",
  );
});

test("error 事件移除占位消息且其余消息原样保留", () => {
  const remaining = reduceAssistantMessages(
    initialMessages(),
    { type: "error", message: "上游模型超时" },
    { placeholderId: PLACEHOLDER_ID },
  );
  assert.deepEqual(remaining, [USER_MESSAGE]);
});

test("done 携带 captcha_pending 时删除占位（阶段五a DDG 挑战终流）", () => {
  const streamed = appendToken(initialMessages(), "将被丢弃的草稿");
  const doneEvent = {
    type: "done",
    session_id: "default:sess-cap",
    message_id: "",
    tokens: 0,
    captcha_pending: true,
  };
  const result = reduceAssistantMessages(streamed, doneEvent, {
    placeholderId: PLACEHOLDER_ID,
  });
  // 后端未持久化 assistant 消息：删占位、保留 user 消息，不留幽灵空回复
  assert.deepEqual(result, [USER_MESSAGE]);

  // 会话 id 照常回写（user 消息已在服务端落盘，重发需挂到同一会话）
  assert.equal(
    reduceAssistantSessionId("old:sess", doneEvent),
    "default:sess-cap",
  );
});

test("done 未携带 captcha_pending 时照常落位（反例）", () => {
  const streamed = appendToken(initialMessages(), "正常回复内容");
  const result = reduceAssistantMessages(
    streamed,
    { type: "done", message_id: "m-ok", tokens: 4, suggestions: [] },
    { placeholderId: PLACEHOLDER_ID },
  );
  assert.equal(result.length, 2);
  assert.equal(result[1].id, "m-ok");
  assert.equal(result[1].streaming, false);
  assert.equal(result[1].content, "正常回复内容");
});

test("captcha_challenge 清空工具指示器且归一化挑战对象", () => {
  const statusEvent = { type: "tool_status", tool: "web_search" };
  // 挑战接管流暂停态：指示器清空，不滞留"工具执行中"
  assert.equal(
    reduceAssistantToolStatus(statusEvent, {
      type: "captcha_challenge",
      challenge_id: "c1",
    }),
    null,
  );
  // 无关事件不清空（原值透传）
  assert.equal(
    reduceAssistantToolStatus(statusEvent, { type: "token", content: "x" }),
    statusEvent,
  );

  const event = {
    type: "captcha_challenge",
    challenge_id: "c1",
    images: [{ hash: "h1", base64: "Zm9v" }],
    query: "latex tutorial",
    engine_name: "DuckDuckGo",
    instruction: "Pick the ducks",
  };
  assert.deepEqual(reduceAssistantCaptcha(null, event), {
    challenge_id: "c1",
    images: [{ hash: "h1", base64: "Zm9v" }],
    query: "latex tutorial",
    engine_name: "DuckDuckGo",
    instruction: "Pick the ducks",
  });
  // images 缺省兜底空数组
  assert.deepEqual(
    reduceAssistantCaptcha(null, {
      type: "captcha_challenge",
      challenge_id: "c2",
    }).images,
    [],
  );
  // 非 captcha_challenge 事件维持原值引用
  const existing = { challenge_id: "keep" };
  assert.equal(reduceAssistantCaptcha(existing, { type: "done" }), existing);
});

test("error 事件在占位已不存在时安全幂等", () => {
  const once = reduceAssistantMessages(
    initialMessages(),
    { type: "error" },
    { placeholderId: PLACEHOLDER_ID },
  );
  const twice = reduceAssistantMessages(
    once,
    { type: "error" },
    { placeholderId: PLACEHOLDER_ID },
  );
  assert.deepEqual(twice, [USER_MESSAGE]);
});

test("isStaleEvent 过期 seq 一律判过期（error 类型不豁免）", () => {
  // 守卫只比较请求序号：项目切换后 seq 自增使在途流全部过期
  assert.equal(isStaleEvent(1, 2), true);
  assert.equal(isStaleEvent(2, 2), false);

  // 对每种事件类型守卫语义一致：过期即丢弃，新鲜即放行
  for (const event of [
    { type: "token", content: "迟到的增量" },
    { type: "round_start" },
    { type: "tool_status", tool: "latex_check" },
    { type: "tool_result", tool: "latex_check" },
    { type: "compressed" },
    { type: "done", message_id: "m-late" },
    { type: "error", message: "迟到的失败" },
  ]) {
    assert.equal(
      isStaleEvent(7, 8),
      true,
      `${event.type} 迟到事件应被守卫丢弃`,
    );
    assert.equal(isStaleEvent(8, 8), false, `${event.type} 新鲜事件应正常处理`);
  }
});

test("无关事件与占位缺失时原样返回输入数组引用", () => {
  const msgs = initialMessages();
  // 不影响消息列表的事件类型
  assert.equal(
    reduceAssistantMessages(
      msgs,
      { type: "compressed" },
      {
        placeholderId: PLACEHOLDER_ID,
      },
    ),
    msgs,
  );
  // 占位不存在时不产生写入（镜像原实现 find 守卫）
  assert.equal(
    reduceAssistantMessages(
      msgs,
      { type: "token", content: "孤儿增量" },
      { placeholderId: "tmp-not-exist" },
    ),
    msgs,
  );
});

test("归约函数不可变更新：输入数组与其中的消息不被修改", () => {
  const frozen = deepFreeze([
    { ...USER_MESSAGE },
    createStreamingPlaceholder(PLACEHOLDER_ID),
  ]);
  const snapshot = JSON.parse(JSON.stringify(frozen));

  // ESM 严格模式下写入冻结对象会抛 TypeError；以下归约应安静返回新数组
  const afterToken = appendToken(frozen, "冻结后的增量");
  const afterRound = reduceAssistantMessages(
    afterToken,
    { type: "round_start" },
    { placeholderId: PLACEHOLDER_ID },
  );
  const afterDone = reduceAssistantMessages(
    afterToken,
    { type: "done", message_id: "m-1", tokens: 5, suggestions: [] },
    { placeholderId: PLACEHOLDER_ID },
  );
  const afterError = reduceAssistantMessages(
    afterToken,
    { type: "error" },
    { placeholderId: PLACEHOLDER_ID },
  );

  // 冻结输入未被改写
  assert.deepEqual(frozen, snapshot);
  assert.equal(frozen[1].content, "");

  // 变更型归约均产生新数组
  assert.notEqual(afterToken, frozen);
  assert.notEqual(afterRound, afterToken);
  assert.notEqual(afterDone, afterToken);
  assert.notEqual(afterError, afterToken);

  // 归约结果本身也符合预期
  assert.equal(afterDone[1].id, "m-1");
  assert.deepEqual(afterError, [frozen[0]]);
});

// ── 阶段五b：工具确认流（tool_pending / tool_cancelled） ─────────────────

const PENDING_EVENT = {
  type: "tool_pending",
  pending_id: "pid1",
  tool: "web_search",
  label: "searching",
  args: { query: "latex bib style" },
};

test("tool_pending 归一化为确认卡对象（args 非对象兜底空对象）", () => {
  assert.deepEqual(reduceAssistantPendingTool(null, PENDING_EVENT), {
    pending_id: "pid1",
    tool: "web_search",
    label: "searching",
    args: { query: "latex bib style" },
  });
  // args 缺失/非对象时兜底空对象；tool/label 缺省回落空串
  assert.deepEqual(
    reduceAssistantPendingTool(null, {
      type: "tool_pending",
      pending_id: "pid2",
      args: "不是对象",
    }),
    { pending_id: "pid2", tool: "", label: "", args: {} },
  );
  // createdAt 由 hook 副作用层补盖，归约不产生时间戳
  assert.equal(
    "createdAt" in reduceAssistantPendingTool(null, PENDING_EVENT),
    false,
  );
});

test("tool_cancelled / done / error 终结确认卡，无关事件维持原值", () => {
  const card = { pending_id: "pid1", tool: "web_search" };
  assert.equal(
    reduceAssistantPendingTool(card, {
      type: "tool_cancelled",
      tool: "web_search",
    }),
    null,
  );
  assert.equal(reduceAssistantPendingTool(card, { type: "done" }), null);
  assert.equal(reduceAssistantPendingTool(card, { type: "error" }), null);
  // 确认后续流中的 token / tool_status 不清卡（卡片由用户动作清除）
  assert.equal(
    reduceAssistantPendingTool(card, { type: "token", content: "x" }),
    card,
  );
  assert.equal(
    reduceAssistantPendingTool(card, {
      type: "tool_status",
      tool: "open_page",
    }),
    card,
  );
  // 无卡时取消/终流幂等（保持 null）
  assert.equal(
    reduceAssistantPendingTool(null, { type: "tool_cancelled" }),
    null,
  );
});

test("tool_pending / tool_cancelled 清空工具指示器（确认流暂停态不滞留）", () => {
  const statusEvent = { type: "tool_status", tool: "open_page" };
  assert.equal(
    reduceAssistantToolStatus(statusEvent, {
      type: "tool_pending",
      pending_id: "p",
    }),
    null,
  );
  assert.equal(
    reduceAssistantToolStatus(statusEvent, { type: "tool_cancelled" }),
    null,
  );
});

test("确认恢复流：pending → 确认后 tool_status/tool_result → done 兜底清卡", () => {
  // 1. pending 到达：卡片落位、指示器清空
  let card = reduceAssistantPendingTool(null, PENDING_EVENT);
  let status = reduceAssistantToolStatus(null, PENDING_EVENT);
  assert.equal(card.pending_id, "pid1");
  assert.equal(status, null);

  // 2. 用户确认（hook 调端点后清卡；流内续传 tool_status）
  status = reduceAssistantToolStatus(status, {
    type: "tool_status",
    tool: "web_search",
  });
  assert.equal(status.tool, "web_search");

  // 3. tool_result 清指示器，token 流正常
  status = reduceAssistantToolStatus(status, {
    type: "tool_result",
    tool: "web_search",
  });
  assert.equal(status, null);

  // 4. done 兜底清卡（防异常时序残留死卡）
  assert.equal(reduceAssistantPendingTool(card, { type: "done" }), null);
});

test("取消流：pending → tool_cancelled → 卡与指示器均终结", () => {
  const card = reduceAssistantPendingTool(null, PENDING_EVENT);
  const status = reduceAssistantToolStatus(null, PENDING_EVENT);
  const cancelled = {
    type: "tool_cancelled",
    pending_id: "pid1",
    tool: "web_search",
  };
  assert.equal(reduceAssistantPendingTool(card, cancelled), null);
  assert.equal(reduceAssistantToolStatus(status, cancelled), null);
  // 消息列表不受 tool_cancelled 影响（占位消息保持流式形态，原引用返回）
  const before = initialMessages();
  const msgs = reduceAssistantMessages(before, cancelled, {
    placeholderId: PLACEHOLDER_ID,
  });
  assert.equal(msgs, before);
});

test("pending 期间新 tool_pending 到达：新卡替换旧卡（不同 pending_id）", () => {
  const first = reduceAssistantPendingTool(null, PENDING_EVENT);
  const second = reduceAssistantPendingTool(first, {
    type: "tool_pending",
    pending_id: "pid2",
    tool: "search_scholarly_works",
    label: "searching_scholarly",
    args: { query: "openalex" },
  });
  assert.equal(second.pending_id, "pid2");
  assert.equal(second.tool, "search_scholarly_works");
});

test("确认流事件对消息列表无影响（token/round_start 照常归约）", () => {
  const streamed = appendToken(initialMessages(), "思考片段");
  const afterPending = reduceAssistantMessages(streamed, PENDING_EVENT, {
    placeholderId: PLACEHOLDER_ID,
  });
  assert.equal(afterPending, streamed);
  const afterCancelled = reduceAssistantMessages(
    afterPending,
    { type: "tool_cancelled", pending_id: "pid1" },
    { placeholderId: PLACEHOLDER_ID },
  );
  assert.equal(afterCancelled, streamed);
  // round_start 后新轮次 token 照常写入占位
  const reset = reduceAssistantMessages(
    afterCancelled,
    { type: "round_start" },
    { placeholderId: PLACEHOLDER_ID },
  );
  assert.equal(reset[1].content, "");
});
