/**
 * 编辑器 AI 助手 SSE 事件归约纯函数（自 useEditorAssistant 抽出）。
 *
 * 本模块只做「事件 → 会话状态」的不可变归约，不感知 React 与 Zustand：
 *   - 消息列表归约：reduceAssistantMessages；
 *   - 工具调用指示器归约：reduceAssistantToolStatus（对应独立于消息列表的
 *     editorStore.assistantToolStatus 字段，不在占位消息上）；
 *   - 会话 id 归约：reduceAssistantSessionId（done 回填服务端 scoped id）；
 *   - DDG 挑战归约：reduceAssistantCaptcha（captcha_challenge 事件 →
 *     useWritingChatCaptcha 通道的挑战对象，阶段五a）；
 *   - 过期守卫判断：isStaleEvent；
 *   - 占位消息工厂：createStreamingPlaceholder。
 *
 * toast、translateError、AbortController、seq 计数等副作用仍由
 * useEditorAssistant hook 编排；本模块全部为纯输入输出。
 */

/**
 * 生成发送时的 assistant 占位消息（streaming 打开、内容为空、建议列表待落位）。
 * id 由调用方生成后传入（hook 内经 tempId()），保持本函数确定性可测。
 *
 * @param {string} id 占位消息 id（形如 tmp-<ts>-<rand>）
 * @returns {{
 *   id: string,
 *   role: "assistant",
 *   content: string,
 *   streaming: boolean,
 *   suggestions: Array,
 *   appliedSuggestions: Object,
 * }} 占位消息对象
 */
export function createStreamingPlaceholder(id) {
  return {
    id,
    role: "assistant",
    content: "",
    streaming: true,
    suggestions: [],
    appliedSuggestions: {},
  };
}

/**
 * 在途请求过期守卫：请求发起时的序号不等于当前序号即视为过期
 * （项目切换 / 组件卸载会使 seq 自增使在途流全部过期）。
 * 对一切事件类型一视同仁，error 事件同样不豁免——过期请求的
 * 迟到事件一律丢弃。
 *
 * @param {number} seq 事件所属请求的序号
 * @param {number} currentSeq 当前最新请求序号
 * @returns {boolean} 是否过期（true 表示应丢弃该事件）
 */
export function isStaleEvent(seq, currentSeq) {
  return seq !== currentSeq;
}

/**
 * 以不可变方式对占位消息做局部更新（镜像 editorStore.patchAssistantMessage
 * 的 map 展开语义：始终产生新数组，未命中时各消息内容不变）。
 *
 * @param {Array<object>} messages 当前消息数组
 * @param {string} placeholderId 占位消息 id
 * @param {object} patch 待合并的字段补丁
 * @returns {Array<object>} 新消息数组；未命中占位时返回原数组引用
 */
function patchPlaceholderInList(messages, placeholderId, patch) {
  // 单趟完成定位与重建（未命中时不产生写入）
  let hit = false;
  const next = messages.map((m) => {
    if (m.id !== placeholderId) return m;
    hit = true;
    return { ...m, ...patch };
  });
  return hit ? next : messages;
}

/**
 * 归约单条 SSE 事件对消息列表的影响，返回新数组（不可变更新，不改入参）：
 *   - token：把增量文本追加到占位消息 content（占位不存在时原样返回）；
 *   - round_start：新一轮工具轮次，重置占位消息 content 为空串，
 *     与后端落盘内容保持一致；
 *   - done：收尾——关闭 streaming，用服务端 message_id 替换占位 id
 *     （缺省回落占位 id），suggestions 非数组回落空数组，tokens 缺省 0，
 *     已累积 content 保持不变；
 *   - error：移除占位消息，其余消息原样保留；
 *   - 其他事件（tool_status / tool_result / compressed / 未知类型）：
 *     不影响消息列表，原样返回入参数组引用。
 *
 * @param {Array<object>} messages 当前消息数组
 * @param {{
 *   type: string,
 *   content?: string,
 *   message_id?: string,
 *   session_id?: string,
 *   suggestions?: Array,
 *   tokens?: number,
 * }} event SSE 事件对象
 * @param {{placeholderId: string}} ctx 归约上下文（当前流式占位消息 id）
 * @returns {Array<object>} 新消息数组；无需变更时返回原数组引用
 */
