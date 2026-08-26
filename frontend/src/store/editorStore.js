/**
 * 编辑器状态（editor-plan §9）：editorPreferences + editorSession 两个切片
 * 组合为一个独立 store，不进入全局 useStore——编辑器缓冲区是页面级临时态，
 * 与项目级持久化状态（latexcellence-project）生命周期不同。
 *
 * 持久化策略：
 *   - editorPreferences：persist 持久化（autoSave / softWrap / fontSize），
 *     水合后经 sanitizePreferences 白名单清洗，脏数据回落默认值；
 *   - editorSession（当前文件、缓冲区内容、baseMd5、脏标记、冲突信息）：
 *     不持久化——刷新即丢弃未保存缓冲区，避免与磁盘真实状态分叉。
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

import {
  EDITOR_PREFERENCES_DEFAULTS,
  sanitizePreferences,
} from "@/lib/editor/preferences";
import { api } from "@/api/index";

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
          set((state) => ({
            assistantMessages: state.assistantMessages.map((m) =>
              m.id === messageId
                ? { ...m, appliedSuggestions: authoritative }
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

/* ── editorSession 切片（不持久化） ───────────────────────────────────── */

function createEditorSessionSlice(set, get) {
  return {
    /** 当前打开的文件路径（项目相对路径；null 表示未打开文件） */
    filePath: null,
    /** 缓冲区全文（受控 value 的单一事实源） */
    content: "",
    /** 打开时服务端返回的 MD5（乐观锁基线） */
    baseMd5: null,
    /** 脏标记：缓冲区 ≠ baseMd5 对应的磁盘内容 */
    dirty: false,
    /** 409 冲突信息（非 null 时 ConflictDialog 打开） */
    conflict: null,

    /**
     * 打开文件成功后的缓冲区初始化。
     */
    openBuffer: ({ filePath, content, baseMd5 }) =>
      set({
        filePath,
        content,
        baseMd5,
        dirty: false,
        conflict: null,
      }),

    /** CM6 onChange 回写缓冲区并置脏标记 */
    setBufferContent: (content) =>
      set((state) => (state.filePath ? { content, dirty: true } : {})),

    /**
     * 保存成功：更新乐观锁基线并按保存快照重算脏标记。
     * @param {string} md5 服务端返回的新 MD5
     * @param {string} [savedContent] 发起保存时的缓冲区快照；在途期间若有
     *   新输入（content ≠ 快照），保持 dirty=true，避免"已保存"假象导致
     *   切换文件/关闭页面时静默丢弃未落盘编辑。
     */
    markSaved: (md5, savedContent) =>
      set((state) => ({
        baseMd5: md5,
        dirty:
          typeof savedContent === "string"
            ? state.content !== savedContent
            : false,
      })),

    /** 记录冲突（保存返回 409 时调用） */
    setConflict: (conflict) => set({ conflict }),

    /** 关闭缓冲区（切换文件失败回退 / 项目关闭时调用） */
    closeBuffer: () =>
      set({
        filePath: null,
        content: "",
        baseMd5: null,
        dirty: false,
        conflict: null,
      }),
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
