/**
 * 编辑器多缓冲区会话的 sessionStorage 持久化纯模块
 * （editor-productivity-plan §5.1 / 决策 Q14，阶段一）。
 *
 * 职责：
 *   1. 序列化 / 反序列化：所有打开 tab（含脏缓冲区全文 + baseMd5）与激活
 *      tab、项目隔离键一起写入 sessionStorage，刷新后整体恢复；
 *   2. 配额兜底：超出配额（约 5MB）时按 LRU 淘汰最久不活跃的**非脏** tab
 *      后重试；全为脏 tab 仍超限时返回 overflow，由调用方 toast 提示保存；
 *   3. 字段白名单清洗：恢复时逐条校验，脏数据 / 结构损坏整体放弃。
 *
 * 项目隔离键（projectKey：单用户 projectPath / 多用户 projectId）随载荷
 * 写入：sessionStorage 按 origin 共享，多用户模式下切换项目后不得把上一个
 * 项目的 tab 恢复进当前项目。cursor/scroll 不持久化（内存态即可）。
 *
 * 纯函数（storage 以参数注入），供 node --test 直接单测。
 */

export const EDITOR_SESSION_STORAGE_KEY = "latexcellence-editor-session";

/** sessionStorage 配额预算（约 5MB，预留余量后按字节衡量） */
export const EDITOR_SESSION_MAX_BYTES = 5 * 1024 * 1024;

const encoder = typeof TextEncoder !== "undefined" ? new TextEncoder() : null;

/**
 * @param {string} text
 * @returns {number} UTF-8 字节数
 */
export function measureBytes(text) {
  if (encoder) return encoder.encode(text).length;
  // TextEncoder 缺失时的保守回退（正常浏览器 / Node 18+ 不会走到）
  return text.length;
}

/**
 * 序列化会话为持久化载荷文本（紧凑 JSON）。
 * @param {{projectKey: string|null, tabs: Array, activeTabPath: string|null}} session
 * @returns {string}
 */
export function serializeSession({ projectKey, tabs, activeTabPath }) {
  return JSON.stringify({
    version: 1,
    projectKey: projectKey ?? null,
    activeTabPath: activeTabPath ?? null,
    tabs: tabs.map((tab) => ({
      filePath: tab.filePath,
      buffer: tab.buffer,
      baseMd5: tab.baseMd5 ?? null,
      dirty: tab.dirty,
      stale: tab.stale,
      lastActive: tab.lastActive ?? 0,
    })),
  });
}

/**
 * 解析并清洗持久化载荷：逐条白名单校验，非法条目丢弃；
 * activeTabPath 不在剩余集合中时回落首个 tab（仍无则 null）。
 * @param {string} text
 * @returns {{ok: true, projectKey: string|null, tabs: Array, activeTabPath: string|null}
 *           |{ok: false}}
 */
export function parseSession(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    return { ok: false };
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return { ok: false };
  }
  if (!Array.isArray(data.tabs)) {
    return { ok: false };
  }
  const tabs = [];
  for (const raw of data.tabs) {
    if (!raw || typeof raw !== "object") continue;
    if (typeof raw.filePath !== "string" || raw.filePath === "") continue;
    if (typeof raw.buffer !== "string") continue;
    tabs.push({
      filePath: raw.filePath,
      buffer: raw.buffer,
      baseMd5: typeof raw.baseMd5 === "string" ? raw.baseMd5 : null,
      dirty: raw.dirty === true,
      stale: raw.stale === true,
      lastActive: typeof raw.lastActive === "number" ? raw.lastActive : 0,
    });
  }
  const activeTabPath =
    typeof data.activeTabPath === "string" &&
    tabs.some((tab) => tab.filePath === data.activeTabPath)
      ? data.activeTabPath
      : (tabs[0]?.filePath ?? null);
  return {
    ok: true,
    projectKey: typeof data.projectKey === "string" ? data.projectKey : null,
    tabs,
    activeTabPath,
  };
}

/**
 * 配额归约：序列化后超限时按 LRU 淘汰最久不活跃的非脏 tab 重试。
 * 淘汰顺序：lastActive 升序；同为最久时按 tabs 顺序（稳定）。
 * 全为脏 tab 仍超限时终止并返回 overflow 标记（不淘汰任何脏 tab）。
 *
 * @param {Array} tabs 当前 tab 记录
 * @param {string|null} activeTabPath
 * @param {{projectKey?: string|null, limitBytes?: number}} [options]
 * @returns {{ok: boolean, tabs: Array, activeTabPath: string|null, evicted: string[], overflow: boolean}}
 *   ok：归约后载荷可写入；evicted：被淘汰的 filePath 列表；
 *   overflow：全为脏 tab 仍超限（调用方 toast 提示保存）
 */