export function reduceAssistantMessages(messages, event, ctx) {
  const { placeholderId } = ctx;
  switch (event.type) {
    case "token": {
      // 单趟完成定位、读取已累积内容与重建：token 为最高频事件，
      // 原实现 find 定位 + map 重建两趟遍历（js-combine-iterations）；
      // 占位不存在时不产生写入，返回原数组引用
      let hit = false;
      const next = messages.map((m) => {
        if (m.id !== placeholderId) return m;
        hit = true;
        return { ...m, content: (m.content || "") + event.content };
      });
      return hit ? next : messages;
    }
    case "round_start":
      // 新一轮工具轮次：重置占位消息内容，与后端落盘内容保持一致
      return patchPlaceholderInList(messages, placeholderId, {
        content: "",
      });
    case "done":
      // captcha_pending 终流形态（阶段五a DDG 挑战）：后端未持久化
      // assistant 消息（挑战分支在落盘前 return），删占位避免"幽灵"空
      // 回复残留（镜像 error 分支与 Writing Chat sse.js 同语义）
      if (event.captcha_pending) {
        return messages.filter((m) => m.id !== placeholderId);
      }
      // patch 用旧 id 定位；服务端 id 缺省回落占位 id
      return patchPlaceholderInList(messages, placeholderId, {
        id: event.message_id || placeholderId,
        streaming: false,
        suggestions: Array.isArray(event.suggestions) ? event.suggestions : [],
        tokens: event.tokens || 0,
      });
    case "error":
      // 失败回复不残留"幽灵"占位，仅移除占位本身
      return messages.filter((m) => m.id !== placeholderId);
    default:
      return messages;
  }
}

/**
 * 归约 SSE 事件对工具调用指示器（editorStore.assistantToolStatus，
 * 独立于消息列表的 store 字段）的影响：
 *   - tool_status：指示器置为整个事件对象（面板据此渲染工具名/状态）；
 *   - tool_result / done / captcha_challenge：工具轮次结束、整轮流收尾
 *     或 DDG 挑战接管的流暂停态，指示器清空为 null（滞留会挂僵尸
 *     "工具执行中"指示器，对齐 Writing Chat sse.js 先例）；
 *   - 其他事件：维持原值。
 *
 * @param {object|null} toolStatus 当前指示器值
 * @param {{type: string}} event SSE 事件对象
 * @returns {object|null} 新指示器值（无变化时返回原值）
 */
export function reduceAssistantToolStatus(toolStatus, event) {
  switch (event.type) {
    case "tool_status":
      return event;
    case "tool_result":
    case "done":
    case "captcha_challenge":
      return null;
    default:
      return toolStatus;
  }
}

/**
 * 归约 SSE 事件对 DDG 挑战状态（useWritingChatCaptcha 通道的
 * captchaChallenge state）的影响：仅 captcha_challenge 事件归一化产出
 * 挑战对象（空数组/空串兜底），其余事件维持原值。挑战的清除由
 * submitCaptcha / dismissCaptcha 负责（终流后挑战保持展示，与
 * Writing Chat 同语义）。
 *
 * @param {object|null} captchaChallenge 当前挑战对象
 * @param {{type: string}} event SSE 事件对象
 * @returns {object|null} 新挑战值（无变化时返回原值）
 */
export function reduceAssistantCaptcha(captchaChallenge, event) {
  if (event.type !== "captcha_challenge") return captchaChallenge;
  return {
    challenge_id: event.challenge_id,
    images: event.images || [],
    query: event.query || "",
    engine_name: event.engine_name || "",
    instruction: event.instruction || "",
  };
}

/**
 * 归约 SSE 事件对会话 id（editorStore.assistantSessionId）的影响：
 * 仅 done 事件携带服务端 scoped session_id 时更新，其余维持原值
 * （调用方须在消息 patch 之后应用，保持「替换服务端 id 后同步会话 id」
 * 的既有顺序）。
 *
 * @param {string|null} sessionId 当前会话 id
 * @param {{type: string, session_id?: string}} event SSE 事件对象
 * @returns {string|null} 新会话 id（无变化时返回原值）
 */
export function reduceAssistantSessionId(sessionId, event) {
  if (event.type === "done" && event.session_id) {
    return event.session_id;
  }
  return sessionId;
}
