/**
 * 编辑器状态（editor-plan §9 + editor-productivity-plan §5.1）：
 * editorPreferences + editorSession 两个切片组合为一个独立 store，不进入
 * 全局 useStore——编辑器缓冲区是页面级临时态，与项目级持久化状态
 * （latexcellence-project）生命周期不同。
 *
 * 持久化策略：
 *   - editorPreferences：persist 持久化（autoSave / softWrap / fontSize），
 *     水合后经 sanitizePreferences 白名单清洗，脏数据回落默认值；
 *   - editorSession（多缓冲区：tabs + activeTabPath + 冲突信息）：不进
 *     localStorage——刷新恢复走 sessionStorage 专项通道（含脏缓冲区全文，
 *     LRU 配额兜底，见 lib/editor/sessionPersistence.js），由
 *     useEditorBuffer 负责写入与恢复，避免与磁盘真实状态分叉。
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

import {
  EDITOR_PREFERENCES_DEFAULTS,
  sanitizePreferences,
} from "@/lib/editor/preferences";
import {
  activateTabReducer,
  activeBaseMd5,
  activeBuffer,
  activeDirty,
  activeFilePath,
  closeTabReducer,
  countDirtyTabs,
  findTab,
  getActiveTab,
  getDirtyTabsInOrder,
  getTabBuffer,
  markTabSavedReducer,
  openTabReducer,
  resetSessionReducer,
  setTabBufferReducer,
  setTabStaleReducer,
} from "@/lib/editor/tabs";
import { api } from "@/api/index";

export {
  activeBaseMd5,
  activeBuffer,
  activeDirty,
  activeFilePath,
  findTab,
  getActiveTab,
  getTabBuffer,
  countDirtyTabs,
  getDirtyTabsInOrder,
};

/* ── editorPreferences 切片（persist 持久化） ─────────────────────────── */

/**
 * @param {(partial: Partial<EditorStoreState>) => void} set zustand setState
 */
function createEditorPreferencesSlice(set) {
  return {
    ...EDITOR_PREFERENCES_DEFAULTS,
    setAutoSave: (value) => set({ autoSave: !!value }),
    setSoftWrap: (value) => set({ softWrap: !!value }),
    // 字号经白名单校验（区间整数），非法值静默忽略
    setFontSize: (value) => {
      const cleaned = sanitizePreferences({ fontSize: value });
      set({ fontSize: cleaned.fontSize });
    },
    /** 整体覆盖偏好切片（阶段四导入通道复用；本阶段仅水合 migrate 使用） */
    applyPreferences: (partial) => set(sanitizePreferences(partial)),
  };
}

/* ── editorAssistant 切片（不持久化，阶段四） ─────────────────────────── */

/**
 * 服务端历史消息 → 本地运行时结构的映射（R8 历史加载点）：
 * 会话消息注入 store 的唯一入口是 appendAssistantMessage，在此完成归一化——
 * - 新数据：applied_suggestions（已应用建议 index 数组）→ appliedSuggestions map；
 * - 旧数据：仅有消息级 suggestionApplied=true 而无数组时，视为全部建议已应用，
 *   避免历史卡片闪回可编辑态。
 * 运行时"是否已应用"的唯一来源是消息上的 appliedSuggestions map。
 */
function normalizeAssistantMessage(message) {
  if (!message || typeof message !== "object") return message;
  const next = { ...message };
  if (Array.isArray(next.applied_suggestions)) {
    next.appliedSuggestions = Object.fromEntries(
      next.applied_suggestions.map((index) => [index, true]),
    );
  } else if (next.suggestionApplied === true) {
    const suggestions = Array.isArray(next.suggestions) ? next.suggestions : [];
    next.appliedSuggestions = Object.fromEntries(
      suggestions
        .filter((s) => s && typeof s.index === "number")
        .map((s) => [s.index, true]),
    );
  }
  delete next.applied_suggestions;
  delete next.suggestionApplied;
  return next;
}

/**
 * 编辑器 AI 助手会话状态（editor-plan §7）：按项目 scope 隔离由后端保证，
 * 前端只保存当前项目的活跃会话 id 与消息列表。切换文件时会话延续（§7.4）；
 * 切换项目时经 setAssistantProjectKey 检测清空。刷新即丢弃（不持久化），
 * 会话历史仍可通过后端 session id 找回。
 */