export function fitSessionToQuota(
  tabs,
  activeTabPath,
  { projectKey = null, limitBytes = EDITOR_SESSION_MAX_BYTES } = {},
) {
  let current = tabs;
  let currentActive = activeTabPath;
  const evicted = [];
  for (;;) {
    const bytes = measureBytes(
      serializeSession({
        projectKey,
        tabs: current,
        activeTabPath: currentActive,
      }),
    );
    if (bytes <= limitBytes) {
      return {
        ok: true,
        tabs: current,
        activeTabPath: currentActive,
        evicted,
        overflow: false,
      };
    }
    // 最久不活跃的非脏 tab（激活 tab 通常是最近活跃，天然靠后）
    const candidates = current
      .map((tab, index) => ({ tab, index }))
      .filter(({ tab }) => !tab.dirty)
      .sort((a, b) => a.tab.lastActive - b.tab.lastActive || a.index - b.index);
    if (candidates.length === 0) {
      return {
        ok: false,
        tabs: current,
        activeTabPath: currentActive,
        evicted,
        overflow: true,
      };
    }
    const victim = candidates[0];
    current = current.filter((tab) => tab.filePath !== victim.tab.filePath);
    if (currentActive === victim.tab.filePath) {
      currentActive =
        current[Math.min(victim.index, current.length - 1)]?.filePath ?? null;
    }
    evicted.push(victim.tab.filePath);
  }
}

/**
 * 写入 sessionStorage（配额归约后落盘；归约后仍超限不写、返回 overflow）。
 * @param {Storage} storage 注入的 sessionStorage 形状对象
 * @param {{projectKey: string|null, tabs: Array, activeTabPath: string|null}} session
 * @param {{limitBytes?: number}} [options]
 * @returns {{ok: boolean, evicted: string[], overflow: boolean}}
 */
export function saveSession(storage, session, options = {}) {
  const fit = fitSessionToQuota(session.tabs, session.activeTabPath, {
    projectKey: session.projectKey,
    limitBytes: options.limitBytes,
  });
  if (!fit.ok) {
    return { ok: false, evicted: fit.evicted, overflow: true };
  }
  try {
    storage.setItem(
      EDITOR_SESSION_STORAGE_KEY,
      serializeSession({
        projectKey: session.projectKey,
        tabs: fit.tabs,
        activeTabPath: fit.activeTabPath,
      }),
    );
    return { ok: true, evicted: fit.evicted, overflow: false };
  } catch (_) {
    // 序列化已控量仍写入失败（宿主配额策略等）：放弃本轮持久化，
    // 不影响编辑器主流程（乐观锁在保存时兜底）
    return { ok: false, evicted: fit.evicted, overflow: false };
  }
}

/**
 * 读取并恢复会话：项目键不匹配 / 载荷损坏一律放弃（返回 ok:false），
 * 不影响当前项目状态。
 * @param {Storage} storage 注入的 sessionStorage 形状对象
 * @param {string|null} projectKey 当前项目隔离键
 * @returns {{ok: boolean, reason?: "missing"|"corrupt"|"project_mismatch", tabs?: Array, activeTabPath?: string|null}}
 */
export function loadSession(storage, projectKey) {
  let text;
  try {
    text = storage.getItem(EDITOR_SESSION_STORAGE_KEY);
  } catch (_) {
    return { ok: false, reason: "missing" };
  }
  if (!text) return { ok: false, reason: "missing" };
  const parsed = parseSession(text);
  if (!parsed.ok) return { ok: false, reason: "corrupt" };
  if ((parsed.projectKey ?? null) !== (projectKey ?? null)) {
    return { ok: false, reason: "project_mismatch" };
  }
  return {
    ok: true,
    tabs: parsed.tabs,
    activeTabPath: parsed.activeTabPath,
  };
}

/**
 * 清除持久化会话（项目切换 / 全部关闭后调用，避免残留载荷跨项目误恢复）。
 * @param {Storage} storage
 */
export function clearSession(storage) {
  try {
    storage.removeItem(EDITOR_SESSION_STORAGE_KEY);
  } catch (_) {
    // 忽略宿主异常
  }
}
