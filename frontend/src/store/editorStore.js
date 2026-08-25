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
    /** 打开/保存进行中 */
    busy: false,
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
        busy: false,
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
        busy: false,
      })),

    /** 记录冲突（保存返回 409 时调用） */
    setConflict: (conflict) => set({ conflict, busy: false }),

    /** 关闭缓冲区（切换文件失败回退 / 项目关闭时调用） */
    closeBuffer: () =>
      set({
        filePath: null,
        content: "",
        baseMd5: null,
        dirty: false,
        conflict: null,
        busy: false,
      }),

    setBusy: (busy) => set({ busy: !!busy }),
  };
}

export const useEditorStore = create(
  persist(
    (set, get) => ({
      ...createEditorPreferencesSlice(set),
      ...createEditorSessionSlice(set, get),
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