function createEditorAssistantSlice(set, get) {
  return {
    /** 后端 scoped session_id（{scope}:{uuid}）；null 表示尚未开始对话 */
    assistantSessionId: null,
    /** 项目隔离键：单用户为 projectPath，多用户为 projectId */
    assistantProjectKey: null,
    /** 消息列表：{id, role, content, streaming, suggestions, appliedSuggestions} */
    assistantMessages: [],
    /** SSE 流式接收中 */
    assistantStreaming: false,
    /** 工具调用指示器（tool_status 事件；null 表示空闲） */
    assistantToolStatus: null,

    setAssistantSessionId: (sessionId) =>
      set({ assistantSessionId: sessionId }),
    /** 注入消息；服务端历史消息在此完成 applied 状态归一化 */
    appendAssistantMessage: (message) =>
      set((state) => ({
        assistantMessages: [
          ...state.assistantMessages,
          normalizeAssistantMessage(message),
        ],
      })),
    patchAssistantMessage: (messageId, patch) =>
      set((state) => ({
        assistantMessages: state.assistantMessages.map((m) =>
          m.id === messageId ? { ...m, ...patch } : m,
        ),
      })),
    /**
     * 标记某条建议已应用：乐观写 appliedSuggestions[index]，随后上报后端
     * （携带 suggestion_index），并用响应中的 applied_suggestions 全量数组
     * 回写权威状态（不再维护消息级 suggestionApplied 标记）。
     * 上报失败不影响已应用的缓冲区修改。
     * @param {string} messageId 消息 id（tmp- 占位不上报）
     * @param {number} index 建议 index
     * @param {{workspaceId?: string}} [options]
     */
    markSuggestionApplied: async (messageId, index, { workspaceId } = {}) => {
      set((state) => ({
        assistantMessages: state.assistantMessages.map((m) =>
          m.id === messageId
            ? {
                ...m,
                appliedSuggestions: {
                  ...(m.appliedSuggestions || {}),
                  [index]: true,
                },
              }
            : m,
        ),
      }));
      const sessionId = get().assistantSessionId;
      if (!sessionId || !messageId || messageId.startsWith("tmp-")) return;
      try {
        const res = await api.writingChat.applyEditorSuggestion(
          sessionId,
          messageId,
          workspaceId,
          index,
        );
        if (Array.isArray(res?.applied_suggestions)) {
          const authoritative = Object.fromEntries(
            res.applied_suggestions.map((i) => [i, true]),
          );
          // 并集合并而非整体替换：同一消息两条建议被快速连点时，先完成的
          // 上报响应快照可能尚未包含第二条，整体替换会把第二条的乐观标记
          // 抹掉（服务端实际已记录）；建议无"取消应用"语义，并集安全
          set((state) => ({
            assistantMessages: state.assistantMessages.map((m) =>
              m.id === messageId
                ? {
                    ...m,
                    appliedSuggestions: {
                      ...(m.appliedSuggestions || {}),
                      ...authoritative,
                    },
                  }
                : m,
            ),
          }));
        }
      } catch (_) {
        // 标记上报失败不影响已应用的缓冲区修改（保留乐观标记）
      }
    },
    removeAssistantMessage: (messageId) =>
      set((state) => ({
        assistantMessages: state.assistantMessages.filter(
          (m) => m.id !== messageId,
        ),
      })),
    setAssistantStreaming: (streaming) =>
      set({ assistantStreaming: !!streaming }),
    setAssistantToolStatus: (status) => set({ assistantToolStatus: status }),
    /**
     * 项目切换守卫：项目键与当前不一致时清空本地会话视图。
     * @returns {boolean} 是否发生了重置
     */
    ensureAssistantProject: (projectKey) =>
      set((state) => {
        if (!projectKey || state.assistantProjectKey === projectKey) {
          return {};
        }
        return {
          assistantProjectKey: projectKey,
          assistantSessionId: null,
          assistantMessages: [],
          assistantStreaming: false,
          assistantToolStatus: null,
        };
      }),
    clearAssistantConversation: () =>
      set({
        assistantSessionId: null,
        assistantMessages: [],
        assistantStreaming: false,
        assistantToolStatus: null,
      }),
  };
}

/* ── editorSession 切片（不持久化；刷新恢复走 sessionStorage 专项通道） ── */

/**
 * 多缓冲区会话（editor-productivity-plan §5.1，阶段一）：
 *   tabs: [{ filePath, buffer, baseMd5, dirty, stale, lastActive }]
 *   activeTabPath: 激活 tab（键 = 项目相对路径）
 *   conflict: 409 冲突信息 { filePath, message }（含所属文件，弹窗动作
 *   始终作用于冲突归属 tab，不随激活切换漂移）
 *
 * tab 转移逻辑（打开 / 激活 / 关闭 / 保存回写等）全部委托
 * lib/editor/tabs.js 的不可变归约；cursor/selection 随 CM6 EditorState
 * 隔离（tabViews 登记表），不进入本切片。
 */
function createEditorSessionSlice(set, get) {
  /** 单调时刻源：LRU lastActive 依据（测试外恒递增） */
  const now = () => Date.now();

  return {
    tabs: [],
    activeTabPath: null,
    conflict: null,

    /**
     * 打开 tab：不存在则新建（携带服务端内容），已存在视为显式重拉
     * （冲突放弃重载 / 后端撤销刷新）：以磁盘内容覆盖缓冲区并复位脏 /
     * 陈旧标记，随后激活。
     */
    openTab: ({ filePath, content, baseMd5 }) =>
      set((state) => ({
        ...openTabReducer(state, {
          filePath,
          buffer: content,
          baseMd5,
          now: now(),
        }),
        conflict: state.conflict?.filePath === filePath ? null : state.conflict,
      })),

    /** 激活 tab（文件树点击已打开的文件 / URL ?file= / 关闭后邻居接管） */
    activateTab: (filePath) =>
      set((state) => activateTabReducer(state, filePath, now())),

    /** CM6 onChange 回写激活 tab 的缓冲区并置脏标记 */
    setBufferContent: (content) =>
      set((state) => setTabBufferReducer(state, content, now())),

    /**
     * 保存成功：更新该 tab 的乐观锁基线、清除陈旧标记，并按保存快照重算
     * 脏标记（在途期间有新输入则保持 dirty）。
     * @param {string} filePath 保存归属 tab（保存期间允许切换激活 tab）
     * @param {string} md5 服务端返回的新 MD5
     * @param {string} [savedBuffer] 发起保存时的缓冲区快照
     */
    markTabSaved: (filePath, md5, savedBuffer) =>
      set((state) =>
        markTabSavedReducer(state, { filePath, md5, savedBuffer }),
      ),

    /** 陈旧检测回写（激活 tab 惰性 file-md5 比对结果） */
    setTabStale: (filePath, stale) =>
      set((state) => setTabStaleReducer(state, filePath, stale)),

    /** 记录冲突（保存返回 409 时调用；message 已脱敏为相对路径） */
    setConflict: (conflict) => set({ conflict }),

    /** 关闭单个 tab；关闭激活 tab 时邻居接管激活位 */
    closeTab: (filePath) => set((state) => closeTabReducer(state, filePath)),

    /**
     * 整体恢复会话（sessionStorage 刷新恢复专用入口，Q14）：载荷已经
     * lib/editor/sessionPersistence.parseSession 白名单清洗，此处原样
     * 写回（含脏缓冲区全文、baseMd5 与陈旧标记）。
     */
    restoreSession: ({ tabs, activeTabPath }) =>
      set({ tabs, activeTabPath: activeTabPath ?? null, conflict: null }),

    /** 关闭全部 tab / 会话复位（项目切换守卫、恢复失败兜底） */
    resetSession: () =>
      set((state) => ({ ...resetSessionReducer(), conflict: null })),
  };
}

export const useEditorStore = create(
  persist(
    (set, get) => ({
      ...createEditorPreferencesSlice(set),
      ...createEditorSessionSlice(set, get),
      ...createEditorAssistantSlice(set, get),
    }),
    {
      name: "latexcellence-editor",
      version: 1,
      // 仅偏好进 localStorage；会话切片（含缓冲区内容）显式排除
      partialize: (state) => ({
        autoSave: state.autoSave,
        softWrap: state.softWrap,
        fontSize: state.fontSize,
      }),
      merge: (persisted, current) => ({
        ...current,
        ...sanitizePreferences(persisted),
      }),
    },
  ),
);

export default useEditorStore;
